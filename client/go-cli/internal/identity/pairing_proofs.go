package identity

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/rand"
	"crypto/sha256"
	"encoding/binary"
	"encoding/json"
	"math/big"
	"time"
)

const serverProofTag = "arbiter-pairing-server-v1\x00"
const clientProofTag = "arbiter-pairing-client-v1\x00"

// PairingServer contains public trust verified inside the approved PSK channel.
// It is provisional: this does not persist trust or prove durable activation.
type PairingServer struct {
	ServerID       string
	AuthorityDER   []byte
	CertificateDER []byte
}

func proofDigest(context []byte, role string) [32]byte {
	return sha256.Sum256(append(append(bytes.Clone(context), 0), role...))
}

func proofSignature(key *ecdsa.PrivateKey, context []byte, role string) ([]byte, error) {
	digest := proofDigest(context, role)
	r, s, err := ecdsa.Sign(rand.Reader, key, digest[:])
	if err != nil {
		return nil, err
	}
	result := make([]byte, 64)
	r.FillBytes(result[:32])
	s.FillBytes(result[32:])
	return result, nil
}

func proofCertificate(data []byte) ([]byte, []byte, error) {
	if len(data) < 2 {
		return nil, nil, errPairing
	}
	n := int(binary.BigEndian.Uint16(data))
	if n == 0 || n > MaxCertificateBytes || len(data) < n+2 {
		return nil, nil, errPairing
	}
	return data[2 : n+2], data[n+2:], nil
}

func verifyServerProof(data, handshakeHash, prologue []byte, now time.Time) (*PairingServer, []byte, error) {
	if len(handshakeHash) != 32 || len(data) < len(serverProofTag)+36+4+64 ||
		len(data) > len(serverProofTag)+36+4+2*MaxCertificateBytes+64 || !bytes.HasPrefix(data, []byte(serverProofTag)) {
		return nil, nil, errPairing
	}
	id := string(data[len(serverProofTag) : len(serverProofTag)+36])
	root, rest, err := proofCertificate(data[len(serverProofTag)+36:])
	if err != nil || !uuid4.MatchString(id) {
		return nil, nil, errPairing
	}
	leaf, signature, err := proofCertificate(rest)
	if err != nil || len(signature) != 64 {
		return nil, nil, errPairing
	}
	cert, err := VerifyCredential(leaf, Identity{Role: ServerTLS, SubjectID: id}, &Issuer{DER: root, ID: id}, now)
	if err != nil {
		return nil, nil, errPairing
	}
	prologueHash := sha256.Sum256(prologue)
	headerHash := sha256.Sum256(data[:len(data)-64])
	h := sha256.New()
	h.Write([]byte("arbiter-enrollment-possession-v1\x00"))
	h.Write(handshakeHash)
	h.Write(prologueHash[:])
	h.Write(headerHash[:])
	context := h.Sum(nil)
	digest := proofDigest(context, "server")
	key, ok := cert.PublicKey.(*ecdsa.PublicKey)
	if !ok || !ecdsa.Verify(key, digest[:], new(big.Int).SetBytes(signature[:32]), new(big.Int).SetBytes(signature[32:])) {
		return nil, nil, errPairing
	}
	return &PairingServer{id, bytes.Clone(root), bytes.Clone(leaf)}, context, nil
}

// ProveClient authenticates the server leaf and proves both exact proposed
// signing keys over this one request and Noise exchange. Both proofs are inside
// Noise. A successful return is not a server acceptance or activation receipt.
func (p *PairingInitiator) ProveClient(serverWire []byte, material *Provisional, now time.Time) ([]byte, *PairingServer, error) {
	if p == nil || p.pairingState == nil {
		return nil, nil, errPairing
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.phase != 2 || p.proofSent {
		return nil, nil, p.fail()
	}
	public, err := material.Public()
	if err != nil {
		return nil, nil, p.fail()
	}
	advertisement, err := json.Marshal(public)
	if err != nil || !bytes.Equal(advertisement, p.advertisement) {
		return nil, nil, p.fail()
	}
	data, err := p.decryptLocked(serverWire)
	if err != nil {
		return nil, nil, errPairing
	}
	server, context, err := verifyServerProof(data, p.hash, p.prologue, now)
	if err != nil {
		return nil, nil, p.fail()
	}
	proof := []byte(clientProofTag)
	for i, key := range []*ecdsa.PrivateKey{material.access, material.portable} {
		role := []string{"access", "portable"}[i]
		signature, err := proofSignature(key, context, role)
		if err != nil {
			return nil, nil, p.fail()
		}
		proof = append(proof, signature...)
	}
	wire, err := p.encryptLocked(proof)
	if err != nil {
		return nil, nil, errPairing
	}
	p.proofSent = true
	p.serverTrust = &PairingServer{server.ServerID, bytes.Clone(server.AuthorityDER), bytes.Clone(server.CertificateDER)}
	return wire, server, nil
}
