package identity

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

func TestEnrollmentSourceValidationSharesOwnerAndPrecedesCleanup(t *testing.T) {
	for _, mode := range []string{"fresh", "reuse", "reuse-other-host", "reuse-other-port", "reuse-other-prefix", "reuse-other-ipv6", "reuse-other-ipv6-zone", "missing", "prepared-only", "expired", "same-server", "same-server-trailing-slash", "same-server-prefix", "same-server-host-case", "same-server-default-port", "same-server-source-default-port", "same-server-padded-default-port", "same-server-padded-source-port", "same-server-padded-nondefault-port", "same-server-ipv6", "same-server-mapped-ipv4"} {
		t.Run(mode, func(t *testing.T) {
			state := custodyTestDir(t)
			age := time.Duration(0)
			if mode == "expired" {
				age = 366 * 24 * time.Hour
			}
			original, raw, _ := portableFixture(t, age, 2*365*24*time.Hour)
			sourceURL, target := "https://source.invalid", "https://target.invalid"
			sameServer := false
			switch mode {
			case "same-server":
				target, sameServer = sourceURL, true
			case "same-server-trailing-slash":
				target, sameServer = sourceURL+"/", true
			case "same-server-prefix":
				sourceURL, target, sameServer = sourceURL+"/tenant/", sourceURL+"/tenant", true
			case "same-server-host-case":
				target, sameServer = "https://SOURCE.invalid", true
			case "same-server-default-port":
				target, sameServer = sourceURL+":443", true
			case "same-server-source-default-port":
				sourceURL, target, sameServer = sourceURL+":443", sourceURL, true
			case "same-server-padded-default-port":
				target, sameServer = sourceURL+":0443", true
			case "same-server-padded-source-port":
				sourceURL, target, sameServer = sourceURL+":00443", sourceURL, true
			case "same-server-padded-nondefault-port":
				sourceURL, target, sameServer = sourceURL+":444", sourceURL+":00444", true
			case "same-server-ipv6":
				sourceURL, target, sameServer = "https://[2001:db8::1]", "https://[2001:0db8:0:0:0:0:0:1]", true
			case "same-server-mapped-ipv4":
				sourceURL, target, sameServer = "https://127.0.0.1", "https://[::ffff:127.0.0.1]", true
			case "reuse-other-host":
				target = "https://other.invalid"
			case "reuse-other-port":
				target = sourceURL + ":444"
			case "reuse-other-prefix":
				target = sourceURL + "/tenant"
			case "reuse-other-ipv6":
				sourceURL, target = "https://[2001:db8::1]", "https://[2001:db8::2]"
			case "reuse-other-ipv6-zone":
				sourceURL, target = "https://[fe80::1%25zoneA]", "https://[fe80::1%25zonea]"
			}
			original.record.ServerURL = sourceURL
			domain := "enrolled"
			if mode == "prepared-only" {
				domain = "prepared"
			}
			before := savePortableFixture(t, original, raw, state, domain)
			directory := filepath.Join(state, "provisional")
			orphan, _, err := NewProvisional("https://abandoned.invalid", "abandoned", time.Now())
			if err != nil {
				t.Fatal(err)
			}
			if err = SaveProvisional(context.Background(), directory, orphan); err != nil {
				t.Fatal(err)
			}
			orphanPath := filepath.Join(directory, orphan.record.Selector+".json")
			orphanBefore, err := os.ReadFile(orphanPath)
			if err != nil {
				t.Fatal(err)
			}
			selector := original.record.Selector
			if mode == "fresh" {
				selector = ""
			}
			if mode == "missing" {
				selector = orphan.record.Selector
			}
			called := false
			err = RunEnrollment(context.Background(), state, target, "target", selector,
				func(ctx context.Context, p *Provisional, _ PairingSecret) error {
					called = true
					if p.record.Version != 2 {
						t.Fatal("new public enrollment is not v2")
					}
					if _, e := os.Stat(orphanPath); !errors.Is(e, os.ErrNotExist) {
						t.Fatal("abandoned request was not cleaned", e)
					}
					if e := CleanupProvisional(ctx, directory); !errors.Is(e, ErrProvisionalBusy) {
						t.Fatal("source/exchange lost shared owner", e)
					}
					if strings.HasPrefix(mode, "reuse") && (!samePublicKey(p.portable.Public(), original.portable.Public()) || samePublicKey(p.access.Public(), original.access.Public()) || bytes.Equal(p.recipient.Bytes(), original.recipient.Bytes())) {
						t.Fatal("incorrect source or target keys")
					}
					if _, e := LoadProvisional(directory, p.record.Selector); e != nil {
						t.Fatal("new material not saved before exchange", e)
					}
					return nil
				})
			valid := mode == "fresh" || strings.HasPrefix(mode, "reuse")
			if sameServer {
				valid = false
			}
			if called != valid || valid && err != nil || !valid && !errors.Is(err, ErrEnrollmentSource) {
				t.Fatal("source validation outcome", err)
			}
			if !valid {
				after, e := os.ReadFile(orphanPath)
				if e != nil || !bytes.Equal(after, orphanBefore) {
					t.Fatal("invalid source cleaned existing state", e)
				}
			}
			after, e := os.ReadFile(filepath.Join(state, domain, original.record.Selector+".json"))
			if e != nil || !bytes.Equal(after, before) {
				t.Fatal("source state changed", e)
			}
		})
	}
}

