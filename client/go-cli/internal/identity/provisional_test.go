package identity

import (
	"bytes"
	"context"
	"crypto/ecdh"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/hmac"
	"crypto/rand"
	"crypto/sha256"
	"crypto/x509"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"testing"
	"time"
)

var provisionalNow = time.Date(2026, 10, 22, 0, 0, 0, 0, time.UTC)

func provisional(t *testing.T) (*Provisional, PairingSecret) {
	t.Helper()
	p, secret, err := NewProvisional("https://localhost:8075/prefix", "Agent Smith", provisionalNow)
	if err != nil {
		t.Fatal(err)
	}
	return p, secret
}

func TestProvisionalRoundTripAndSecretSeparation(t *testing.T) {
	p, secret := provisional(t)
	raw, err := p.EncodePrivate()
	if err != nil || !bytes.HasPrefix(raw, []byte(`{"warning":"NEVER SHARE THIS`)) || bytes.Contains(raw, []byte(secret.Encoded())) {
		t.Fatal("private format lost its warning or persisted the PSK")
	}
	decoded, err := DecodeProvisional(raw)
	if err != nil {
		t.Fatal(err)
	}
	first, err := p.Public()
	if err != nil {
		t.Fatal(err)
	}
	second, err := decoded.Public()
	if err != nil || !reflect.DeepEqual(first, second) {
		t.Fatal("private reload changed public keys or request metadata")
	}
	if samePublicKey(decoded.access.Public(), decoded.portable.Public()) || decoded.record.CreatedAt != provisionalNow {
		t.Fatal("keys or creation time not preserved separately")
	}
	next, nextSecret := provisional(t)
	if next.record.Selector == p.record.Selector || next.record.AccessKey == p.record.AccessKey || next.record.PortableKey == p.record.PortableKey || next.record.RecipientKey == p.record.RecipientKey || nextSecret == secret {
		t.Fatal("fresh request reused material")
	}
	publicJSON, err := json.Marshal(first)
	if err != nil {
		t.Fatal(err)
	}
	for _, private := range []string{secret.Encoded(), p.record.AccessKey, p.record.PortableKey, p.record.RecipientKey} {
		if bytes.Contains(publicJSON, []byte(private)) {
			t.Fatal("public advertisement contains private material")
		}
	}
	for _, value := range []any{p, *p, secret, &secret} {
		if _, err := json.Marshal(value); err == nil {
			t.Fatal("generic JSON silently exposed private material")
		}
		for _, rendered := range []string{fmt.Sprint(value), fmt.Sprintf("%+v", value), fmt.Sprintf("%#v", value)} {
			if !strings.Contains(rendered, "redacted") {
				t.Fatal("ordinary formatting did not redact private material")
			}
			for _, private := range []string{secret.Encoded(), p.record.AccessKey, p.record.PortableKey, p.record.RecipientKey} {
				if strings.Contains(rendered, private) {
					t.Fatal("ordinary formatting exposed private material")
				}
			}
		}
	}
	parsed, err := ParsePairingSecret(secret.Encoded())
	actualBytes, actualErr := parsed.Bytes()
	expectedBytes, expectedErr := secret.Bytes()
	if err != nil || actualErr != nil || expectedErr != nil || actualBytes != expectedBytes || len(secret.Encoded()) != 43 {
		t.Fatal("explicit 32-byte secret handoff changed")
	}
	var missing PairingSecret
	if _, err := missing.Bytes(); err == nil || missing.Encoded() != "" {
		t.Fatal("uninitialized secret silently supplied a predictable PSK")
	}
}

