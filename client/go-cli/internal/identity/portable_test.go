package identity

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/asn1"
	"encoding/binary"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math/big"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"
)

func portableFixture(t *testing.T, portableAgo, accessAgo time.Duration) (*Provisional, []byte, time.Time) {
	t.Helper()
	now := time.Now().UTC().Truncate(time.Second)
	p, _, err := NewProvisional("https://source.invalid", "source", now)
	if err != nil {
		t.Fatal(err)
	}
	ids := []string{"10000000-0000-4000-8000-000000000001", "20000000-0000-4000-8000-000000000002", "30000000-0000-4000-8000-000000000003", "40000000-0000-4000-8000-000000000004"}
	issue := func(id Identity, key *ecdsa.PrivateKey, parent *x509.Certificate, parentKey *ecdsa.PrivateKey, at time.Time) []byte {
		names, e := id.names()
		if e != nil {
			t.Fatal(e)
		}
		sanValue, _ := asn1.Marshal(names)
		c := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: string(id.Role)}, NotBefore: at.Add(-time.Minute), NotAfter: at.Add(365 * 24 * time.Hour), BasicConstraintsValid: true, KeyUsage: x509.KeyUsageDigitalSignature, ExtraExtensions: []pkix.Extension{{Id: san, Critical: true, Value: sanValue}}}
		if id.ca() {
			c.IsCA = true
			c.MaxPathLenZero = true
			c.KeyUsage = x509.KeyUsageCertSign
			c.NotAfter = at.Add(10 * 365 * 24 * time.Hour)
			parent = c
			parentKey = key
		} else {
			purpose := asn1.ObjectIdentifier{1, 3, 6, 1, 5, 5, 7, 3, 2}
			if id.Role == ServerTLS {
				purpose[8] = 1
			}
			value, _ := asn1.Marshal([]asn1.ObjectIdentifier{purpose})
			c.ExtraExtensions = append(c.ExtraExtensions, pkix.Extension{Id: eku, Critical: true, Value: value})
		}
		der, e := x509.CreateCertificate(rand.Reader, c, parent, key.Public(), parentKey)
		if e != nil {
			t.Fatal(e)
		}
		return der
	}
	keys := make([]*ecdsa.PrivateKey, 3)
	for i := range keys {
		keys[i], err = ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
		if err != nil {
			t.Fatal(err)
		}
	}
	at := now.Add(-2 * 365 * 24 * time.Hour)
	serverDER := issue(Identity{Role: ServerAuthority, SubjectID: ids[0]}, keys[0], nil, nil, at)
	server, _ := x509.ParseCertificate(serverDER)
	ownerDER := issue(Identity{Role: Owner, SubjectID: ids[1]}, keys[1], nil, nil, at)
	owner, _ := x509.ParseCertificate(ownerDER)
	tlsDER := issue(Identity{Role: ServerTLS, SubjectID: ids[0]}, keys[2], server, keys[0], now)
	portableDER := issue(Identity{Role: Portable, SubjectID: ids[2]}, p.portable, owner, keys[1], now.Add(-portableAgo))
	accessDER := issue(Identity{Role: Access, SubjectID: ids[2], ServerID: ids[0], EnrollmentID: ids[3]}, p.access, server, keys[0], now.Add(-accessAgo))
	public, _ := p.Public()
	ad, _ := json.Marshal(public)
	hash := sha256.Sum256(ad)
	raw := []byte(preparedTag + strings.Join(ids, "") + p.record.Selector)
	raw = append(raw, hash[:]...)
	raw = append(raw, []byte(strings.Repeat("a", 32))...)
	raw = binary.BigEndian.AppendUint64(raw, uint64(now.Add(300*time.Second).Unix()))
	recovery, _ := x509.MarshalPKIXPublicKey(keys[1].Public())
	origin, _ := x509.MarshalPKIXPublicKey(keys[2].Public())
	for _, field := range [][]byte{serverDER, tlsDER, ownerDER, portableDER, accessDER, public.RecipientKey, recovery, origin, []byte("[]")} {
		raw = binary.BigEndian.AppendUint16(raw, uint16(len(field)))
		raw = append(raw, field...)
	}
	return p, raw, now
}

func savePortableFixture(t *testing.T, p *Provisional, raw []byte, root, domain string) []byte {
	t.Helper()
	private, err := p.EncodePrivate()
	if err != nil {
		t.Fatal(err)
	}
	bundle, err := json.Marshal(preparedPrivateRecord{privateWarning, 1, private, raw})
	if err != nil {
		t.Fatal(err)
	}
	if err = savePrivateRecord(context.Background(), filepath.Join(root, domain), p.record.Selector, bundle, func(string) error { return nil }); err != nil {
		t.Fatal(err)
	}
	return bundle
}