func TestEnrollmentRegistrationAndCarrierBounds(t *testing.T) {
	for _, mode := range []string{"closed", "redirect", "digest", "oversize", "header", "text", "fragment", "controls", "deadline", "cancel"} {
		t.Run(mode, func(t *testing.T) {
			handoff, upgrades := 0, 0
			handlerStarted, handlerDone := make(chan struct{}), make(chan struct{})
			ctx, cancel := context.WithCancel(context.Background())
			defer cancel()
			u := websocket.Upgrader{ReadBufferSize: 1024, WriteBufferSize: 1024}
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Header.Get("Authorization") != "" || r.Header.Get("Cookie") != "" || r.Header.Get("Proxy-Authorization") != "" {
					t.Error("inherited credential")
				}
				if r.Method == http.MethodPost {
					b, _ := io.ReadAll(r.Body)
					digest := sha256.Sum256(b)
					switch mode {
					case "closed":
						w.WriteHeader(403)
						return
					case "redirect":
						w.Header().Set("Location", "/redirected")
						w.WriteHeader(307)
						return
					case "oversize":
						fmt.Fprint(w, strings.Repeat("x", 65537))
						return
					case "header":
						w.Header().Set("X-Fill", strings.Repeat("x", 33000))
						return
					case "digest":
						digest[0] ^= 1
					}
					// Obtain the exact selector without logging the advertisement.
					selector := strings.Split(strings.Split(string(b), `"selector":"`)[1], `"`)[0]
					fmt.Fprintf(w, `{"selector":%q,"request_digest":%q,"status":"pending"}`, selector, hex.EncodeToString(digest[:]))
					return
				}
				upgrades++
				ws, err := u.Upgrade(w, r, nil)
				if err != nil {
					return
				}
				close(handlerStarted)
				defer close(handlerDone)
				defer ws.Close()
				if _, _, err := ws.ReadMessage(); err != nil {
					return
				}
				switch mode {
				case "text":
					ws.WriteMessage(websocket.TextMessage, []byte("untrusted"))
				case "fragment":
					writer, _ := ws.NextWriter(websocket.BinaryMessage)
					writer.Write(make([]byte, MaxPairingFrame+1))
					writer.Close()
				case "controls":
					for i := 0; i < 33; i++ {
						ws.WriteControl(websocket.PongMessage, []byte("ping"), time.Now().Add(time.Second))
					}
				case "cancel":
					cancel()
					if err := ws.SetReadDeadline(time.Now().Add(time.Second)); err != nil {
						t.Errorf("set cancellation read deadline: %v", err)
					}
					if _, _, err := ws.ReadMessage(); err == nil {
						t.Error("client cancellation did not close peer socket")
					} else if netErr, ok := err.(net.Error); ok && netErr.Timeout() {
						t.Errorf("client cancellation did not close peer socket: %v", err)
					}
				case "deadline":
					time.Sleep(200 * time.Millisecond)
				}
			}))
			defer server.Close()
			p, secret, err := NewProvisional(server.URL, "test", time.Now())
			if err != nil {
				t.Fatal(err)
			}
			t.Setenv("HTTPS_PROXY", "http://127.0.0.1:1")
			err = PairEnrollment(ctx, p, secret, 100*time.Millisecond, t.TempDir(), func(context.Context, string, string) error { handoff++; return nil })
			if err == nil {
				t.Fatal("invalid peer accepted")
			}
			if mode == "closed" && err != ErrEnrollmentClosed {
				t.Fatal(err)
			}
			want := 1
			if mode == "closed" || mode == "redirect" || mode == "digest" || mode == "oversize" || mode == "header" {
				want = 0
			}
			if handoff != want || upgrades != want {
				t.Fatalf("handoff=%d upgrades=%d wanted=%d", handoff, upgrades, want)
			}
			if want == 1 {
				select {
				case <-handlerStarted:
				case <-time.After(time.Second):
					t.Fatal("upgraded handler did not start")
				}
				select {
				case <-handlerDone:
				case <-time.After(time.Second):
					t.Fatal("upgraded handler did not finish")
				}
			}
		})
	}
}

