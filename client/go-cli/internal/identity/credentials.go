// Package identity verifies explicit Arbiter credential roles. Verification does
// not register an issuer, enroll a client, or grant access.
package identity

import (
	"bytes"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/x509"
	"encoding/asn1"
	"errors"
	"regexp"
	"time"
)

const MaxCertificateBytes = 4096

type Role string

const (
	Owner           Role = "owner"
	ServerAuthority Role = "server-authority"
	Portable        Role = "portable"
	Access          Role = "access"
	ServerTLS       Role = "server"
)

type Identity struct {
	Role         Role
	SubjectID    string
	ServerID     string
	EnrollmentID string
}

// Issuer must come from caller-selected registered trust, never from a peer's
// assertion that its own root is trusted.
type Issuer struct {
	DER []byte
	ID  string
}

var (
	errCredential       = errors.New("invalid Arbiter credential profile or registered issuer")
	uuid4               = regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`)
	constraints         = asn1.ObjectIdentifier{2, 5, 29, 19}
	keyUsage            = asn1.ObjectIdentifier{2, 5, 29, 15}
	san                 = asn1.ObjectIdentifier{2, 5, 29, 17}
	eku                 = asn1.ObjectIdentifier{2, 5, 29, 37}
	subjectKeyID        = asn1.ObjectIdentifier{2, 5, 29, 14}
	authorityKeyID      = asn1.ObjectIdentifier{2, 5, 29, 35}
	ecdsaSHA256         = asn1.ObjectIdentifier{1, 2, 840, 10045, 4, 3, 2}
	ecPublicKey         = asn1.ObjectIdentifier{1, 2, 840, 10045, 2, 1}
	p256                = asn1.ObjectIdentifier{1, 2, 840, 10045, 3, 1, 7}
	unsupportedOptional = map[string]struct{}{
		"1.3.36.8.3.3":            {},
		"1.3.6.1.4.1.11129.2.4.2": {}, "1.3.6.1.4.1.11129.2.4.3": {},
		"1.3.6.1.4.1.11129.2.4.5": {}, "1.3.6.1.4.1.311.21.7": {},
		"1.3.6.1.5.5.7.1.1": {}, "1.3.6.1.5.5.7.1.11": {},
		"1.3.6.1.5.5.7.1.24": {}, "1.3.6.1.5.5.7.48.1.5": {},
		"2.5.29.9": {}, "2.5.29.16": {}, "2.5.29.18": {}, "2.5.29.20": {},
		"2.5.29.27": {}, "2.5.29.28": {}, "2.5.29.30": {}, "2.5.29.31": {},
		"2.5.29.32": {}, "2.5.29.33": {}, "2.5.29.36": {}, "2.5.29.46": {},
		"2.5.29.54": {},
	}
)

// strictDERElement parses one complete DER element and rejects non-minimal
// length encodings. encoding/asn1 intentionally permits extra fields at the
// end of a SEQUENCE, so the profile parser below consumes every child itself.
func strictDERElement(der []byte) (asn1.RawValue, []byte, error) {
	var value asn1.RawValue
	rest, err := asn1.Unmarshal(der, &value)
	if err != nil || len(rest) == len(der) {
		return asn1.RawValue{}, nil, errCredential
	}
	return value, rest, nil
}

func strictOptionalExtension(id string, value []byte) error {
	switch id {
	case subjectKeyID.String():
		inner, remaining, err := strictDERElement(value)
		if err != nil || len(remaining) != 0 || inner.Class != asn1.ClassUniversal || inner.Tag != asn1.TagOctetString || len(inner.Bytes) == 0 {
			return errCredential
		}
	case authorityKeyID.String():
		children, err := strictSequence(value)
		if err != nil || len(children) != 1 {
			return errCredential
		}
		child := children[0]
		if child.Class != asn1.ClassContextSpecific || child.Tag != 0 || child.IsCompound || len(child.Bytes) == 0 {
			return errCredential
		}
	default:
		if _, unsupported := unsupportedOptional[id]; unsupported {
			return errCredential
		}
		element, remaining, err := strictDERElement(value)
		if err != nil || len(remaining) != 0 {
			return errCredential
		}
		pending := []asn1.RawValue{element}
		for len(pending) != 0 {
			element = pending[len(pending)-1]
			pending = pending[:len(pending)-1]
			if element.IsCompound {
				children, err := strictConstructedChildren(element)
				if err != nil {
					return err
				}
				pending = append(pending, children...)
			}
		}
	}
	return nil
}

func strictSequence(der []byte) ([]asn1.RawValue, error) {
	value, rest, err := strictDERElement(der)
	if err != nil || len(rest) != 0 || value.Class != asn1.ClassUniversal || value.Tag != asn1.TagSequence || !value.IsCompound {
		return nil, errCredential
	}
	return strictConstructedChildren(value)
}

func strictConstructedChildren(value asn1.RawValue) ([]asn1.RawValue, error) {
	if !value.IsCompound {
		return nil, errCredential
	}
	var children []asn1.RawValue
	content := value.Bytes
	for len(content) != 0 {
		child, remaining, err := strictDERElement(content)
		if err != nil {
			return nil, errCredential
		}
		children = append(children, child)
		content = remaining
	}
	return children, nil
}

func strictOID(value asn1.RawValue, expected asn1.ObjectIdentifier) error {
	if value.Class != asn1.ClassUniversal || value.Tag != asn1.TagOID {
		return errCredential
	}
	var oid asn1.ObjectIdentifier
	if rest, err := asn1.Unmarshal(value.FullBytes, &oid); err != nil || len(rest) != 0 || !oid.Equal(expected) {
		return errCredential
	}
	return nil
}

func strictAlgorithmIdentifier(der []byte, algorithm asn1.ObjectIdentifier, parameter asn1.ObjectIdentifier) error {
	children, err := strictSequence(der)
	if err != nil || len(children) < 1 || len(children) > 2 {
		return errCredential
	}
	if err := strictOID(children[0], algorithm); err != nil {
		return err
	}
	if len(children) == 1 {
		if parameter != nil {
			return errCredential
		}
		return nil
	}
	if parameter == nil {
		return errCredential
	}
	return strictOID(children[1], parameter)
}

func strictName(der []byte) error {
	children, err := strictSequence(der)
	if err != nil {
		return err
	}
	for _, set := range children {
		if set.Class != asn1.ClassUniversal || set.Tag != asn1.TagSet || !set.IsCompound {
			return errCredential
		}
		attributes, err := strictConstructedChildren(set)
		if err != nil {
			return err
		}
		for _, attribute := range attributes {
			parts, err := strictSequence(attribute.FullBytes)
			if err != nil || len(parts) != 2 || parts[0].Class != asn1.ClassUniversal || parts[0].Tag != asn1.TagOID {
				return errCredential
			}
		}
	}
	return nil
}

func strictValidity(der []byte) error {
	children, err := strictSequence(der)
	if err != nil || len(children) != 2 {
		return errCredential
	}
	for _, child := range children {
		if child.Class != asn1.ClassUniversal || child.Tag != asn1.TagUTCTime && child.Tag != asn1.TagGeneralizedTime {
			return errCredential
		}
	}
	return nil
}

func strictExtensions(value asn1.RawValue) error {
	if value.Class != asn1.ClassContextSpecific || value.Tag != 3 || !value.IsCompound {
		return errCredential
	}
	children, err := strictSequence(value.Bytes)
	if err != nil {
		return err
	}
	for _, extension := range children {
		parts, err := strictSequence(extension.FullBytes)
		if err != nil || len(parts) < 2 || len(parts) > 3 {
			return errCredential
		}
		if parts[0].Class != asn1.ClassUniversal || parts[0].Tag != asn1.TagOID {
			return errCredential
		}
		valuePart := parts[1]
		if len(parts) == 3 {
			if valuePart.Class != asn1.ClassUniversal || valuePart.Tag != asn1.TagBoolean || len(valuePart.Bytes) != 1 || valuePart.Bytes[0] != 0xff {
				return errCredential
			}
			valuePart = parts[2]
		}
		if valuePart.Class != asn1.ClassUniversal || valuePart.Tag != asn1.TagOctetString {
			return errCredential
		}
		var extensionID asn1.ObjectIdentifier
		if rest, parseErr := asn1.Unmarshal(parts[0].FullBytes, &extensionID); parseErr != nil || len(rest) != 0 {
			return errCredential
		}
		if err := strictOptionalExtension(extensionID.String(), valuePart.Bytes); err != nil {
			return err
		}
	}
	return nil
}

func strictCertificateDER(der []byte) error {
	certificate, err := strictSequence(der)
	if err != nil || len(certificate) != 3 {
		return errCredential
	}
	tbs, err := strictSequence(certificate[0].FullBytes)
	if err != nil || len(tbs) != 8 {
		return errCredential
	}
	versionValue := tbs[0]
	version, err := strictConstructedChildren(versionValue)
	if err != nil || versionValue.Class != asn1.ClassContextSpecific || versionValue.Tag != 0 || len(version) != 1 || version[0].Class != asn1.ClassUniversal || version[0].Tag != asn1.TagInteger {
		return errCredential
	}
	if err := strictAlgorithmIdentifier(tbs[2].FullBytes, ecdsaSHA256, nil); err != nil {
		return errCredential
	}
	if err := strictAlgorithmIdentifier(certificate[1].FullBytes, ecdsaSHA256, nil); err != nil || !bytes.Equal(certificate[1].FullBytes, tbs[2].FullBytes) {
		return errCredential
	}
	if err := strictName(tbs[3].FullBytes); err != nil {
		return err
	}
	if err := strictValidity(tbs[4].FullBytes); err != nil {
		return err
	}
	if err := strictName(tbs[5].FullBytes); err != nil {
		return err
	}
	spki, err := strictSequence(tbs[6].FullBytes)
	if err != nil || len(spki) != 2 || spki[1].Class != asn1.ClassUniversal || spki[1].Tag != asn1.TagBitString {
		return errCredential
	}
	if err := strictAlgorithmIdentifier(spki[0].FullBytes, ecPublicKey, p256); err != nil {
		return err
	}
	if err := strictExtensions(tbs[7]); err != nil {
		return err
	}
	if certificate[2].Class != asn1.ClassUniversal || certificate[2].Tag != asn1.TagBitString {
		return errCredential
	}
	return nil
}

func samePublicKey(first, second any) bool {
	a, ok := first.(*ecdsa.PublicKey)
	if !ok {
		return false
	}
	b, ok := second.(*ecdsa.PublicKey)
	return ok && a.X.Cmp(b.X) == 0 && a.Y.Cmp(b.Y) == 0
}

func (i Identity) ca() bool { return i.Role == Owner || i.Role == ServerAuthority }

func (i Identity) names() ([]asn1.RawValue, error) {
	switch i.Role {
	case Owner, ServerAuthority, Portable, Access, ServerTLS:
	default:
		return nil, errCredential
	}
	roles, ids := []string{string(i.Role)}, []string{i.SubjectID}
	if i.Role == Access {
		roles = append(roles, "server", "enrollment")
		ids = append(ids, i.ServerID, i.EnrollmentID)
	} else if i.ServerID != "" || i.EnrollmentID != "" {
		return nil, errCredential
	}
	seen := make(map[string]bool)
	var names []asn1.RawValue
	for n, id := range ids {
		if !uuid4.MatchString(id) || seen[id] {
			return nil, errCredential
		}
		seen[id] = true
		names = append(names, asn1.RawValue{Class: 2, Tag: 6, Bytes: []byte("urn:arbiter:" + roles[n] + ":" + id)})
	}
	return names, nil
}

func profile(der []byte, expected Identity, now time.Time) (*x509.Certificate, error) {
	if len(der) == 0 || len(der) > MaxCertificateBytes {
		return nil, errCredential
	}
	names, err := expected.names()
	if err != nil {
		return nil, err
	}
	cert, err := x509.ParseCertificate(der)
	if err != nil || !bytes.Equal(cert.Raw, der) || strictCertificateDER(der) != nil {
		return nil, errCredential
	}
	key, ok := cert.PublicKey.(*ecdsa.PublicKey)
	if !ok || key.Curve != elliptic.P256() || cert.Version != 3 || cert.SignatureAlgorithm != x509.ECDSAWithSHA256 || cert.SerialNumber.Sign() < 0 {
		return nil, errCredential
	}
	if now.Before(cert.NotBefore) || now.After(cert.NotAfter) {
		return nil, errCredential
	}
	lifetime := 365 * 24 * time.Hour
	bc, ku := []byte{0x30, 0}, []byte{3, 2, 7, 0x80}
	if expected.ca() {
		lifetime *= 10
		bc, ku = []byte{0x30, 6, 1, 1, 0xff, 2, 1, 0}, []byte{3, 2, 2, 4}
	}
	if duration := cert.NotAfter.Sub(cert.NotBefore); duration <= 0 || duration > lifetime+5*time.Minute {
		return nil, errCredential
	}
	namesDER, err := asn1.Marshal(names)
	if err != nil {
		return nil, errCredential
	}
	values := map[string][]byte{constraints.String(): bc, keyUsage.String(): ku, san.String(): namesDER}
	if !expected.ca() {
		purpose := asn1.ObjectIdentifier{1, 3, 6, 1, 5, 5, 7, 3, 2}
		if expected.Role == ServerTLS {
			purpose[len(purpose)-1] = 1
		}
		values[eku.String()], err = asn1.Marshal([]asn1.ObjectIdentifier{purpose})
		if err != nil {
			return nil, errCredential
		}
	}
	for _, extension := range cert.Extensions {
		id := extension.Id.String()
		value, required := values[id]
		if required {
			if !extension.Critical || !bytes.Equal(extension.Value, value) {
				return nil, errCredential
			}
			delete(values, id)
		} else if extension.Critical || expected.ca() && extension.Id.Equal(eku) {
			return nil, errCredential
		}
	}
	if len(values) != 0 {
		return nil, errCredential
	}
	return cert, nil
}

// VerifyCredential checks one exact role/identity against explicit trust. A nil
// issuer is accepted only for a self-signed authority profile; it does not make
// that authority trusted. now is explicit so callers cannot silently skip time.
func VerifyCredential(der []byte, expected Identity, issuer *Issuer, now time.Time) (*x509.Certificate, error) {
	cert, err := profile(der, expected, now)
	if err != nil {
		return nil, err
	}
	root := cert
	if expected.ca() {
		if issuer != nil {
			return nil, errCredential
		}
	} else {
		if issuer == nil {
			return nil, errCredential
		}
		role := ServerAuthority
		if expected.Role == Portable {
			role = Owner
		} else {
			serverID := expected.SubjectID
			if expected.Role == Access {
				serverID = expected.ServerID
			}
			if issuer.ID != serverID {
				return nil, errCredential
			}
		}
		root, err = VerifyCredential(issuer.DER, Identity{Role: role, SubjectID: issuer.ID}, nil, now)
		if err != nil || cert.NotBefore.Before(root.NotBefore) || cert.NotAfter.After(root.NotAfter) || samePublicKey(cert.PublicKey, root.PublicKey) {
			return nil, errCredential
		}
	}
	if !bytes.Equal(cert.RawIssuer, root.RawSubject) || root.CheckSignature(cert.SignatureAlgorithm, cert.RawTBSCertificate, cert.Signature) != nil {
		return nil, errCredential
	}
	return cert, nil
}
