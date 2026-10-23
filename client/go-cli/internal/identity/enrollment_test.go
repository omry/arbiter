package identity

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

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
			err = PairEnrollment(ctx, p, secret, 100*time.Millisecond, func(context.Context, string, string) error { handoff++; return nil })
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
			err = PairEnrollment(ctx, p, secret, time.Second, func(context.Context, string, string) error {
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
		result <- PairEnrollment(ctx, p, secret, 5*time.Second, func(context.Context, string, string) error { return nil })
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
