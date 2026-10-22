package identity

import (
	"bytes"
	"crypto/ecdh"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"errors"
	"io"
	"net/url"
	"strings"
	"time"
	"unicode"
	"unicode/utf8"
)

const MaxProvisionalBytes = 8192

const privateWarning = "NEVER SHARE THIS: private client keys; possession permits impersonation."

var errProvisional = errors.New("invalid provisional enrollment material")

// PairingSecret stays in memory. Encoded is for explicit operator handoff;
// Bytes supplies a copy to the authenticated pairing protocol.
type PairingSecret struct {
	value [32]byte
	valid bool
}

func (s PairingSecret) Encoded() string {
	if !s.valid {
		return ""
	}
	return base64.RawURLEncoding.EncodeToString(s.value[:])
}
func (s PairingSecret) Bytes() ([32]byte, error) {
	if !s.valid {
		return [32]byte{}, errProvisional
	}
	return s.value, nil
}
func (s PairingSecret) String() string   { return "<pairing secret redacted>" }
func (s PairingSecret) GoString() string { return s.String() }
func (s PairingSecret) MarshalJSON() ([]byte, error) {
	return nil, errors.New("pairing secrets require explicit handoff")
}

func ParsePairingSecret(encoded string) (PairingSecret, error) {
	var secret PairingSecret
	if len(encoded) != 43 {
		return secret, errProvisional
	}
	raw, err := base64.RawURLEncoding.DecodeString(encoded)
	if err != nil || len(raw) != len(secret.value) || base64.RawURLEncoding.EncodeToString(raw) != encoded {
		return secret, errProvisional
	}
	copy(secret.value[:], raw)
	secret.valid = true
	return secret, nil
}

type provisionalRecord struct {
	Warning      string    `json:"warning"`
	Version      int       `json:"version"`
	Selector     string    `json:"selector"`
	ServerURL    string    `json:"server_url"`
	Name         string    `json:"name"`
	CreatedAt    time.Time `json:"created_at"`
	AccessKey    string    `json:"access_key_pkcs8"`
	PortableKey  string    `json:"portable_key_pkcs8"`
	RecipientKey string    `json:"recipient_key_pkcs8"`
}

// Provisional contains no approved agent ID, certificate, grant or server trust.
// Private persistence and ordinary traffic require separate verified boundaries.
type Provisional struct {
	record    provisionalRecord
	access    *ecdsa.PrivateKey
	portable  *ecdsa.PrivateKey
	recipient *ecdh.PrivateKey
}

type ProvisionalPublic struct {
	Version      int    `json:"version"`
	Selector     string `json:"selector"`
	Name         string `json:"name"`
	AccessKey    []byte `json:"access_spki"`
	PortableKey  []byte `json:"portable_spki"`
	RecipientKey []byte `json:"recipient_spki"`
}

func (Provisional) String() string     { return "<provisional private keys redacted>" }
func (p Provisional) GoString() string { return p.String() }
func (Provisional) MarshalJSON() ([]byte, error) {
	return nil, errors.New("private keys require explicit EncodePrivate")
}

func validateProvisionalMetadata(r provisionalRecord) error {
	u, err := url.Parse(r.ServerURL)
	_, offset := r.CreatedAt.Zone()
	if err != nil || len(r.ServerURL) > 2048 || !utf8.ValidString(r.ServerURL) || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" ||
		r.Version != 1 || r.Warning != privateWarning || r.CreatedAt.IsZero() || offset != 0 ||
		len(r.Name) == 0 || len(r.Name) > 256 || strings.TrimSpace(r.Name) != r.Name || !utf8.ValidString(r.Name) || strings.IndexFunc(r.Name, unicode.IsControl) >= 0 {
		return errProvisional
	}
	selector, err := base64.RawURLEncoding.DecodeString(r.Selector)
	if err != nil || len(selector) != 16 || base64.RawURLEncoding.EncodeToString(selector) != r.Selector {
		return errProvisional
	}
	return nil
}

