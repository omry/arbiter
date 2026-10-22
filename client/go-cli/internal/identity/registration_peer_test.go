package identity

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
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
