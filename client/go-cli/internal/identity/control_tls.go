package identity

import (
	"bytes"
	"context"
	"crypto/tls"
	"encoding/binary"
	"errors"
	"io"
	"net"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gorilla/websocket"
)

const controlPath = "/api/v1/native/control"
const controlALPN = "arbiter-control-v1"
const maxControl = 65536

var errControl = errors.New("authenticated server control is unavailable or invalid")

// controlSocket is an untrusted byte carrier. Only TLS exposes a usable stream.
// One reader/writer, no read-ahead, compression, redirects or inherited headers.
type controlSocket struct {
	*websocket.Conn
	reader io.Reader
}

func (c *controlSocket) Read(p []byte) (int, error) {
	for {
		if c.reader == nil {
			typ, reader, err := c.NextReader()
			if err != nil || typ != websocket.BinaryMessage {
				return 0, errControl
			}
			c.reader = reader
		}
		n, err := c.reader.Read(p)
		if err == io.EOF {
			c.reader = nil
			if n > 0 {
				return n, nil
			}
			continue
		}
		return n, err
	}
}
func (c *controlSocket) Write(p []byte) (int, error) {
	written := 0
	for len(p) > 0 {
		n := min(len(p), 16384)
		if c.WriteMessage(websocket.BinaryMessage, p[:n]) != nil {
			return written, errControl
		}
		written += n
		p = p[n:]
	}
	return written, nil
}
func (c *controlSocket) LocalAddr() net.Addr  { return c.UnderlyingConn().LocalAddr() }
func (c *controlSocket) RemoteAddr() net.Addr { return c.UnderlyingConn().RemoteAddr() }
func (c *controlSocket) SetDeadline(t time.Time) error {
	if err := c.SetReadDeadline(t); err != nil {
		return err
	}
	return c.SetWriteDeadline(t)
}

// controlHandshakeConn bounds unverified TLS input before certificate parsing.
func controlHandshakeConn(conn net.Conn) *pairingConn {
	return &pairingConn{Conn: conn, headers: true}
}

// controlAbortConn closes the raw carrier before its TLS wrapper. Gorilla's
// failed-upgrade cleanup calls Close on the returned net.Conn directly.
type controlAbortConn struct {
	net.Conn
	abort func()
}

func (c *controlAbortConn) Close() error {
	if c.abort != nil {
		c.abort()
	}
	return c.Conn.Close()
}

// ControlStream authenticates remembered server trust, never an outer proxy.
// It permits one bounded request/reply; receipt semantics belong to its caller.
type ControlStream struct {
	ctx      context.Context
	tls      *tls.Conn
	socket   *controlSocket
	abort    func()
	cancel   context.CancelFunc
	stop     func() bool
	done     chan struct{}
	once     sync.Once
	timer    *time.Timer
	exporter []byte
	used     atomic.Bool
}