func TestEnrollmentRegistrationRejectsDuplicateFields(t *testing.T) {
	tests := []struct {
		name        string
		field       string
		conflicting bool
	}{
		{name: "selector-identical", field: "selector"},
		{name: "selector-conflicting", field: "selector", conflicting: true},
		{name: "digest-identical", field: "digest"},
		{name: "digest-conflicting", field: "digest", conflicting: true},
		{name: "status-identical", field: "status"},
		{name: "status-conflicting", field: "status", conflicting: true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			handoff, upgrades := 0, 0
			u := websocket.Upgrader{ReadBufferSize: 1024, WriteBufferSize: 1024}
			server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if r.Method == http.MethodPost {
					body, _ := io.ReadAll(r.Body)
					var ad struct {
						Selector string `json:"selector"`
					}
					if json.Unmarshal(body, &ad) != nil {
						t.Error("invalid advertisement")
						return
					}
					digest := sha256.Sum256(body)
					digestValue := hex.EncodeToString(digest[:])
					selectorValue, statusValue := ad.Selector, "pending"
					if tc.conflicting {
						switch tc.field {
						case "selector":
							selectorValue = "different-selector"
						case "digest":
							digestValue = strings.Repeat("0", len(digestValue))
						case "status":
							statusValue = "rejected"
						}
					}
					switch tc.field {
					case "selector":
						fmt.Fprintf(w, `{"selector":%q,"\u0073elector":%q,"request_digest":%q,"status":%q}`, selectorValue, ad.Selector, digestValue, statusValue)
					case "digest":
						fmt.Fprintf(w, `{"selector":%q,"request_digest":%q,"request_\u0064igest":%q,"status":%q}`, ad.Selector, digestValue, hex.EncodeToString(digest[:]), statusValue)
					case "status":
						fmt.Fprintf(w, `{"selector":%q,"request_digest":%q,"status":%q,"\u0073tatus":%q}`, ad.Selector, hex.EncodeToString(digest[:]), statusValue, "pending")
					}
					return
				}
				ws, err := u.Upgrade(w, r, nil)
				if err != nil {
					return
				}
				upgrades++
				ws.Close()
			}))
			defer server.Close()
			p, secret, err := NewProvisional(server.URL, "test", time.Now())
			if err != nil {
				t.Fatal(err)
			}
			t.Setenv("HTTPS_PROXY", "http://127.0.0.1:1")
			ctx, cancel := context.WithTimeout(context.Background(), time.Second)
			defer cancel()
			err = PairEnrollment(ctx, p, secret, time.Second, t.TempDir(), func(context.Context, string, string) error {
				handoff++
				return nil
			})
			if err != ErrEnrollmentUnavailable {
				t.Fatalf("error=%v, wanted %v", err, ErrEnrollmentUnavailable)
			}
			if handoff != 0 || upgrades != 0 {
				t.Fatalf("handoff=%d upgrades=%d, wanted zero", handoff, upgrades)
			}
		})
	}
}

func TestPairingWireAccounting(t *testing.T) {
	a, b := net.Pipe()
	defer a.Close()
	defer b.Close()
	c := &pairingConn{Conn: a, read: 32768, headers: true}
	if _, err := c.Read(make([]byte, 1)); err == nil {
		t.Fatal("header budget bypass")
	}
	c.headers = false
	c.read = MaxPairingBytes
	if _, err := c.Read(make([]byte, 1)); err == nil {
		t.Fatal("wire budget bypass")
	}
	c.written = MaxPairingBytes
	if _, err := c.Write([]byte{1}); err == nil {
		t.Fatal("write budget bypass")
	}
}

func TestEnrollmentCancellationWakesStalledUpgrade(t *testing.T) {
	entered, release := make(chan struct{}), make(chan struct{})
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Method == http.MethodPost {
			body, _ := io.ReadAll(r.Body)
			var ad ProvisionalPublic
			if json.Unmarshal(body, &ad) != nil {
				t.Error("invalid advertisement")
				return
			}
			digest := sha256.Sum256(body)
			fmt.Fprintf(w, `{"selector":%q,"request_digest":%q,"status":"pending"}`, ad.Selector, hex.EncodeToString(digest[:]))
			return
		}
		close(entered)
		<-release // TLS is complete, but HTTP upgrade has no response.
	}))
	defer server.Close()
	defer close(release)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	p, secret, err := NewProvisional(server.URL, "test", time.Now())
	if err != nil {
		t.Fatal(err)
	}
	result := make(chan error, 1)
	go func() {
		result <- PairEnrollment(ctx, p, secret, 5*time.Second, t.TempDir(), func(context.Context, string, string) error { return nil })
	}()
	select {
	case <-entered:
	case <-time.After(10 * time.Second):
		t.Fatal("upgrade was not reached")
	}
	cancel()
	select {
	case err := <-result:
		if err == nil {
			t.Fatal("cancelled exchange accepted")
		}
	case <-time.After(time.Second):
		<-result // Settle the actual operation before releasing the server.
		t.Fatal("cancelled upgrade did not wake; waited for connect deadline")
	}
}
