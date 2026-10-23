package identity

import (
	"bytes"
	"context"
	"crypto/sha256"
	"crypto/tls"
	"encoding/hex"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/gorilla/websocket"
)

const proofComplete = "arbiter-pairing-proof-complete-v1\x00"

var ErrEnrollmentClosed = errors.New("enrollment is closed; ask the server operator to run arbiter-server enrollment open, then start again")
var ErrEnrollmentUnavailable = errors.New("enrollment is unavailable or invalid; the request may have expired, restarted or been rejected; ask the operator and start again")

// enrollmentHTTP is deliberately separate from ordinary API configuration. The
// outer certificate is untrusted bootstrap transport; only Noise authenticates
// pairing. No inherited proxy, cookie jar, authorization, redirects or client key.
func enrollmentHTTP(timeout time.Duration) (*http.Client, *http.Transport) {
	t := &http.Transport{TLSClientConfig: &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: true},
		MaxResponseHeaderBytes: 32768, DisableKeepAlives: true,
		DialContext: (&net.Dialer{Timeout: timeout}).DialContext, TLSHandshakeTimeout: timeout}
	return &http.Client{Transport: t, Timeout: timeout, CheckRedirect: func(*http.Request, []*http.Request) error {
		return http.ErrUseLastResponse
	}}, t
}

func enrollmentURL(raw, path string) (*url.URL, error) {
	u, err := url.Parse(raw)
	if err != nil || u.Scheme != "https" || u.Hostname() == "" || u.User != nil || u.RawQuery != "" || u.Fragment != "" {
		return nil, ErrEnrollmentUnavailable
	}
	u.Path = strings.TrimRight(u.Path, "/") + path
	u.RawPath = ""
	return u, nil
}

// PairEnrollment consumes saved provisional keys. Handoff must show the secret
// only through a protected explicit interaction and wait for operator approval.
// Its successful return proves possession only: no enrolled state is persisted.
func PairEnrollment(ctx context.Context, p *Provisional, secret PairingSecret, timeout time.Duration,
	handoff func(context.Context, string, string) error,
) error {
	if p == nil || handoff == nil || timeout <= 0 {
		return ErrEnrollmentUnavailable
	}
	initiator, err := NewPairingInitiator(p, secret)
	if err != nil {
		return ErrEnrollmentUnavailable
	}
	defer initiator.Close()
	raw := initiator.Advertisement()
	target, err := enrollmentURL(p.record.ServerURL, "/api/v1/enrollment/requests")
	if err != nil {
		return err
	}
	client, transport := enrollmentHTTP(timeout)
	defer transport.CloseIdleConnections()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, target.String(), bytes.NewReader(raw))
	if err != nil {
		return ErrEnrollmentUnavailable
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := client.Do(req)
	if err != nil {
		return ErrEnrollmentUnavailable
	}
	data, readErr := io.ReadAll(io.LimitReader(resp.Body, 65537))
	resp.Body.Close()
	if readErr != nil || len(data) > 65536 {
		return ErrEnrollmentUnavailable
	}
	if resp.StatusCode == http.StatusForbidden {
		return ErrEnrollmentClosed
	}
	var pending struct{ Selector, RequestDigest, Status string }
	// Explicit wire names avoid accepting a language-specific spelling by accident.
	fields := make(map[string]json.RawMessage, 3)
	d := json.NewDecoder(bytes.NewReader(data))
	token, err := d.Token()
	if resp.StatusCode != http.StatusOK || err != nil || token != json.Delim('{') {
		return ErrEnrollmentUnavailable
	}
	allowed := map[string]bool{"selector": true, "request_digest": true, "status": true}
	for d.More() {
		token, err = d.Token()
		name, ok := token.(string)
		if err != nil || !ok || !allowed[name] {
			return ErrEnrollmentUnavailable
		}
		if _, ok := fields[name]; ok {
			return ErrEnrollmentUnavailable
		}
		var value json.RawMessage
		if d.Decode(&value) != nil {
			return ErrEnrollmentUnavailable
		}
		fields[name] = value
	}
	if token, err = d.Token(); err != nil || token != json.Delim('}') {
		return ErrEnrollmentUnavailable
	}
	if _, err = d.Token(); err != io.EOF {
		return ErrEnrollmentUnavailable
	}
	if len(fields) != 3 ||
		json.Unmarshal(fields["selector"], &pending.Selector) != nil ||
		json.Unmarshal(fields["request_digest"], &pending.RequestDigest) != nil ||
		json.Unmarshal(fields["status"], &pending.Status) != nil {
		return ErrEnrollmentUnavailable
	}
	digest := sha256.Sum256(raw)
	if pending.Selector != p.record.Selector || pending.Status != "pending" || pending.RequestDigest != hex.EncodeToString(digest[:]) {
		return ErrEnrollmentUnavailable
	}
	if err := handoff(ctx, pending.Selector, secret.Encoded()); err != nil {
		return err
	}
	ctx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	target, _ = enrollmentURL(p.record.ServerURL, "/api/v1/enrollment/requests/"+pending.Selector+"/pair")
	target.Scheme = "wss"
	return pairingSocket(ctx, target.String(), initiator, p)
}