// NewProvisional generates independent keys and a public 128-bit request selector.
// The separate 256-bit PSK is never part of the private persistence format.
func NewProvisional(serverURL, name string, now time.Time) (*Provisional, PairingSecret, error) {
	var secret PairingSecret
	var selector [16]byte
	if _, err := rand.Read(selector[:]); err != nil {
		return nil, secret, err
	}
	r := provisionalRecord{Warning: privateWarning, Version: 1, Selector: base64.RawURLEncoding.EncodeToString(selector[:]), ServerURL: serverURL, Name: name, CreatedAt: now.UTC()}
	if err := validateProvisionalMetadata(r); err != nil {
		return nil, secret, err
	}
	access, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, secret, err
	}
	portable, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		return nil, secret, err
	}
	recipient, err := ecdh.X25519().GenerateKey(rand.Reader)
	if err != nil {
		return nil, secret, err
	}
	keys := []*string{&r.AccessKey, &r.PortableKey, &r.RecipientKey}
	for i, key := range []any{access, portable, recipient} {
		der, err := x509.MarshalPKCS8PrivateKey(key)
		if err != nil {
			return nil, secret, err
		}
		*keys[i] = base64.StdEncoding.EncodeToString(der)
	}
	if _, err := rand.Read(secret.value[:]); err != nil {
		return nil, PairingSecret{}, err
	}
	secret.valid = true
	return &Provisional{r, access, portable, recipient}, secret, nil
}

func (p *Provisional) EncodePrivate() ([]byte, error) {
	if p == nil || p.access == nil || p.portable == nil || p.recipient == nil {
		return nil, errProvisional
	}
	return json.Marshal(p.record)
}

func (p *Provisional) Public() (ProvisionalPublic, error) {
	if p == nil || p.access == nil || p.portable == nil || p.recipient == nil {
		return ProvisionalPublic{}, errProvisional
	}
	access, err := x509.MarshalPKIXPublicKey(p.access.Public())
	if err != nil {
		return ProvisionalPublic{}, err
	}
	portable, err := x509.MarshalPKIXPublicKey(p.portable.Public())
	if err != nil {
		return ProvisionalPublic{}, err
	}
	recipient, err := x509.MarshalPKIXPublicKey(p.recipient.PublicKey())
	return ProvisionalPublic{1, p.record.Selector, p.record.Name, access, portable, recipient}, err
}

func decodeProvisionalKey(encoded string) (any, error) {
	if len(encoded) > 684 {
		return nil, errProvisional
	}
	der, err := base64.StdEncoding.DecodeString(encoded)
	if err != nil || len(der) == 0 || len(der) > 512 || base64.StdEncoding.EncodeToString(der) != encoded {
		return nil, errProvisional
	}
	key, err := x509.ParsePKCS8PrivateKey(der)
	if err != nil {
		return nil, errProvisional
	}
	canonical, err := x509.MarshalPKCS8PrivateKey(key)
	if err != nil || !bytes.Equal(canonical, der) {
		return nil, errProvisional
	}
	return key, nil
}

func DecodeProvisional(data []byte) (*Provisional, error) {
	if len(data) == 0 || len(data) > MaxProvisionalBytes || !utf8.Valid(data) {
		return nil, errProvisional
	}
	// encoding/json alone accepts duplicate object keys. Reject them explicitly.
	d := json.NewDecoder(bytes.NewReader(data))
	token, err := d.Token()
	if err != nil || token != json.Delim('{') {
		return nil, errProvisional
	}
	seen := make(map[string]bool)
	allowed := map[string]bool{"warning": true, "version": true, "selector": true, "server_url": true, "name": true, "created_at": true, "access_key_pkcs8": true, "portable_key_pkcs8": true, "recipient_key_pkcs8": true}
	for d.More() {
		token, err = d.Token()
		name, ok := token.(string)
		if err != nil || !ok || !allowed[name] || seen[name] {
			return nil, errProvisional
		}
		seen[name] = true
		var value json.RawMessage
		if d.Decode(&value) != nil {
			return nil, errProvisional
		}
	}
	if token, err = d.Token(); err != nil || token != json.Delim('}') {
		return nil, errProvisional
	}
	if _, err = d.Token(); err != io.EOF {
		return nil, errProvisional
	}
	var r provisionalRecord
	d = json.NewDecoder(bytes.NewReader(data))
	d.DisallowUnknownFields()
	if d.Decode(&r) != nil || validateProvisionalMetadata(r) != nil {
		return nil, errProvisional
	}
	a, err := decodeProvisionalKey(r.AccessKey)
	if err != nil {
		return nil, err
	}
	b, err := decodeProvisionalKey(r.PortableKey)
	if err != nil {
		return nil, err
	}
	c, err := decodeProvisionalKey(r.RecipientKey)
	if err != nil {
		return nil, err
	}
	access, aok := a.(*ecdsa.PrivateKey)
	portable, bok := b.(*ecdsa.PrivateKey)
	recipient, cok := c.(*ecdh.PrivateKey)
	if !aok || !bok || !cok || access.Curve != elliptic.P256() || portable.Curve != elliptic.P256() || recipient.Curve() != ecdh.X25519() || samePublicKey(access.Public(), portable.Public()) {
		return nil, errProvisional
	}
	return &Provisional{r, access, portable, recipient}, nil
}
