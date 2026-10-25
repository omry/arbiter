package identity

import (
	"bufio"
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"crypto/sha1"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/base64"
	"encoding/binary"
	"encoding/json"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/gorilla/websocket"
)

// Public-only input created by the real Python server; no product trust setup.
func TestPythonControlTLSPeer(t *testing.T) {
	path := os.Getenv("ARBITER_TEST_CONTROL_TLS")
	if path == "" {
		t.Skip("Python control TLS peer is not running")
	}
	raw, err := os.ReadFile(path)
	if err != nil || len(raw) > 32768 {
		t.Fatal("invalid public fixture")
	}
	var input struct {
		URL, Mode string
		Trust     PairingServer
	}
	if json.Unmarshal(raw, &input) != nil {
		t.Fatal("invalid public fixture")
	}
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	if input.Mode == "control-timeout" {
		var stop context.CancelFunc
		ctx, stop = context.WithTimeout(ctx, time.Second)
		defer stop()
	}
	timeout := 5 * time.Second
	if input.Mode == "separate-deadline" {
		timeout = 2 * time.Second
	}
	if input.Mode == "handshake-timeout" {
		timeout = 50 * time.Millisecond
	}
	stream, err := DialControl(ctx, input.URL, input.Trust, timeout)
	if input.Mode == "server-id" || input.Mode == "issuer" || input.Mode == "alpn" || input.Mode == "version" || input.Mode == "handshake-timeout" {
		if err == nil {
			stream.Close()
			t.Fatal("invalid server authenticated")
		}
		return
	}
	if err != nil {
		t.Fatal(err)
	}
	defer stream.Close()
	message := stream.Exporter()
	if len(message) != 32 {
		t.Fatal("missing channel binding")
	}
	message[0] ^= 1
	if bytes.Equal(message, stream.Exporter()) {
		t.Fatal("exporter leaked mutable state")
	}
	message = stream.Exporter()
	if input.Mode == "maximum" {
		message = append(message, make([]byte, maxControl-len(message))...)
	}
	if input.Mode == "cancel" {
		cancel()
	}
	reply, err := stream.Exchange(message)
	if input.Mode == "oversized-reply" || input.Mode == "truncated" || input.Mode == "cancel" || input.Mode == "control-timeout" {
		if err == nil {
			t.Fatal("invalid exchange succeeded")
		}
		return
	}
	if err != nil || !bytes.Equal(reply, message) {
		t.Fatal("TLS framing or Python/Go exporter mismatch", err)
	}
	if _, err := stream.Exchange(message); err == nil {
		t.Fatal("second control accepted")
	}
	t.Log("fresh Python-Go inner TLS peer passed")
}

type failingControlConn struct {
	net.Conn
	writes atomic.Int32
}

func (c *failingControlConn) Write([]byte) (int, error) {
	c.writes.Add(1)
	return 0, io.ErrClosedPipe
}

func TestControlExchangeConcurrentSingleUse(t *testing.T) {
	raw := &failingControlConn{}
	s := &ControlStream{ctx: context.Background(), tls: tls.Client(raw, &tls.Config{InsecureSkipVerify: true}), cancel: func() {}}
	start := make(chan struct{})
	var group sync.WaitGroup
	for range 32 {
		group.Add(1)
		go func() {
			defer group.Done()
			<-start
			if _, err := s.Exchange([]byte("one fixed request")); err == nil {
				t.Error("synthetic transport succeeded")
			}
		}()
	}
	close(start)
	group.Wait()
	if raw.writes.Load() != 1 {
		t.Fatal("more than one exchange entered I/O")
	}
}

func TestControlOuterHasNoCredentialsOrRedirects(t *testing.T) {
	calls := 0
	other := httptest.NewTLSServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		calls++
	}))
	defer other.Close()
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != controlPath || r.Header.Get("Authorization") != "" ||
			r.Header.Get("Proxy-Authorization") != "" || r.Header.Get("Cookie") != "" || len(r.TLS.PeerCertificates) != 0 {
			t.Error("outer upgrade leaked credentials")
		}
		http.Redirect(w, r, other.URL, http.StatusTemporaryRedirect)
	}))
	defer server.Close()
	t.Setenv("HTTPS_PROXY", other.URL)
	t.Setenv("HTTP_PROXY", other.URL)
	stream, err := DialControl(context.Background(), server.URL, PairingServer{}, time.Second)
	if err == nil {
		stream.Close()
		t.Fatal("redirect accepted")
	}
	if calls != 0 {
		t.Fatal("inherited proxy or redirect used")
	}
	for _, url := range []string{"http://localhost", "https://user:password@localhost", "https://localhost?secret=x"} {
		if _, err := DialControl(context.Background(), url, PairingServer{}, time.Second); err == nil {
			t.Fatal("unsafe carrier URL accepted")
		}
	}
}

