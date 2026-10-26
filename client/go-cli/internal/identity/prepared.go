package identity

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/sha256"
	"crypto/x509"
	"encoding/binary"
	"encoding/json"
	"errors"
	"io"
	"os"
	"strings"
	"time"
	"unicode/utf8"
)

const preparedTag = "arbiter-enrollment-prepared-v1\x00"
const readyTag = "arbiter-enrollment-ready-v1\x00"
const maxPreparedPrivateBytes = MaxProvisionalBytes + 2*MaxPairingFrame

type preparedPrivateRecord struct {
	Warning     string          `json:"warning"`
	Version     int             `json:"version"`
	Provisional json.RawMessage `json:"provisional"`
	Result      []byte          `json:"result"`
}

// PreparedEnrollment is uncommitted. Nothing in this type authorizes API work.
type PreparedEnrollment struct {
	ServerID, OwnerID, AgentID, EnrollmentID string
	ExpiresAt                                time.Time
	Grants                                   [][2]string
	raw                                      []byte
}

func preparedField(data *[]byte) ([]byte, error) {
	if len(*data) < 2 {
		return nil, errPairing
	}
	n := int(binary.BigEndian.Uint16(*data))
	if n == 0 || len(*data) < n+2 {
		return nil, errPairing
	}
	value := (*data)[2 : n+2]
	*data = (*data)[n+2:]
	return value, nil
}

func verifyPrepared(raw []byte, material *Provisional, server *PairingServer, now time.Time) (*PreparedEnrollment, error) {
	return verifyPreparedResult(raw, material, server, now, false)
}

func verifyHistoricalServerCredential(der []byte, expected Identity, issuer *Issuer, now time.Time) error {
	cert, err := x509.ParseCertificate(der)
	if err != nil || !bytes.Equal(cert.Raw, der) || now.Before(cert.NotBefore) {
		return errPairing
	}
	// Receipt loading authenticates the current peer separately over TLS. The
	// embedded server leaf is historical result material, so verify its full
	// profile and chain at the leaf's issuance boundary instead of imposing a
	// second retrieval deadline on the unchanged result.
	_, err = VerifyCredential(der, expected, issuer, cert.NotBefore)
	return err
}