func TestRejectMalformedProvisionalJSON(t *testing.T) {
	p, _ := provisional(t)
	raw, _ := p.EncodePrivate()
	var record map[string]any
	if json.Unmarshal(raw, &record) != nil {
		t.Fatal("invalid test record")
	}
	wrongCurve, _ := ecdsa.GenerateKey(elliptic.P384(), rand.Reader)
	wrongDER, _ := x509.MarshalPKCS8PrivateKey(wrongCurve)
	keyDER, _ := base64.StdEncoding.DecodeString(p.record.AccessKey)
	for name, change := range map[string]func(map[string]any){
		"version":                   func(r map[string]any) { r["version"] = 2 },
		"warning":                   func(r map[string]any) { r["warning"] = "safe to share" },
		"secret":                    func(r map[string]any) { r["psk"] = "must not persist" },
		"agent-id":                  func(r map[string]any) { r["agent_id"] = "not-approved" },
		"case-alias":                func(r map[string]any) { r["Version"] = 1 },
		"missing-key":               func(r map[string]any) { delete(r, "access_key_pkcs8") },
		"null-key":                  func(r map[string]any) { r["access_key_pkcs8"] = nil },
		"same-key":                  func(r map[string]any) { r["portable_key_pkcs8"] = r["access_key_pkcs8"] },
		"wrong-signing-algorithm":   func(r map[string]any) { r["access_key_pkcs8"] = r["recipient_key_pkcs8"] },
		"wrong-recipient-algorithm": func(r map[string]any) { r["recipient_key_pkcs8"] = r["access_key_pkcs8"] },
		"wrong-curve":               func(r map[string]any) { r["access_key_pkcs8"] = base64.StdEncoding.EncodeToString(wrongDER) },
		"trailing-DER":              func(r map[string]any) { r["access_key_pkcs8"] = base64.StdEncoding.EncodeToString(append(keyDER, 0)) },
		"noncanonical-base64":       func(r map[string]any) { r["access_key_pkcs8"] = p.record.AccessKey + "\n" },
		"oversized-key":             func(r map[string]any) { r["access_key_pkcs8"] = strings.Repeat("A", 685) },
		"selector":                  func(r map[string]any) { r["selector"] = strings.Repeat("A", 21) + "B" },
		"empty-name":                func(r map[string]any) { r["name"] = "" },
		"name-control":              func(r map[string]any) { r["name"] = "agent\nforged" },
		"name-bound":                func(r map[string]any) { r["name"] = strings.Repeat("a", 257) },
		"zero-time":                 func(r map[string]any) { r["created_at"] = "0001-01-01T00:00:00Z" },
		"non-UTC-time":              func(r map[string]any) { r["created_at"] = "2026-10-22T00:00:00+01:00" },
		"URL-userinfo":              func(r map[string]any) { r["server_url"] = "https://user:password@localhost" },
		"URL-scheme":                func(r map[string]any) { r["server_url"] = "http://localhost" },
		"URL-query":                 func(r map[string]any) { r["server_url"] = "https://localhost/?token=secret" },
	} {
		t.Run(name, func(t *testing.T) {
			copy := make(map[string]any)
			for k, v := range record {
				copy[k] = v
			}
			change(copy)
			encoded, _ := json.Marshal(copy)
			if _, err := DecodeProvisional(encoded); err == nil || strings.Contains(err.Error(), p.record.AccessKey) {
				t.Fatal("invalid or secret-bearing record accepted or leaked")
			}
		})
	}
	for _, bad := range [][]byte{nil, []byte("null"), []byte("[]"), []byte("{"), append(raw, raw...), append([]byte(`{"version":1,`), raw[1:]...), bytes.Repeat([]byte(" "), MaxProvisionalBytes+1)} {
		if _, err := DecodeProvisional(bad); err == nil {
			t.Fatal("accepted incomplete, duplicate, trailing or oversized JSON")
		}
	}
	for _, bad := range []string{"", strings.Repeat("A", 42), strings.Repeat("A", 44), strings.Repeat("A", 42) + "B", strings.Repeat("!", 43)} {
		if _, err := ParsePairingSecret(bad); err == nil {
			t.Fatal("accepted noncanonical or wrong-size PSK")
		}
	}
	for _, p := range []*Provisional{nil, {}} {
		if _, err := p.EncodePrivate(); err == nil {
			t.Fatal("encoded uninitialized material")
		}
		if _, err := p.Public(); err == nil {
			t.Fatal("advertised uninitialized material")
		}
	}
}

func TestProvisionalMetadataAndExactSizeBounds(t *testing.T) {
	for _, input := range []struct {
		url, name string
		now       time.Time
	}{
		{"http://localhost", "agent", provisionalNow},
		{"https://localhost/#secret", "agent", provisionalNow},
		{"https://localhost", " agent", provisionalNow},
		{"https://localhost", string([]byte{0xff}), provisionalNow},
		{"https://localhost/" + strings.Repeat("a", 2048), "agent", provisionalNow},
		{"https://localhost", "agent", time.Time{}},
	} {
		if p, _, err := NewProvisional(input.url, input.name, input.now); err == nil || p != nil {
			t.Fatal("constructed invalid provisional metadata")
		}
	}
	p, _, err := NewProvisional("https://localhost", strings.Repeat("a", 256), provisionalNow)
	if err != nil {
		t.Fatal("rejected exact name boundary")
	}
	raw, _ := p.EncodePrivate()
	boundary := append(raw, bytes.Repeat([]byte(" "), MaxProvisionalBytes-len(raw))...)
	if _, err := DecodeProvisional(boundary); err != nil {
		t.Fatal("rejected exact record size boundary")
	}
	if _, err := DecodeProvisional(append(boundary, ' ')); err == nil {
		t.Fatal("accepted record above size boundary")
	}
	if _, err := DecodeProvisional(bytes.Replace(raw, []byte(`"name":"a`), []byte{'"', 'n', 'a', 'm', 'e', '"', ':', '"', 0xff}, 1)); err == nil {
		t.Fatal("accepted invalid UTF-8 input")
	}
}

// Python checks actual signatures and ECDH after the production private codec
// round-trip. Only public keys, signatures and a test confirmation leave Go.
func TestPythonProvisionalPeer(t *testing.T) {
	input := os.Getenv("ARBITER_TEST_PROVISIONAL_PEER")
	if input == "" {
		return
	}
	raw, err := base64.StdEncoding.DecodeString(input)
	if err != nil {
		t.Fatal("invalid test public key")
	}
	peer, err := ecdh.X25519().NewPublicKey(raw)
	if err != nil {
		t.Fatal(err)
	}
	p, _ := provisional(t)
	root := filepath.Join(custodyTestDir(t), "state")
	if err := SaveProvisional(context.Background(), root, p); err != nil {
		t.Fatal(err)
	}
	p, err = LoadProvisional(root, p.record.Selector)
	if err != nil {
		t.Fatal(err)
	}
	message := []byte("arbiter-provisional-peer-v1")
	digest := sha256.Sum256(message)
	access, err := ecdsa.SignASN1(rand.Reader, p.access, digest[:])
	if err != nil {
		t.Fatal(err)
	}
	portable, err := ecdsa.SignASN1(rand.Reader, p.portable, digest[:])
	if err != nil {
		t.Fatal(err)
	}
	shared, err := p.recipient.ECDH(peer)
	if err != nil {
		t.Fatal(err)
	}
	mac := hmac.New(sha256.New, shared)
	mac.Write(message)
	public, _ := p.Public()
	out, err := json.Marshal(map[string]any{"public": public, "access_signature": access, "portable_signature": portable, "recipient_proof": mac.Sum(nil)})
	if err != nil {
		t.Fatal(err)
	}
	fmt.Println("ARBITER_PUBLIC_PROVISIONAL_PEER=" + string(out))
}
