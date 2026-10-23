package identity

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/asn1"
	"encoding/binary"
	"math/big"
	"testing"
	"time"

	"github.com/flynn/noise"
)

func proofPeer(t *testing.T) (*PairingInitiator, *Provisional, *noise.CipherState, *noise.CipherState) {
	t.Helper()
	material, secret, err := NewProvisional("https://localhost", "Agent Smith", time.Now())
	if err != nil {
		t.Fatal(err)
	}
	p, err := NewPairingInitiator(material, secret)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(p.Close)
	hs := responderFixture(t, p.prologue, secret)
	first, err := p.FirstFlight()
	if err != nil {
		t.Fatal(err)
	}
	if _, _, _, err := hs.ReadMessage(nil, first); err != nil {
		t.Fatal(err)
	}
	reply, receive, send, err := hs.WriteMessage(nil, pairingPayload(p.prologue, "server"))
	if err != nil || p.Complete(reply) != nil {
		t.Fatal("handshake failed", err)
	}
	return p, material, send, receive
}

func proofCertificates(t *testing.T) ([]byte, []byte, *ecdsa.PrivateKey, string, time.Time) {
	t.Helper()
	now := time.Now().UTC().Truncate(time.Second)
	id := "10000000-0000-4000-8000-000000000001"
	rootKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	leafKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	template := func(role string) *x509.Certificate {
		value, err := asn1.Marshal([]asn1.RawValue{{Class: 2, Tag: 6, Bytes: []byte("urn:arbiter:" + role + ":" + id)}})
		if err != nil {
			t.Fatal(err)
		}
		return &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: role}, NotBefore: now.Add(-time.Hour), NotAfter: now.Add(time.Hour), BasicConstraintsValid: true, ExtraExtensions: []pkix.Extension{{Id: san, Critical: true, Value: value}}}
	}
	root := template("server-authority")
	root.IsCA, root.MaxPathLenZero, root.KeyUsage = true, true, x509.KeyUsageCertSign
	rootDER, err := x509.CreateCertificate(rand.Reader, root, root, rootKey.Public(), rootKey)
	if err != nil {
		t.Fatal(err)
	}
	leaf := template("server")
	leaf.KeyUsage, leaf.ExtKeyUsage = x509.KeyUsageDigitalSignature, []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}
	purpose, err := asn1.Marshal([]asn1.ObjectIdentifier{{1, 3, 6, 1, 5, 5, 7, 3, 1}})
	if err != nil {
		t.Fatal(err)
	}
	leaf.ExtraExtensions = append(leaf.ExtraExtensions, pkix.Extension{Id: eku, Critical: true, Value: purpose})
	leafDER, err := x509.CreateCertificate(rand.Reader, leaf, root, leafKey.Public(), rootKey)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := VerifyCredential(leafDER, Identity{Role: ServerTLS, SubjectID: id}, &Issuer{DER: rootDER, ID: id}, now); err != nil {
		t.Fatal("invalid fixture credential", err)
	}
	return rootDER, leafDER, leafKey, id, now
}

func proofPacket(t *testing.T, p *PairingInitiator, root, leaf []byte, key *ecdsa.PrivateKey, id string) ([]byte, []byte) {
	t.Helper()
	header := []byte(serverProofTag + id)
	for _, cert := range [][]byte{root, leaf} {
		header = binary.BigEndian.AppendUint16(header, uint16(len(cert)))
		header = append(header, cert...)
	}
	a, b := sha256.Sum256(p.prologue), sha256.Sum256(header)
	h := sha256.New()
	h.Write([]byte("arbiter-enrollment-possession-v1\x00"))
	h.Write(p.hash)
	h.Write(a[:])
	h.Write(b[:])
	context := h.Sum(nil)
	sig, err := proofSignature(key, context, "server")
	if err != nil {
		t.Fatal(err)
	}
	return append(header, sig...), context
}