func TestPortableSourcePurposeAndCustody(t *testing.T) {
	for _, mode := range []string{"current", "expired-access", "expired-portable", "future-portable"} {
		t.Run(mode, func(t *testing.T) {
			portableAgo, accessAgo := time.Duration(0), time.Duration(0)
			if mode == "expired-access" {
				accessAgo = 366 * 24 * time.Hour
			}
			if mode == "expired-portable" {
				portableAgo = 366 * 24 * time.Hour
			}
			if mode == "future-portable" {
				portableAgo = -time.Hour
			}
			p, raw, now := portableFixture(t, portableAgo, accessAgo)
			root := custodyTestDir(t)
			before := savePortableFixture(t, p, raw, root, "enrolled")
			path := filepath.Join(root, "enrolled", p.record.Selector+".json")
			source, err := LoadPortableIdentity(root, p.record.Selector, now)
			want := mode == "current" || mode == "expired-access"
			if (err == nil) != want {
				t.Fatal("source purpose", mode, err)
			}
			_, _, receiptErr := loadPrepared(filepath.Join(root, "enrolled"), p.record.Selector, now, true)
			if (receiptErr == nil) != (mode == "current" || mode == "expired-portable") {
				t.Fatal("receipt purpose", mode, receiptErr)
			}
			after, e := os.ReadFile(path)
			if e != nil || !bytes.Equal(before, after) {
				t.Fatal("source changed", e)
			}
			if !want {
				return
			}
			if _, e := json.Marshal(source); e == nil {
				t.Fatal("private source serialized")
			}
			if !strings.Contains(fmt.Sprintf("%#v", source), "redacted") {
				t.Fatal("private source formatted")
			}
			target, _, e := NewProvisionalFromIdentity("https://target.invalid", "target", now, source)
			if e != nil {
				t.Fatal(e)
			}
			if !samePublicKey(target.portable.Public(), p.portable.Public()) || samePublicKey(target.access.Public(), p.access.Public()) || bytes.Equal(target.recipient.Bytes(), p.recipient.Bytes()) || target.record.Selector == p.record.Selector {
				t.Fatal("reused unrelated source keys")
			}
			encoded, _ := target.EncodePrivate()
			loaded, e := DecodeProvisional(encoded)
			if e != nil {
				t.Fatal(e)
			}
			a, _ := target.Public()
			b, _ := loaded.Public()
			aj, _ := json.Marshal(a)
			bj, _ := json.Marshal(b)
			if !bytes.Equal(aj, bj) || a.Version != 2 {
				t.Fatal("v2 round trip")
			}
			if _, _, e = NewProvisionalFromIdentity(p.record.ServerURL, "same", now, source); e == nil {
				t.Fatal("same target accepted")
			}
			preparedOnly := custodyTestDir(t)
			savePortableFixture(t, p, raw, preparedOnly, "prepared")
			if _, e = LoadPortableIdentity(preparedOnly, p.record.Selector, now); e == nil {
				t.Fatal("prepared-only source accepted")
			}
			if err = os.Chmod(path, 0644); err == nil && runtime.GOOS != "windows" {
				if _, e = LoadPortableIdentity(root, p.record.Selector, now); e == nil {
					t.Fatal("unsafe file accepted")
				}
				os.Chmod(path, 0600)
			}
		})
	}
}

func TestPortablePrivateClaimMutationAndLegacyEncoding(t *testing.T) {
	p, raw, now := portableFixture(t, 0, 0)
	root := custodyTestDir(t)
	savePortableFixture(t, p, raw, root, "enrolled")
	source, err := LoadPortableIdentity(root, p.record.Selector, now)
	if err != nil {
		t.Fatal(err)
	}
	target, _, err := NewProvisionalFromIdentity("https://target.invalid", "target", now, source)
	if err != nil {
		t.Fatal(err)
	}
	encoded, _ := target.EncodePrivate()
	for _, field := range []string{"owner_id", "agent_id", "issuer_sha256", "owner_der", "certificate_der", "unknown", "Owner_ID"} {
		t.Run(field, func(t *testing.T) {
			var claim map[string]any
			json.Unmarshal(target.record.Source, &claim)
			claim[field] = "broken"
			changed, _ := json.Marshal(claim)
			r := target.record
			r.Source = changed
			data, _ := json.Marshal(r)
			if _, e := DecodeProvisional(data); e == nil {
				t.Fatal("changed claim accepted")
			}
		})
	}
	for _, bad := range [][]byte{bytes.Replace(encoded, []byte(`"owner_id":`), []byte(`"owner_id":"duplicate","owner_id":`), 1), bytes.Replace(encoded, []byte(`"source_identity":`), []byte(`"source_identity":null,"source_identity":`), 1), append(encoded, 'x'), bytes.Repeat([]byte(" "), MaxPortableProvisionalBytes+1)} {
		if _, e := DecodeProvisional(bad); e == nil {
			t.Fatal("malformed v2 accepted")
		}
	}
	legacy, _ := p.EncodePrivate()
	public, _ := p.Public()
	ad, _ := json.Marshal(public)
	decoded, e := DecodeProvisional(legacy)
	if e != nil {
		t.Fatal(e)
	}
	again, _ := decoded.Public()
	ad2, _ := json.Marshal(again)
	if !bytes.Equal(ad, ad2) || bytes.Contains(ad, []byte("portable_identity")) || bytes.Contains(legacy, []byte("source_identity")) {
		t.Fatal("v1 reconstruction changed")
	}
	if _, e := DecodeProvisional(append(legacy, bytes.Repeat([]byte(" "), MaxProvisionalBytes)...)); e == nil {
		t.Fatal("v1 bound relaxed")
	}
	fresh, _, e := NewProvisionalFromIdentity("https://target.invalid", "fresh", now, nil)
	if e != nil {
		t.Fatal(e)
	}
	f, _ := fresh.Public()
	fj, _ := json.Marshal(f)
	if !bytes.Contains(fj, []byte(`"portable_identity":null`)) {
		t.Fatal("v2 fresh missing null")
	}
}