func verifyPreparedResult(raw []byte, material *Provisional, server *PairingServer, now time.Time, receipt bool) (*PreparedEnrollment, error) {
	const fixed = len(preparedTag) + 144 + 22 + 32 + 32 + 8
	if len(raw) < fixed || len(raw) > MaxPairingFrame-16 || !bytes.HasPrefix(raw, []byte(preparedTag)) || material == nil || server == nil {
		return nil, errPairing
	}
	data := raw[len(preparedTag):]
	ids := make([]string, 4)
	seen := map[string]bool{}
	for i := range ids {
		ids[i], data = string(data[:36]), data[36:]
		if !uuid4.MatchString(ids[i]) || seen[ids[i]] {
			return nil, errPairing
		}
		seen[ids[i]] = true
	}
	public, err := material.Public()
	if err != nil {
		return nil, err
	}
	advertisement, err := json.Marshal(public)
	digest := sha256.Sum256(advertisement)
	if err != nil || ids[0] != server.ServerID || string(data[:22]) != public.Selector || !bytes.Equal(data[22:54], digest[:]) {
		return nil, errPairing
	}
	for _, c := range data[54:86] {
		if !(c >= '0' && c <= '9' || c >= 'a' && c <= 'f') {
			return nil, errPairing
		}
	}
	expires := binary.BigEndian.Uint64(data[86:94])
	if expires == 0 || expires > uint64(now.Add(10*time.Minute).Unix()) || !receipt && expires <= uint64(now.Unix()) {
		return nil, errPairing
	}
	data = data[94:]
	fields := make([][]byte, 9)
	for i := range fields {
		fields[i], err = preparedField(&data)
		if err != nil {
			return nil, err
		}
	}
	if len(data) != 0 || !bytes.Equal(fields[0], server.AuthorityDER) || !bytes.Equal(fields[1], server.CertificateDER) || !bytes.Equal(fields[5], public.RecipientKey) {
		return nil, errPairing
	}
	var serverErr error
	if receipt {
		serverErr = verifyHistoricalServerCredential(fields[1], Identity{Role: ServerTLS, SubjectID: ids[0]}, &Issuer{DER: fields[0], ID: ids[0]}, now)
	} else {
		_, serverErr = VerifyCredential(fields[1], Identity{Role: ServerTLS, SubjectID: ids[0]}, &Issuer{DER: fields[0], ID: ids[0]}, now)
	}
	if serverErr != nil {
		return nil, serverErr
	}
	if _, err := VerifyCredential(fields[2], Identity{Role: Owner, SubjectID: ids[1]}, nil, now); err != nil {
		return nil, err
	}
	for _, item := range []struct {
		index    int
		identity Identity
		issuer   Issuer
		key      *ecdsa.PublicKey
	}{
		{3, Identity{Role: Portable, SubjectID: ids[2]}, Issuer{DER: fields[2], ID: ids[1]}, &material.portable.PublicKey},
		{4, Identity{Role: Access, SubjectID: ids[2], ServerID: ids[0], EnrollmentID: ids[3]}, Issuer{DER: fields[0], ID: ids[0]}, &material.access.PublicKey},
	} {
		cert, err := VerifyCredential(fields[item.index], item.identity, &item.issuer, now)
		if err != nil || !samePublicKey(cert.PublicKey, item.key) {
			return nil, errPairing
		}
	}
	var recovery *ecdsa.PublicKey
	for _, der := range fields[6:8] {
		key, err := x509.ParsePKIXPublicKey(der)
		k, ok := key.(*ecdsa.PublicKey)
		canonical, encodingErr := x509.MarshalPKIXPublicKey(key)
		if err != nil || !ok || k.Curve != elliptic.P256() || encodingErr != nil || !bytes.Equal(canonical, der) || recovery != nil && samePublicKey(recovery, k) {
			return nil, errPairing
		}
		recovery = k
	}
	// Decode arrays without accepting shortened/overlong account pairs.
	var grants [][]string
	if !utf8.Valid(fields[8]) || json.Unmarshal(fields[8], &grants) != nil || grants == nil {
		return nil, errPairing
	}
	result := &PreparedEnrollment{ServerID: ids[0], OwnerID: ids[1], AgentID: ids[2], EnrollmentID: ids[3], ExpiresAt: time.Unix(int64(expires), 0), raw: bytes.Clone(raw)}
	var last []string
	for _, pair := range grants {
		if len(pair) != 2 || pair[0] == "" || pair[1] == "" {
			return nil, errPairing
		}
		if last != nil && (pair[0] < last[0] || pair[0] == last[0] && pair[1] <= last[1]) {
			return nil, errPairing
		}
		last = pair
		result.Grants = append(result.Grants, [2]string{pair[0], pair[1]})
	}
	return result, nil
}

