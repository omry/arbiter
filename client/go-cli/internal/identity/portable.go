package identity

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/hex"
	"encoding/json"
	"path/filepath"
	"time"
)

type portableSource struct {
	OwnerID     string `json:"owner_id"`
	AgentID     string `json:"agent_id"`
	Fingerprint string `json:"issuer_sha256"`
	OwnerDER    []byte `json:"owner_der"`
	Certificate []byte `json:"certificate_der"`
}

// PortableIdentity is local enrolled evidence, not target-server authorization.
// Its private key can only be reused through NewProvisionalFromIdentity.
type PortableIdentity struct {
	source    portableSource
	key       *ecdsa.PrivateKey
	serverURL string
}

func (PortableIdentity) String() string               { return "<portable private identity redacted>" }
func (p PortableIdentity) GoString() string           { return p.String() }
func (PortableIdentity) MarshalJSON() ([]byte, error) { return nil, errProvisional }

func decodePortableSource(raw []byte, key *ecdsa.PrivateKey, current *time.Time) (*portableSource, error) {
	if bytes.Equal(raw, []byte("null")) {
		return nil, nil
	}
	var s portableSource
	if len(raw) == 0 || json.Unmarshal(raw, &s) != nil {
		return nil, errProvisional
	}
	canonical, err := json.Marshal(s)
	if err != nil || !bytes.Equal(canonical, raw) || !uuid4.MatchString(s.OwnerID) || !uuid4.MatchString(s.AgentID) || s.OwnerID == s.AgentID || len(s.OwnerDER) > MaxCertificateBytes || len(s.Certificate) > MaxCertificateBytes {
		return nil, errProvisional
	}
	hash := sha256.Sum256(s.OwnerDER)
	if s.Fingerprint != hex.EncodeToString(hash[:]) {
		return nil, errProvisional
	}
	cert, err := x509.ParseCertificate(s.Certificate)
	if err != nil {
		return nil, errProvisional
	}
	at := cert.NotBefore
	if current != nil {
		at = *current
	}
	cert, err = VerifyCredential(s.Certificate, Identity{Role: Portable, SubjectID: s.AgentID}, &Issuer{DER: s.OwnerDER, ID: s.OwnerID}, at)
	if err != nil || key == nil || !samePublicKey(cert.PublicKey, key.Public()) {
		return nil, errProvisional
	}
	return &s, nil
}

// LoadPortableIdentity reads only enrolled/ under the supplied state root.
// Hold the existing enrollment owner across this read and the new enrollment.
// No server connection or source access-certificate lifetime is required.
func LoadPortableIdentity(stateRoot, selector string, now time.Time) (*PortableIdentity, error) {
	material, raw, server, err := loadStoredEnrollment(filepath.Join(stateRoot, "enrolled"), selector, 1)
	if err != nil {
		return nil, err
	}
	prepared, err := verifyPreparedPurpose(raw, material, server, now, true, true)
	if err != nil {
		return nil, err
	}
	fields := raw[len(preparedTag)+144+22+32+32+8:]
	var parts [4][]byte
	for i := range parts {
		parts[i], err = preparedField(&fields)
		if err != nil {
			return nil, err
		}
	}
	hash := sha256.Sum256(parts[2])
	s := portableSource{prepared.OwnerID, prepared.AgentID, hex.EncodeToString(hash[:]), parts[2], parts[3]}
	if material.record.Version == 2 && !bytes.Equal(material.record.Source, []byte("null")) {
		encoded, _ := json.Marshal(s)
		if !bytes.Equal(encoded, material.record.Source) {
			return nil, errProvisional
		}
	}
	return &PortableIdentity{s, material.portable, material.record.ServerURL}, nil
}

// NewProvisionalFromIdentity makes v2 material; nil selects a fresh identity.
func NewProvisionalFromIdentity(serverURL, name string, now time.Time, source *PortableIdentity) (*Provisional, PairingSecret, error) {
	if source != nil {
		raw, _ := json.Marshal(source.source)
		if _, err := decodePortableSource(raw, source.key, &now); err != nil || source.serverURL == serverURL {
			return nil, PairingSecret{}, errProvisional
		}
	}
	p, secret, err := NewProvisional(serverURL, name, now)
	if err != nil {
		return nil, secret, err
	}
	p.record.Version = 2
	p.record.Source = json.RawMessage("null")
	if source != nil {
		p.portable = source.key
		der, err := x509.MarshalPKCS8PrivateKey(p.portable)
		if err != nil {
			return nil, PairingSecret{}, err
		}
		p.record.PortableKey = base64.StdEncoding.EncodeToString(der)
		p.record.Source, err = json.Marshal(source.source)
		if err != nil {
			return nil, PairingSecret{}, err
		}
	}
	return p, secret, nil
}