func TestPythonPortableCodecPeer(t *testing.T) {
	if os.Getenv("ARBITER_TEST_PORTABLE_CODEC") != "1" {
		t.Skip("Python codec peer is not running")
	}
	p, raw, now := portableFixture(t, 0, 366*24*time.Hour)
	root := custodyTestDir(t)
	savePortableFixture(t, p, raw, root, "enrolled")
	source, err := LoadPortableIdentity(root, p.record.Selector, now)
	if err != nil {
		t.Fatal(err)
	}
	for _, s := range []*PortableIdentity{nil, source} {
		target, _, e := NewProvisionalFromIdentity("https://target.invalid", "target", now, s)
		if e != nil {
			t.Fatal(e)
		}
		private, _ := target.EncodePrivate()
		target, e = DecodeProvisional(private)
		if e != nil {
			t.Fatal(e)
		}
		public, e := target.Public()
		if e != nil {
			t.Fatal(e)
		}
		output, e := json.Marshal(struct {
			Public   ProvisionalPublic `json:"public"`
			OwnerDER []byte            `json:"owner_der"`
		}{public, source.source.OwnerDER})
		if e != nil {
			t.Fatal(e)
		}
		fmt.Println("ARBITER_PORTABLE_CODEC=" + string(output))
	}
	hash := sha256.Sum256(source.source.OwnerDER)
	if source.source.Fingerprint != hex.EncodeToString(hash[:]) {
		t.Fatal("issuer hash")
	}
}

func TestStoredSourceBindingsAndVersionTwoReceipt(t *testing.T) {
	p, raw, now := portableFixture(t, 0, 0)
	root := custodyTestDir(t)
	original := savePortableFixture(t, p, raw, root, "enrolled")
	path := filepath.Join(root, "enrolled", p.record.Selector+".json")
	for _, offset := range []int{0, len(preparedTag), len(preparedTag) + 36, len(preparedTag) + 72, len(preparedTag) + 144 + 22} {
		var record preparedPrivateRecord
		json.Unmarshal(original, &record)
		record.Result = bytes.Clone(raw)
		record.Result[offset] ^= 1
		bad, _ := json.Marshal(record)
		if err := os.WriteFile(path, bad, 0600); err != nil {
			t.Fatal(err)
		}
		if _, err := LoadPortableIdentity(root, p.record.Selector, now); err == nil {
			t.Fatal("altered stored binding accepted", offset)
		}
	}
	if err := os.WriteFile(path, original, 0600); err != nil {
		t.Fatal(err)
	}
	source, err := LoadPortableIdentity(root, p.record.Selector, now)
	if err != nil {
		t.Fatal(err)
	}
	target, _, err := NewProvisionalFromIdentity("https://target.invalid", "target", now, source)
	if err != nil {
		t.Fatal(err)
	}
	other, _, _ := NewProvisional("https://other.invalid", "other", now)
	r := target.record
	r.PortableKey = other.record.PortableKey
	bad, _ := json.Marshal(r)
	if _, err = DecodeProvisional(bad); err == nil {
		t.Fatal("source key mismatch accepted")
	}
	p.record.Version = 2
	p.record.Source = json.RawMessage("null")
	public, _ := p.Public()
	ad, _ := json.Marshal(public)
	hash := sha256.Sum256(ad)
	raw = bytes.Clone(raw)
	copy(raw[len(preparedTag)+144+22:], hash[:])
	v2Root := custodyTestDir(t)
	savePortableFixture(t, p, raw, v2Root, "enrolled")
	if _, _, err = loadPrepared(filepath.Join(v2Root, "enrolled"), p.record.Selector, now, true); err != nil {
		t.Fatal("v2 receipt", err)
	}
	if _, err = LoadPortableIdentity(v2Root, p.record.Selector, now); err != nil {
		t.Fatal("v2 source", err)
	}
}
