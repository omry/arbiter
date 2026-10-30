package identity

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// Only the synthetic Python integration harness sets this loopback endpoint.
// No product enrollment command or unverified TLS policy is introduced here.
func TestPythonRegistrationPeer(t *testing.T) {
	endpoint := os.Getenv("ARBITER_TEST_REGISTRATION_URL")
	if endpoint == "" {
		t.Skip("Python registration peer is not running")
	}
	u, err := url.Parse(endpoint)
	if err != nil || u.Scheme != "http" || u.Hostname() != "127.0.0.1" || u.User != nil {
		t.Fatal("registration fixture must use loopback HTTP")
	}
	client := &http.Client{Timeout: 5 * time.Second, Transport: &http.Transport{},
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	defer client.CloseIdleConnections()
	root := filepath.Join(custodyTestDir(t), "state")
	err = RunProvisional(context.Background(), root, "https://localhost", "Agent Smith",
		func(ctx context.Context, p *Provisional, _ PairingSecret) error {
			advertisement, err := p.Public()
			if err != nil {
				return err
			}
			public, err := json.Marshal(advertisement)
			if err != nil {
				return err
			}
			request, err := http.NewRequestWithContext(ctx, http.MethodPost, endpoint, bytes.NewReader(public))
			if err != nil {
				return err
			}
			response, err := client.Do(request)
			if err != nil {
				return err
			}
			defer response.Body.Close()
			raw, err := io.ReadAll(io.LimitReader(response.Body, 4097))
			if err != nil || len(raw) > 4096 || response.StatusCode != http.StatusOK {
				return fmt.Errorf("registration fixture rejected advertisement")
			}
			var result struct{ Selector, RequestDigest, Status string }
			var reply map[string]string
			if json.Unmarshal(raw, &reply) != nil {
				return fmt.Errorf("invalid registration fixture response")
			}
			result.Selector, result.RequestDigest, result.Status = reply["selector"], reply["request_digest"], reply["status"]
			digest := sha256.Sum256(public)
			if len(reply) != 3 || result.Selector != p.record.Selector || result.RequestDigest != hex.EncodeToString(digest[:]) || result.Status != "pending" {
				return fmt.Errorf("registration fixture response mismatch")
			}
			fmt.Println("ARBITER_PUBLIC_REGISTRATION=" + string(public))
			return nil
		})
	if err != nil {
		t.Fatal("Go registration peer failed", err)
	}
	assertOnlyProvisionalLock(t, root)
}

// Real A/B servers supply the production HTTP/Noise/TLS exchange. Only local
// approval and controlled shutdown use this bounded synthetic handoff socket.
func TestPythonPortableEnrollmentPeer(t *testing.T) {
	endpoint := os.Getenv("ARBITER_TEST_PORTABLE_ENROLLMENT_CONTROL")
	if endpoint == "" {
		t.Skip("Python portable enrollment peer is not running")
	}
	host, _, err := net.SplitHostPort(endpoint)
	if err != nil || host != "127.0.0.1" {
		t.Fatal("loopback required")
	}
	conn, err := net.DialTimeout("tcp", endpoint, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(40 * time.Second))
	input, output := json.NewDecoder(io.LimitReader(conn, 32768)), json.NewEncoder(conn)
	var config struct{ A, B, Mode string }
	if input.Decode(&config) != nil {
		t.Fatal("fixture configuration")
	}
	ctx := context.Background()
	root := filepath.Join(custodyTestDir(t), "state")
	err = WithEnrollmentOwner(ctx, root, func() error {
		original, secret, e := NewProvisional(config.A, "source", time.Now())
		if e != nil {
			return e
		}
		var first *EnrollmentReceipt
		var sourceBytes []byte
		for phase := 0; phase < 2; phase++ {
			material := original
			if phase == 1 {
				source, e := LoadPortableIdentity(root, original.record.Selector, time.Now())
				if e != nil {
					return e
				}
				material, secret, e = NewProvisionalFromIdentity(config.B, "target", time.Now(), source)
				if e != nil {
					return e
				}
				if samePublicKey(material.access.Public(), original.access.Public()) || bytes.Equal(material.recipient.Bytes(), original.recipient.Bytes()) {
					return fmt.Errorf("target keys reused")
				}
			}
			e = PairEnrollment(ctx, material, secret, 5*time.Second, filepath.Join(root, "prepared"), func(_ context.Context, selector, secret string) error {
				if output.Encode(map[string]any{"phase": phase, "selector": selector, "secret": secret}) != nil {
					return fmt.Errorf("handoff")
				}
				var accepted bool
				if input.Decode(&accepted) != nil || !accepted {
					return fmt.Errorf("approval")
				}
				return nil
			})
			if phase == 0 && e != nil || phase == 1 && (e == nil) != (config.Mode == "ok") {
				return fmt.Errorf("pairing outcome: %v", e)
			}
			if phase == 1 {
				if config.Mode == "changed-portable" {
					if _, err := os.Stat(filepath.Join(root, "prepared", material.record.Selector+".json")); !os.IsNotExist(err) {
						return fmt.Errorf("changed portable identity reached ready")
					}
					if output.Encode("rejected") != nil {
						return fmt.Errorf("rejection handoff")
					}
					var rejected bool
					if input.Decode(&rejected) != nil || !rejected {
						return fmt.Errorf("rejection acknowledgement")
					}
					return nil
				}
				if output.Encode("prepared") != nil {
					return fmt.Errorf("restart handoff")
				}
				var restarted bool
				if input.Decode(&restarted) != nil || !restarted {
					return fmt.Errorf("restart")
				}
			}
			receipt, e := ReconcileEnrollment(ctx, filepath.Join(root, "prepared"), material.record.Selector, 5*time.Second)
			if e != nil {
				return e
			}
			if phase == 0 {
				first = receipt
			} else {
				if receipt.prepared.AgentID != first.prepared.AgentID || receipt.prepared.OwnerID != first.prepared.OwnerID || receipt.prepared.ServerID == first.prepared.ServerID || receipt.prepared.EnrollmentID == first.prepared.EnrollmentID {
					return fmt.Errorf("portable/target IDs")
				}
				again, e := ReconcileEnrollment(ctx, filepath.Join(root, "prepared"), material.record.Selector, 5*time.Second)
				if e != nil || !bytes.Equal(again.prepared.raw, receipt.prepared.raw) {
					return fmt.Errorf("exact recovery")
				}
			}
			if e = receipt.Promote(ctx, filepath.Join(root, "enrolled")); e != nil {
				return e
			}
			if phase == 0 {
				sourceBytes, e = os.ReadFile(filepath.Join(root, "enrolled", original.record.Selector+".json"))
				if e != nil {
					return e
				}
				if output.Encode("source-ready") != nil {
					return fmt.Errorf("source handoff")
				}
				var offline bool
				if input.Decode(&offline) != nil || !offline {
					return fmt.Errorf("source shutdown")
				}
			}
		}
		after, e := os.ReadFile(filepath.Join(root, "enrolled", original.record.Selector+".json"))
		if e != nil || !bytes.Equal(after, sourceBytes) {
			return fmt.Errorf("source custody changed")
		}
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	fmt.Println("ARBITER_PORTABLE_ENROLLMENT=pass")
}