func TestControlCertificateChainAdmission(t *testing.T) {
	fixture := httptest.NewTLSServer(nil)
	defer fixture.Close()
	certificate := fixture.TLS.Certificates[0]
	der := certificate.Certificate[0]
	certificate.Certificate = make([][]byte, 128)
	for i := range certificate.Certificate {
		certificate.Certificate[i] = der
	}
	left, right := net.Pipe()
	defer left.Close()
	defer right.Close()
	left.SetDeadline(time.Now().Add(2 * time.Second))
	right.SetDeadline(time.Now().Add(2 * time.Second))
	server := tls.Server(right, &tls.Config{MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13,
		Certificates: []tls.Certificate{certificate}, SessionTicketsDisabled: true})
	done := make(chan error, 1)
	go func() {
		done <- server.Handshake()
		right.Close()
	}()
	incoming := controlHandshakeConn(left)
	parsed := false
	client := tls.Client(incoming, &tls.Config{MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13,
		InsecureSkipVerify: true, VerifyConnection: func(tls.ConnectionState) error {
			parsed = true
			return errControl
		}})
	err := client.Handshake()
	left.Close()
	serverErr := <-done
	if err == nil || parsed || incoming.read != 32768 || serverErr == nil {
		t.Fatalf("chain admission: client=%v parsed=%v read=%d server=%v", err, parsed, incoming.read, serverErr)
	}
}

func TestControlOuterCertificateChainAdmission(t *testing.T) {
	var reached atomic.Bool
	server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		reached.Store(true)
		w.WriteHeader(http.StatusBadRequest)
	}))
	fixture := httptest.NewTLSServer(nil)
	certificate := fixture.TLS.Certificates[0]
	fixture.Close()
	der := certificate.Certificate[0]
	certificate.Certificate = make([][]byte, 128)
	for i := range certificate.Certificate {
		certificate.Certificate[i] = der
	}
	server.TLS = &tls.Config{Certificates: []tls.Certificate{certificate},
		MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13}
	server.StartTLS()
	defer server.Close()
	stream, err := DialControl(context.Background(), server.URL, PairingServer{}, time.Second)
	if stream != nil {
		stream.Close()
	}
	if err == nil || reached.Load() {
		t.Fatal("oversized outer certificate chain reached HTTP upgrade", err)
	}
}

func outerControlServerConfig(t *testing.T) *tls.Config {
	t.Helper()
	key, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Now()
	template := &x509.Certificate{SerialNumber: big.NewInt(1), Subject: pkix.Name{CommonName: "localhost"},
		NotBefore: now.Add(-time.Minute), NotAfter: now.Add(time.Hour),
		KeyUsage:    x509.KeyUsageKeyEncipherment | x509.KeyUsageDigitalSignature,
		ExtKeyUsage: []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth}}
	der, err := x509.CreateCertificate(rand.Reader, template, template, &key.PublicKey, key)
	if err != nil {
		t.Fatal(err)
	}
	return &tls.Config{Certificates: []tls.Certificate{{Certificate: [][]byte{der}, PrivateKey: key}},
		MinVersion: tls.VersionTLS12, MaxVersion: tls.VersionTLS12}
}

