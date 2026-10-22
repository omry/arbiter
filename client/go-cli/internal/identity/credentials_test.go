package identity

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

type credentialCase struct {
	Name        string
	Certificate string
	Role        Role
	IDs         []string
	Issuer      string
	IssuerID    string `json:"issuer_id"`
	Valid       bool
}

type credentialVectors struct {
	Schema       string
	TestOnly     bool `json:"test_only"`
	Now          time.Time
	Certificates map[string][]byte
	Cases        []credentialCase
}

func vectors(t *testing.T) credentialVectors {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("..", "..", "..", "..", "tests", "fixtures", "authentication", "credential-profiles-v1.json"))
	if err != nil {
		t.Fatal(err)
	}
	var v credentialVectors
	if err := json.Unmarshal(data, &v); err != nil || v.Schema != "arbiter-credential-profiles-v1" || !v.TestOnly {
		t.Fatalf("invalid shared fixture: %v", err)
	}
	return v
}

func runVectors(t *testing.T, v credentialVectors) {
	t.Helper()
	if len(v.Cases) < 10 {
		t.Fatal("credential peer requires complete positive and negative cases")
	}
	for _, c := range v.Cases {
		t.Run(c.Name, func(t *testing.T) {
			id := Identity{Role: c.Role, SubjectID: c.IDs[0]}
			if len(c.IDs) == 3 {
				id.ServerID, id.EnrollmentID = c.IDs[1], c.IDs[2]
			}
			var issuer *Issuer
			if c.Issuer != "" {
				issuer = &Issuer{DER: v.Certificates[c.Issuer], ID: c.IssuerID}
			}
			_, err := VerifyCredential(v.Certificates[c.Certificate], id, issuer, v.Now)
			if (err == nil) != c.Valid {
				t.Fatalf("valid=%v, got %v", c.Valid, err)
			}
		})
	}
}

func TestSharedPythonCredentialProfiles(t *testing.T) { runVectors(t, vectors(t)) }

// The Python integration test feeds freshly issued, public-only certificates to
// this executable peer. This is test input, never a product trust initializer.
func TestPythonCredentialPeer(t *testing.T) {
	path := os.Getenv("ARBITER_TEST_CREDENTIAL_PEER")
	if path == "" {
		return
	}
	data, err := os.ReadFile(path)
	if err != nil || len(data) > 256*1024 {
		t.Fatalf("invalid public peer fixture: %v", err)
	}
	var v credentialVectors
	if err := json.Unmarshal(data, &v); err != nil {
		t.Fatal(err)
	}
	runVectors(t, v)
	t.Logf("verified %d live Python credential cases", len(v.Cases))
}

func TestIdentityTrustAndTimeFailures(t *testing.T) {
	v := vectors(t)
	agent, server, enrollment := v.Cases[3].IDs[0], v.Cases[3].IDs[1], v.Cases[3].IDs[2]
	id := Identity{Role: Access, SubjectID: agent, ServerID: server, EnrollmentID: enrollment}
	issuer := &Issuer{DER: v.Certificates["server-authority"], ID: server}
	for _, changed := range []Identity{
		{Role: "unknown", SubjectID: agent}, {Role: Access, SubjectID: strings.ToUpper(agent + "a")},
		{Role: Access, SubjectID: agent, ServerID: server},
		{Role: Access, SubjectID: agent, ServerID: server, EnrollmentID: agent},
		{Role: Portable, SubjectID: agent, ServerID: server},
		{Role: Access, SubjectID: strings.Replace(agent, "4000", "5000", 1), ServerID: server, EnrollmentID: enrollment},
	} {
		if _, err := VerifyCredential(v.Certificates["access"], changed, issuer, v.Now); err == nil {
			t.Fatalf("accepted bad expected identity: %+v", changed)
		}
	}
	for _, der := range [][]byte{nil, make([]byte, MaxCertificateBytes+1), []byte("malformed")} {
		if _, err := VerifyCredential(der, id, issuer, v.Now); err == nil {
			t.Fatal("accepted malformed/oversized certificate")
		}
	}
	for _, root := range []*Issuer{nil, {DER: []byte("bad"), ID: server}, {DER: issuer.DER, ID: agent}} {
		if _, err := VerifyCredential(v.Certificates["access"], id, root, v.Now); err == nil {
			t.Fatal("accepted missing or mismatched registered issuer")
		}
	}
	if _, err := VerifyCredential(issuer.DER, Identity{Role: ServerAuthority, SubjectID: server}, issuer, v.Now); err == nil {
		t.Fatal("accepted issuer parameter for self-signed root")
	}
	cert, err := VerifyCredential(v.Certificates["access"], id, issuer, v.Now)
	if err != nil {
		t.Fatal(err)
	}
	for _, when := range []time.Time{cert.NotBefore, cert.NotAfter} {
		if _, err := VerifyCredential(cert.Raw, id, issuer, when); err != nil {
			t.Fatalf("rejected inclusive validity boundary: %v", err)
		}
	}
	for _, when := range []time.Time{cert.NotBefore.Add(-time.Nanosecond), cert.NotAfter.Add(time.Nanosecond), {}} {
		if _, err := VerifyCredential(cert.Raw, id, issuer, when); err == nil {
			t.Fatal("accepted time outside validity")
		}
	}
}
