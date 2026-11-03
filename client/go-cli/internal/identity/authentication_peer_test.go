package identity

import (
	"bytes"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// A bounded synthetic peer adapter can batch two TLS records or split records
// across WS messages. Production still uses its existing controlSocket.
type streamPeerSocket struct {
	*controlSocket
	batch, fragment bool
	pending         []byte
}

func (c *streamPeerSocket) Write(p []byte) (int, error) {
	if c.batch {
		if len(c.pending)+len(p) > 65536 {
			return 0, io.ErrShortBuffer
		}
		c.pending = append(c.pending, p...)
		return len(p), nil
	}
	if !c.fragment {
		return c.controlSocket.Write(p)
	}
	n := len(p)
	for len(p) > 0 {
		size := min(2049, len(p))
		if err := c.WriteMessage(websocket.BinaryMessage, p[:size]); err != nil {
			return n - len(p), err
		}
		p = p[size:]
	}
	return n, nil
}

func TestPythonAuthenticationStreamPeer(t *testing.T) {
	path := os.Getenv("ARBITER_TEST_AUTH_STREAM")
	if path == "" {
		t.Skip("private Python stream peer only")
	}
	var fixture struct {
		URL, ServerID string
		Authority     []byte
		Fragment      bool
	}
	raw, err := os.ReadFile(path)
	if err != nil || json.Unmarshal(raw, &fixture) != nil {
		t.Fatal("invalid synthetic peer fixture")
	}
	dialer := websocket.Dialer{HandshakeTimeout: 5 * time.Second,
		TLSClientConfig: &tls.Config{InsecureSkipVerify: true}}
	ws, _, err := dialer.Dial(fixture.URL, nil)
	if err != nil {
		t.Fatal(err)
	}
	defer ws.Close()
	socket := &streamPeerSocket{controlSocket: &controlSocket{Conn: ws}, fragment: fixture.Fragment}
	if err := socket.SetDeadline(time.Now().Add(8 * time.Second)); err != nil {
		t.Fatal(err)
	}
	conn := tls.Client(socket, &tls.Config{MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13,
		NextProtos: []string{controlALPN}, SessionTicketsDisabled: true, InsecureSkipVerify: true,
		VerifyConnection: func(state tls.ConnectionState) error {
			if state.Version != tls.VersionTLS13 || state.NegotiatedProtocol != controlALPN ||
				state.DidResume || len(state.PeerCertificates) != 1 {
				return errControl
			}
			_, err := VerifyCredential(state.PeerCertificates[0].Raw,
				Identity{Role: ServerTLS, SubjectID: fixture.ServerID},
				&Issuer{DER: fixture.Authority, ID: fixture.ServerID}, time.Now().UTC())
			return err
		}})
	if err := conn.Handshake(); err != nil {
		t.Fatal(err)
	}
	payload := bytes.Repeat([]byte("bounded-stream!!"), 2048) // 32 KiB / two chunks.
	for round := range 16 {
		payload[0] = byte(round)
		socket.batch = !fixture.Fragment
		for offset := 0; offset < len(payload); offset += 16384 {
			if _, err := conn.Write(payload[offset : offset+16384]); err != nil {
				t.Fatal(err)
			}
		}
		if socket.batch {
			if err := ws.WriteMessage(websocket.BinaryMessage, socket.pending); err != nil {
				t.Fatal(err)
			}
			socket.pending = nil
		}
		got := make([]byte, len(payload))
		if _, err := io.ReadFull(conn, got); err != nil || !bytes.Equal(got, payload) {
			t.Fatalf("stream round %d: %v", round, err)
		}
	}
	fmt.Println("ARBITER_STREAM_PEER=pass")
}