func writeControlUpgrade(w io.Writer, request *http.Request) error {
	digest := sha1.Sum([]byte(request.Header.Get("Sec-WebSocket-Key") + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"))
	accept := base64.StdEncoding.EncodeToString(digest[:])
	_, err := io.WriteString(w, "HTTP/1.1 101 Switching Protocols\r\n"+
		"Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: "+accept+"\r\n\r\n")
	return err
}

func readMaskedControlFrame(r io.Reader) ([]byte, error) {
	var header [2]byte
	if _, err := io.ReadFull(r, header[:]); err != nil {
		return nil, err
	}
	length := uint64(header[1] & 0x7f)
	switch length {
	case 126:
		var extended [2]byte
		if _, err := io.ReadFull(r, extended[:]); err != nil {
			return nil, err
		}
		length = uint64(binary.BigEndian.Uint16(extended[:]))
	case 127:
		var extended [8]byte
		if _, err := io.ReadFull(r, extended[:]); err != nil {
			return nil, err
		}
		length = binary.BigEndian.Uint64(extended[:])
	}
	if header[1]&0x80 == 0 || length > maxControl {
		return nil, io.ErrUnexpectedEOF
	}
	var mask [4]byte
	if _, err := io.ReadFull(r, mask[:]); err != nil {
		return nil, err
	}
	payload := make([]byte, length)
	if _, err := io.ReadFull(r, payload); err != nil {
		return nil, err
	}
	for i := range payload {
		payload[i] ^= mask[i%len(mask)]
	}
	return payload, nil
}

func TestControlCloseAbortsRawBeforeOuterTLSCloseNotify(t *testing.T) {
	clientRaw, serverRaw := net.Pipe()
	defer clientRaw.Close()
	defer serverRaw.Close()
	release := make(chan struct{})
	defer close(release)
	serverTLS := tls.Server(serverRaw, outerControlServerConfig(t))
	clientTLS := tls.Client(clientRaw, &tls.Config{MinVersion: tls.VersionTLS12, MaxVersion: tls.VersionTLS12,
		InsecureSkipVerify: true, ServerName: "localhost"})
	serverReady := make(chan error, 1)
	go func() {
		if err := serverTLS.Handshake(); err != nil {
			serverReady <- err
			return
		}
		request, err := http.ReadRequest(bufio.NewReader(serverTLS))
		if err == nil {
			err = writeControlUpgrade(serverTLS, request)
		}
		serverReady <- err
		<-release
	}()
	if err := clientTLS.Handshake(); err != nil {
		t.Fatal(err)
	}
	target, err := url.Parse("ws://localhost" + controlPath)
	if err != nil {
		t.Fatal(err)
	}
	ws, _, err := websocket.NewClient(clientTLS, target, nil, 4096, 4096)
	if err != nil {
		t.Fatal(err)
	}
	if err := <-serverReady; err != nil {
		t.Fatal(err)
	}
	stream := &ControlStream{
		ctx:    context.Background(),
		socket: &controlSocket{Conn: ws},
		abort:  func() { _ = clientRaw.Close() },
		cancel: func() {},
		stop:   func() bool { return true },
		done:   make(chan struct{}),
	}
	closed := make(chan struct{})
	go func() {
		stream.Close()
		close(closed)
	}()
	select {
	case <-closed:
	case <-time.After(500 * time.Millisecond):
		t.Fatal("ControlStream.Close waited for outer TLS close_notify under backpressure")
	}
}

func TestControlUpgradeFailureAbortsRawBeforeOuterTLSCloseNotify(t *testing.T) {
	clientRaw, serverRaw := net.Pipe()
	defer clientRaw.Close()
	defer serverRaw.Close()
	release := make(chan struct{})
	defer close(release)
	serverTLS := tls.Server(serverRaw, outerControlServerConfig(t))
	clientTLS := tls.Client(clientRaw, &tls.Config{MinVersion: tls.VersionTLS12, MaxVersion: tls.VersionTLS12,
		InsecureSkipVerify: true, ServerName: "localhost"})
	serverReady := make(chan error, 1)
	go func() {
		if err := serverTLS.Handshake(); err != nil {
			serverReady <- err
			return
		}
		_, err := http.ReadRequest(bufio.NewReader(serverTLS))
		serverReady <- err
		<-release
	}()
	if err := clientTLS.Handshake(); err != nil {
		t.Fatal(err)
	}
	dialer := websocket.Dialer{ReadBufferSize: 4096, WriteBufferSize: 4096,
		NetDialTLSContext: func(context.Context, string, string) (net.Conn, error) {
			bounded := &pairingConn{Conn: clientTLS, headers: true}
			return &controlAbortConn{Conn: bounded, abort: func() { _ = clientRaw.Close() }}, nil
		}}
	connect, cancel := context.WithTimeout(context.Background(), 50*time.Millisecond)
	defer cancel()
	result := make(chan struct {
		err     error
		elapsed time.Duration
	}, 1)
	start := time.Now()
	go func() {
		_, _, err := dialer.DialContext(connect, "wss://localhost"+controlPath, nil)
		result <- struct {
			err     error
			elapsed time.Duration
		}{err, time.Since(start)}
	}()
	select {
	case err := <-serverReady:
		if err != nil {
			t.Fatal(err)
		}
	case <-time.After(500 * time.Millisecond):
		t.Fatal("Gorilla upgrade did not reach the backpressured server")
	}
	select {
	case outcome := <-result:
		if outcome.err == nil {
			t.Fatal("backpressured upgrade unexpectedly succeeded")
		}
		if outcome.elapsed > 500*time.Millisecond {
			t.Fatalf("upgrade timeout exceeded cleanup bound: %s", outcome.elapsed)
		}
	case <-time.After(1 * time.Second):
		t.Fatal("Gorilla upgrade cleanup remained blocked in outer TLS close_notify")
	}
}

func TestControlHandshakeTimeoutAbortsRawBeforeOuterTLSCloseNotify(t *testing.T) {
	clientRaw, serverRaw := net.Pipe()
	defer clientRaw.Close()
	defer serverRaw.Close()
	release := make(chan struct{})
	defer close(release)
	serverFrame := make(chan []byte, 1)
	serverErr := make(chan error, 1)
	serverTLS := tls.Server(serverRaw, outerControlServerConfig(t))
	clientTLS := tls.Client(clientRaw, &tls.Config{MinVersion: tls.VersionTLS12, MaxVersion: tls.VersionTLS12,
		InsecureSkipVerify: true, ServerName: "localhost"})
	go func() {
		if err := serverTLS.Handshake(); err != nil {
			serverErr <- err
			return
		}
		reader := bufio.NewReader(serverTLS)
		request, err := http.ReadRequest(reader)
		if err == nil {
			err = writeControlUpgrade(serverTLS, request)
		}
		if err != nil {
			serverErr <- err
			return
		}
		payload, err := readMaskedControlFrame(reader)
		if err != nil {
			serverErr <- err
			return
		}
		serverFrame <- payload
		<-release
	}()
	if err := clientTLS.Handshake(); err != nil {
		t.Fatal(err)
	}
	target, err := url.Parse("ws://localhost" + controlPath)
	if err != nil {
		t.Fatal(err)
	}
	outer := &controlAbortConn{
		Conn:  &pairingConn{Conn: clientTLS, headers: true},
		abort: func() { _ = clientRaw.Close() },
	}
	ws, _, err := websocket.NewClient(outer, target, nil, 4096, 4096)
	if err != nil {
		t.Fatal(err)
	}
	streamContext, cancel := context.WithCancel(context.Background())
	defer cancel()
	stream := &ControlStream{
		ctx:    streamContext,
		socket: &controlSocket{Conn: ws},
		abort:  outer.abort,
		cancel: cancel,
		done:   make(chan struct{}),
	}
	defer stream.Close()
	stream.stop = context.AfterFunc(streamContext, func() {
		defer close(stream.done)
		_ = clientRaw.Close()
	})
	stream.tls = tls.Client(controlHandshakeConn(stream.socket), &tls.Config{
		MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13,
		NextProtos: []string{controlALPN}, InsecureSkipVerify: true,
		SessionTicketsDisabled: true,
	})
	connect, connectCancel := context.WithTimeout(streamContext, 50*time.Millisecond)
	defer connectCancel()
	handshake := make(chan error, 1)
	go func() {
		handshake <- stream.tls.HandshakeContext(connect)
	}()
	select {
	case payload := <-serverFrame:
		if len(payload) == 0 {
			t.Fatal("inner TLS ClientHello was not carried in a WebSocket frame")
		}
	case err := <-serverErr:
		t.Fatal(err)
	case <-time.After(2 * time.Second):
		t.Fatal("server did not receive inner TLS ClientHello")
	}
	<-connect.Done()
	start := time.Now()
	select {
	case err := <-handshake:
		if err == nil {
			t.Fatal("backpressured inner handshake unexpectedly succeeded")
		}
		if elapsed := time.Since(start); elapsed > 500*time.Millisecond {
			t.Fatalf("connect timeout exceeded cleanup bound: %s", elapsed)
		}
	case <-time.After(500 * time.Millisecond):
		t.Fatal("setup timeout did not autonomously abort the raw transport")
	}
}