// SaveReady flushes keys, exact result and public trust in one immutable private
// record before creating a channel-bound ready proof. It never promotes access.
func (p *PairingInitiator) SaveReady(ctx context.Context, directory string, material *Provisional, raw []byte, now time.Time) ([]byte, error) {
	if p == nil || p.pairingState == nil {
		return nil, errPairing
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.phase != 2 || !p.proofSent {
		return nil, p.fail()
	}
	public, err := material.Public()
	ad, encodeErr := json.Marshal(public)
	if err != nil || encodeErr != nil || !bytes.Equal(ad, p.advertisement) {
		return nil, p.fail()
	}
	prepared, err := verifyPrepared(raw, material, p.serverTrust, now)
	if err != nil {
		return nil, p.fail()
	}
	private, err := material.EncodePrivate()
	if err != nil {
		return nil, p.fail()
	}
	bundle, err := json.Marshal(preparedPrivateRecord{privateWarning, 1, private, raw})
	if err != nil || len(bundle) > maxPreparedPrivateBytes {
		return nil, p.fail()
	}
	if err := savePrivateRecord(ctx, directory, material.record.Selector, bundle, func(string) error { return nil }); err != nil {
		p.fail()
		return nil, err
	}
	if err := ctx.Err(); err != nil {
		p.fail()
		return nil, err
	}
	if !prepared.ExpiresAt.After(time.Now().UTC()) {
		return nil, p.fail()
	}
	h := sha256.New()
	h.Write([]byte(readyTag))
	h.Write(p.hash)
	prologueHash, resultHash := sha256.Sum256(p.prologue), sha256.Sum256(raw)
	h.Write(prologueHash[:])
	h.Write(resultHash[:])
	signature, err := proofSignature(material.access, h.Sum(nil), "ready")
	if err != nil {
		return nil, p.fail()
	}
	proof := append([]byte(readyTag), resultHash[:]...)
	return p.encryptLocked(append(proof, signature...))
}

// LoadPrepared validates complete stored material without making it enrolled.
func LoadPrepared(directory, selector string, now time.Time) (*Provisional, *PreparedEnrollment, error) {
	return loadPrepared(directory, selector, now, false)
}

func loadPrepared(directory, selector string, now time.Time, receipt bool) (*Provisional, *PreparedEnrollment, error) {
	return loadPreparedLinked(directory, selector, now, receipt, 1)
}

func loadPreparedLinked(directory, selector string, now time.Time, receipt bool, links uint32) (*Provisional, *PreparedEnrollment, error) {
	root, dir, err := custodyRoot(directory, false)
	if err != nil {
		return nil, nil, err
	}
	defer dir.Close()
	final, _, err := custodyNames(root, selector)
	if err != nil {
		return nil, nil, err
	}
	f, err := nativeOpenCustody(final, false, false)
	if err != nil {
		return nil, nil, err
	}
	defer f.Close()
	if err := checkBoundCustody(f, false, false, links); err != nil {
		return nil, nil, err
	}
	bundle, err := io.ReadAll(io.LimitReader(f, int64(maxPreparedPrivateBytes+1)))
	if err != nil || len(bundle) > maxPreparedPrivateBytes || !utf8.Valid(bundle) {
		return nil, nil, errCustody
	}
	if err := checkBoundCustody(f, false, false, links); err != nil {
		return nil, nil, err
	}
	var record preparedPrivateRecord
	if json.Unmarshal(bundle, &record) != nil || record.Warning != privateWarning || record.Version != 1 {
		return nil, nil, errCustody
	}
	// Only this writer's canonical representation is accepted. Duplicate,
	// unknown, differently cased and trailing fields cannot survive readback.
	canonical, err := json.Marshal(record)
	if err != nil || !bytes.Equal(canonical, bundle) {
		return nil, nil, errCustody
	}
	material, err := DecodeProvisional(record.Provisional)
	if err != nil || material.record.Selector != selector {
		return nil, nil, errCustody
	}
	raw := record.Result
	const fixed = len(preparedTag) + 144 + 22 + 32 + 32 + 8
	if len(raw) < fixed {
		return nil, nil, errCustody
	}
	fields := raw[fixed:]
	authority, err := preparedField(&fields)
	if err != nil {
		return nil, nil, err
	}
	leaf, err := preparedField(&fields)
	if err != nil {
		return nil, nil, err
	}
	server := &PairingServer{ServerID: string(raw[len(preparedTag) : len(preparedTag)+36]), AuthorityDER: authority, CertificateDER: leaf}
	prepared, err := verifyPreparedResult(raw, material, server, now, receipt)
	return material, prepared, err
}

// CleanupPrepared uses the same bounded cleanup/ownership contract as provisional
// custody. Starting a new pairing abandons uncommitted results, not enrolled state.
// Later receipt reconciliation must run before explicitly starting a new pairing.
// The caller must hold its RunProvisional enrollment owner throughout the exchange.
func CleanupPrepared(ctx context.Context, directory string) (result error) {
	root, lock, err := lockProvisional(ctx, directory)
	if err != nil {
		return err
	}
	defer func() { result = errors.Join(result, lock.Close()) }()
	dir, err := os.Open(root)
	if err != nil {
		return err
	}
	defer dir.Close()
	if err := checkBoundCustody(dir, true, false, 0); err != nil {
		return err
	}
	names, err := dir.Readdirnames(provisionalCleanupBatch + 1)
	if err != nil && !errors.Is(err, io.EOF) {
		return err
	}
	more := len(names) > provisionalCleanupBatch
	if more {
		names = names[:provisionalCleanupBatch]
	}
	removed := false
	for _, name := range names {
		if err := ctx.Err(); err != nil {
			return err
		}
		var selector string
		switch {
		case strings.HasSuffix(name, ".json"):
			selector = strings.TrimSuffix(name, ".json")
		case strings.HasPrefix(name, ".") && strings.HasSuffix(name, ".pending"):
			selector = strings.TrimSuffix(strings.TrimPrefix(name, "."), ".pending")
		default:
			continue
		}
		if _, _, err := custodyNames(root, selector); err != nil {
			continue
		}
		// Records are an explicitly separate, uncommitted domain. The selected
		// request is abandoned even if its lifetime has not elapsed.
		if err := RemoveProvisional(root, selector); err != nil {
			return err
		}
		removed = true
	}
	if more {
		if !removed {
			return ErrProvisionalUnexpectedContents
		}
		return ErrProvisionalCleanupIncomplete
	}
	return nil
}