func DialControl(ctx context.Context, serverURL string, trust PairingServer, timeout time.Duration) (*ControlStream, error) {
	if timeout <= 0 || timeout > 10*time.Second {
		return nil, errControl
	}
	trust.AuthorityDER = bytes.Clone(trust.AuthorityDER)
	target, err := enrollmentURL(serverURL, controlPath)
	if err != nil {
		return nil, errControl
	}
	target.Scheme = "wss"
	lifetime, cancel := context.WithCancel(ctx)
	connect, endConnect := context.WithTimeout(lifetime, timeout)
	defer endConnect()
	s := &ControlStream{ctx: lifetime, cancel: cancel, done: make(chan struct{})}
	var bounded *pairingConn
	dialer := websocket.Dialer{ReadBufferSize: 4096, WriteBufferSize: 4096,
		NetDialTLSContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			raw, err := (&net.Dialer{}).DialContext(ctx, network, address)
			if err != nil {
				return nil, err
			}
			deadline, _ := ctx.Deadline()
			if err := raw.SetDeadline(deadline); err != nil {
				raw.Close()
				return nil, err
			}
			outer := controlHandshakeConn(raw)
			conn := tls.Client(outer, &tls.Config{MinVersion: tls.VersionTLS12,
				InsecureSkipVerify: true, ServerName: target.Hostname()})
			if err := conn.HandshakeContext(ctx); err != nil {
				raw.Close()
				return nil, err
			}
			outer.headers = false
			bounded = &pairingConn{Conn: conn, headers: true}
			abort := func() { _ = raw.Close() }
			s.abort = abort
			s.stop = context.AfterFunc(lifetime, func() {
				defer close(s.done)
				raw.Close()
			})
			return &controlAbortConn{Conn: bounded, abort: abort}, nil
		}}
	ws, response, err := dialer.DialContext(connect, target.String(), nil)
	if err != nil {
		if response != nil {
			response.Body.Close()
		}
		s.Close()
		return nil, errControl
	}
	s.socket = &controlSocket{Conn: ws}
	if response.Header.Get("Sec-WebSocket-Extensions") != "" || ws.Subprotocol() != "" {
		s.Close()
		return nil, errControl
	}
	bounded.headers = false
	ws.SetReadLimit(maxControl)
	started, count, size := time.Now(), 0, 0
	pingDeadline, _ := connect.Deadline()
	control := func(data string) error {
		if time.Since(started) >= time.Second {
			started, count, size = time.Now(), 0, 0
		}
		count++
		size += len(data) + 6
		if count > 32 || size > 4096 {
			return errControl
		}
		return nil
	}
	ws.SetPingHandler(func(data string) error {
		if err := control(data); err != nil {
			return err
		}
		return ws.WriteControl(websocket.PongMessage, []byte(data), pingDeadline)
	})
	ws.SetPongHandler(control)
	config := &tls.Config{MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13,
		NextProtos: []string{controlALPN}, InsecureSkipVerify: true, SessionTicketsDisabled: true,
		VerifyConnection: func(state tls.ConnectionState) error {
			if state.Version != tls.VersionTLS13 || state.NegotiatedProtocol != controlALPN ||
				state.DidResume || len(state.PeerCertificates) != 1 || len(state.PeerCertificates[0].Raw) > 32768 {
				return errControl
			}
			_, err := VerifyCredential(state.PeerCertificates[0].Raw,
				Identity{Role: ServerTLS, SubjectID: trust.ServerID},
				&Issuer{DER: trust.AuthorityDER, ID: trust.ServerID}, time.Now().UTC())
			return err
		}}
	inner := controlHandshakeConn(s.socket)
	s.tls = tls.Client(inner, config)
	if err := s.tls.HandshakeContext(connect); err != nil {
		s.Close()
		return nil, errControl
	}
	inner.headers = false
	state := s.tls.ConnectionState()
	s.exporter, err = state.ExportKeyingMaterial("EXPORTER-Channel-Binding", nil, 32)
	if err != nil {
		s.Close()
		return nil, errControl
	}
	deadline := time.Now().Add(10 * time.Second)
	if caller, ok := lifetime.Deadline(); ok && caller.Before(deadline) {
		deadline = caller
	}
	if bounded.Conn.SetDeadline(deadline) != nil || s.socket.SetDeadline(deadline) != nil || lifetime.Err() != nil {
		s.Close()
		return nil, errControl
	}
	pingDeadline = deadline
	s.timer = time.AfterFunc(time.Until(deadline), cancel)
	return s, nil
}

func (s *ControlStream) Exporter() []byte { return bytes.Clone(s.exporter) }

func (s *ControlStream) Exchange(message []byte) ([]byte, error) {
	if len(message) == 0 || len(message) > maxControl || !s.used.CompareAndSwap(false, true) {
		return nil, errControl
	}
	defer s.Close()
	if s.ctx.Err() != nil {
		return nil, errControl
	}
	var header [4]byte
	binary.BigEndian.PutUint32(header[:], uint32(len(message)))
	if _, err := s.tls.Write(append(header[:], message...)); err != nil {
		return nil, errControl
	}
	if _, err := io.ReadFull(s.tls, header[:]); err != nil {
		return nil, errControl
	}
	size := binary.BigEndian.Uint32(header[:])
	if size == 0 || size > maxControl {
		return nil, errControl
	}
	result := make([]byte, size)
	if _, err := io.ReadFull(s.tls, result); err != nil {
		return nil, errControl
	}
	return result, nil
}

func (s *ControlStream) Close() error {
	s.once.Do(func() {
		if s.timer != nil {
			s.timer.Stop()
		}
		// Abort the raw socket before closing the TLS wrapper. tls.Conn.Close
		// otherwise tries to send close_notify and can wait on a stalled relay.
		if s.abort != nil {
			s.abort()
		}
		if s.socket != nil {
			s.socket.Close()
		}
		if s.stop != nil && !s.stop() {
			<-s.done
		}
		s.cancel()
	})
	return nil
}