func TestPairingMutualProofAndCopiedOwner(t *testing.T) {
	p, material, send, receive := proofPeer(t)
	root, leaf, key, id, now := proofCertificates(t)
	packet, context := proofPacket(t, p, root, leaf, key, id)
	wire, err := send.Encrypt(nil, nil, packet)
	if err != nil {
		t.Fatal(err)
	}
	reply, trust, err := p.ProveClient(wire, material, now)
	if err != nil || trust == nil || trust.ServerID != id || !bytes.Equal(trust.AuthorityDER, root) || !bytes.Equal(trust.CertificateDER, leaf) {
		t.Fatal("server proof failed", err)
	}
	proof, err := receive.Decrypt(nil, nil, reply)
	if err != nil || !bytes.HasPrefix(proof, []byte(clientProofTag)) || len(proof) != len(clientProofTag)+128 {
		t.Fatal("client proof failed", err)
	}
	for i, key := range []*ecdsa.PublicKey{&material.access.PublicKey, &material.portable.PublicKey} {
		sig := proof[len(clientProofTag)+i*64 : len(clientProofTag)+(i+1)*64]
		digest := proofDigest(context, []string{"access", "portable"}[i])
		if !ecdsa.Verify(key, digest[:], new(big.Int).SetBytes(sig[:32]), new(big.Int).SetBytes(sig[32:])) {
			t.Fatal("wrong key/role signature")
		}
	}
	alias := *p
	if _, _, err := alias.ProveClient(wire, material, now); err == nil {
		t.Fatal("replayed proof")
	}
	if _, err := p.HandshakeHash(); err == nil {
		t.Fatal("copy failure did not close owner")
	}
}

func TestPairingServerProofSubstitutionFailsClosed(t *testing.T) {
	root, leaf, key, id, now := proofCertificates(t)
	for _, invalid := range []string{"signature", "role", "identity", "root", "leaf", "context", "handshake", "reflected-role", "extra", "truncated", "oversize", "expired", "keys", "nil-keys"} {
		t.Run(invalid, func(t *testing.T) {
			p, material, send, _ := proofPeer(t)
			packet, context := proofPacket(t, p, root, leaf, key, id)
			when := now
			switch invalid {
			case "signature":
				packet[len(packet)-1] ^= 1
			case "role":
				packet[0] ^= 1
			case "identity":
				packet[len(serverProofTag)] ^= 1
			case "root":
				packet[len(serverProofTag)+36+2] ^= 1
			case "leaf":
				packet[len(serverProofTag)+36+2+len(root)+2] ^= 1
			case "context":
				p.prologue = append(p.prologue, 1)
			case "handshake":
				p.hash[0] ^= 1
			case "reflected-role":
				signature, err := proofSignature(key, context, "access")
				if err != nil {
					t.Fatal(err)
				}
				copy(packet[len(packet)-64:], signature)
			case "extra":
				packet = append(packet, 0)
			case "truncated":
				packet = packet[:len(packet)-1]
			case "oversize":
				packet = make([]byte, 9000)
			case "expired":
				when = now.Add(2 * time.Hour)
			case "keys":
				material, _, _ = NewProvisional("https://localhost", "Other", now)
			case "nil-keys":
				material = nil
			}
			wire, err := send.Encrypt(nil, nil, packet)
			if err != nil {
				t.Fatal(err)
			}
			if _, trust, err := p.ProveClient(wire, material, when); err == nil || trust != nil {
				t.Fatal("accepted invalid server/key proof")
			}
			if _, err := p.HandshakeHash(); err == nil {
				t.Fatal("invalid proof not terminal")
			}
		})
	}
}

func TestPairingProofRequiresCompletedChannel(t *testing.T) {
	for _, p := range []*PairingInitiator{nil, {}, {pairingState: &pairingState{}}} {
		if _, _, err := p.ProveClient(nil, nil, time.Now()); err == nil {
			t.Fatal("uninitialized proof accepted")
		}
	}
}