// Count plaintext wire bytes, including HTTP and fragmented/control framing.
// The TLS connection below this adapter retains its own bounded record parser.
type pairingConn struct {
	net.Conn
	read, written int
	headers       bool
}

func (c *pairingConn) Read(p []byte) (int, error) {
	limit := MaxPairingBytes
	if c.headers {
		limit = 32768
	}
	if c.read >= limit {
		return 0, ErrEnrollmentUnavailable
	}
	if len(p) > limit-c.read {
		p = p[:limit-c.read]
	}
	n, err := c.Conn.Read(p)
	c.read += n
	return n, err
}
func (c *pairingConn) Write(p []byte) (int, error) {
	if len(p) > MaxPairingBytes-c.written {
		return 0, ErrEnrollmentUnavailable
	}
	n, err := c.Conn.Write(p)
	c.written += n
	return n, err
}

func pairingSocket(ctx context.Context, target string, initiator *PairingInitiator, material *Provisional) error {
	var bounded *pairingConn
	stopCancel := func() bool { return true }
	cancelDone := make(chan struct{})
	defer func() {
		if !stopCancel() {
			<-cancelDone
		}
	}()
	dialer := websocket.Dialer{ReadBufferSize: 4096, WriteBufferSize: 4096,
		NetDialTLSContext: func(ctx context.Context, network, address string) (net.Conn, error) {
			d := tls.Dialer{Config: &tls.Config{MinVersion: tls.VersionTLS12, InsecureSkipVerify: true}}
			c, err := d.DialContext(ctx, network, address)
			if err != nil {
				return nil, err
			}
			deadline, _ := ctx.Deadline()
			if err := c.SetDeadline(deadline); err != nil {
				c.Close()
				return nil, err
			}
			bounded = &pairingConn{Conn: c, headers: true}
			// DialContext's HTTP upgrade reads only honor the absolute deadline.
			// Abort the raw socket on cancellation, without a TLS close-notify
			// write, and retain ownership until that callback actually finishes.
			stopCancel = context.AfterFunc(ctx, func() {
				defer close(cancelDone)
				c.(*tls.Conn).NetConn().Close()
			})
			return bounded, nil
		}}
	ws, response, err := dialer.DialContext(ctx, target, nil)
	if err != nil {
		if response != nil {
			response.Body.Close()
		}
		return ErrEnrollmentUnavailable
	}
	defer ws.Close()
	if response.Header.Get("Sec-WebSocket-Extensions") != "" || ws.Subprotocol() != "" {
		return ErrEnrollmentUnavailable
	}
	deadline, _ := ctx.Deadline()
	if ws.SetReadDeadline(deadline) != nil || ws.SetWriteDeadline(deadline) != nil {
		return ErrEnrollmentUnavailable
	}
	bounded.headers = false
	ws.SetReadLimit(MaxPairingFrame)
	// Controls never extend the absolute deadline; count both bytes and frames.
	started, frames, controls := time.Now(), 0, 0
	control := func(data string) error {
		if time.Since(started) >= time.Second {
			started, frames, controls = time.Now(), 0, 0
		}
		frames++
		controls += len(data) + 6
		if frames > 32 || controls > 4096 {
			return ErrEnrollmentUnavailable
		}
		return nil
	}
	ws.SetPongHandler(control)
	ws.SetPingHandler(func(data string) error {
		if err := control(data); err != nil {
			return err
		}
		deadline, _ := ctx.Deadline()
		return ws.WriteControl(websocket.PongMessage, []byte(data), deadline)
	})
	read := func() ([]byte, error) {
		typ, data, err := ws.ReadMessage()
		if err != nil || typ != websocket.BinaryMessage {
			return nil, ErrEnrollmentUnavailable
		}
		return data, nil
	}
	first, err := initiator.FirstFlight()
	if err != nil || ws.WriteMessage(websocket.BinaryMessage, first) != nil {
		return ErrEnrollmentUnavailable
	}
	reply, err := read()
	if err != nil || initiator.Complete(reply) != nil {
		return ErrEnrollmentUnavailable
	}
	proof, err := read()
	if err != nil {
		return err
	}
	clientProof, _, err := initiator.ProveClient(proof, material, time.Now().UTC())
	if err != nil || ws.WriteMessage(websocket.BinaryMessage, clientProof) != nil {
		return ErrEnrollmentUnavailable
	}
	ack, err := read()
	if err != nil {
		return err
	}
	plain, err := initiator.Decrypt(ack)
	if err != nil || string(plain) != proofComplete {
		return ErrEnrollmentUnavailable
	}
	return ctx.Err()
}
