package arbiterhttp

import (
	"context"
	"errors"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"net/http/httptrace"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func timeoutHTTPClient(connect, response, idle time.Duration) *http.Client {
	client := &http.Client{}
	ConfigureTimeouts(client, connect, response, idle)
	return client
}

func TestResponseBudgetExcludesConnectionSetup(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(60 * time.Millisecond)
		_, _ = io.WriteString(w, "ok")
	}))
	defer server.Close()
	client := timeoutHTTPClient(time.Second, 150*time.Millisecond, time.Second)
	transport := client.Transport.(*TimeoutTransport).Base.(*http.Transport)
	transport.DialContext = func(ctx context.Context, network, addr string) (net.Conn, error) {
		time.Sleep(180 * time.Millisecond)
		return (&net.Dialer{}).DialContext(ctx, network, addr)
	}
	started := time.Now()
	resp, err := client.Get(server.URL)
	if err != nil {
		t.Fatalf("connection consumed the response budget: %v", err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil || string(data) != "ok" {
		t.Fatalf("unexpected response: %q, %v", data, err)
	}
	if time.Since(started) < 150*time.Millisecond {
		t.Fatal("test did not exceed the response budget in total")
	}
}

func TestAPIBudgetExcludesReconnectConnectionSetup(t *testing.T) {
	client := &http.Client{Transport: &TimeoutTransport{
		Base: roundTripFunc(func(req *http.Request) (*http.Response, error) {
			trace := httptrace.ContextClientTrace(req.Context())
			trace.GetConn(req.URL.Host)
			trace.GotConn(httptrace.GotConnInfo{Reused: true})
			trace.GetConn(req.URL.Host)
			time.Sleep(120 * time.Millisecond)
			trace.GotConn(httptrace.GotConnInfo{})
			return &http.Response{
				StatusCode: http.StatusOK,
				Header:     make(http.Header),
				Body:       io.NopCloser(strings.NewReader("ok")),
				Request:    req,
			}, nil
		}),
		ConnectTimeout:  200 * time.Millisecond,
		ResponseTimeout: 80 * time.Millisecond,
		IdleTimeout:     time.Second,
	}}
	resp, err := client.Get("http://arbiter.test")
	if err != nil {
		t.Fatalf("reconnect consumed the response budget: %v", err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil || string(data) != "ok" {
		t.Fatalf("unexpected response after reconnect: %q, %v", data, err)
	}
}

func TestDownloadBudgetStartsAfterReconnect(t *testing.T) {
	client := &http.Client{Transport: &TimeoutTransport{
		Base: roundTripFunc(func(req *http.Request) (*http.Response, error) {
			trace := httptrace.ContextClientTrace(req.Context())
			trace.GetConn(req.URL.Host)
			trace.GotConn(httptrace.GotConnInfo{Reused: true})
			trace.GetConn(req.URL.Host)
			time.Sleep(120 * time.Millisecond)
			trace.GotConn(httptrace.GotConnInfo{})
			return &http.Response{
				StatusCode: http.StatusOK,
				Header:     make(http.Header),
				Body:       io.NopCloser(strings.NewReader("download")),
				Request:    req,
			}, nil
		}),
		ConnectTimeout: 200 * time.Millisecond,
		IdleTimeout:    80 * time.Millisecond,
	}}
	resp, err := client.Get("http://arbiter.test")
	if err != nil {
		t.Fatalf("reconnect consumed the download header budget: %v", err)
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil || string(data) != "download" {
		t.Fatalf("unexpected download after reconnect: %q, %v", data, err)
	}
}

func TestAPIBudgetRemainsCumulativeAcrossReconnect(t *testing.T) {
	finalConnection := make(chan time.Time, 1)
	client := &http.Client{Transport: &TimeoutTransport{
		Base: roundTripFunc(func(req *http.Request) (*http.Response, error) {
			trace := httptrace.ContextClientTrace(req.Context())
			trace.GetConn(req.URL.Host)
			trace.GotConn(httptrace.GotConnInfo{Reused: true})
			time.Sleep(150 * time.Millisecond)
			trace.GetConn(req.URL.Host)
			time.Sleep(170 * time.Millisecond)
			trace.GotConn(httptrace.GotConnInfo{})
			finalConnection <- time.Now()
			<-req.Context().Done()
			return nil, req.Context().Err()
		}),
		ConnectTimeout:  300 * time.Millisecond,
		ResponseTimeout: 300 * time.Millisecond,
		IdleTimeout:     time.Second,
	}}
	_, err := client.Get("http://arbiter.test")
	assertTimeout(t, err, "API response")
	if !strings.Contains(err.Error(), "API response timeout after 300ms") {
		t.Fatalf("unexpected cumulative timeout diagnostic: %v", err)
	}
	if elapsed := time.Since(<-finalConnection); elapsed > 250*time.Millisecond {
		t.Fatalf("response budget was renewed after reconnect: %s", elapsed)
	}
}

func TestAPIBudgetRemainsCumulativeAcrossRedirects(t *testing.T) {
	for _, tc := range []struct {
		name      string
		delay     time.Duration
		wantError bool
	}{
		{name: "short redirects", delay: 10 * time.Millisecond},
		{name: "slow redirects", delay: 70 * time.Millisecond, wantError: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			var hops atomic.Int32
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				time.Sleep(tc.delay)
				hop := hops.Add(1)
				switch hop {
				case 1:
					http.Redirect(w, r, "/redirect/one", http.StatusFound)
				case 2:
					http.Redirect(w, r, "/redirect/two", http.StatusFound)
				case 3:
					http.Redirect(w, r, "/redirect/three", http.StatusFound)
				default:
					w.Header().Set("Content-Type", "application/json")
					_, _ = io.WriteString(w, `{"name":"arbiter"}`)
				}
			}))
			defer server.Close()

			client := NewClientWithHTTP(server.URL, timeoutHTTPClient(time.Second, 180*time.Millisecond, time.Second))
			_, err := client.Info(context.Background())
			if tc.wantError {
				assertTimeout(t, err, "API response")
				if !strings.Contains(err.Error(), "API response timeout after 180ms") {
					t.Fatalf("unexpected redirect timeout diagnostic: %v", err)
				}
				return
			}
			if err != nil {
				t.Fatalf("short redirects exceeded the response budget: %v", err)
			}
		})
	}
}

func TestConnectionTimeout(t *testing.T) {
	client := &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
		<-req.Context().Done()
		return nil, req.Context().Err()
	})}
	ConfigureTimeouts(client, 30*time.Millisecond, time.Second, time.Second)
	_, err := client.Get("http://arbiter.test")
	assertTimeout(t, err, "connection")
}

func TestTLSHandshakeIsConnectionSetup(t *testing.T) {
	listener, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer listener.Close()
	accepted := make(chan net.Conn, 1)
	go func() {
		conn, err := listener.Accept()
		if err == nil {
			accepted <- conn
		}
	}()
	client := timeoutHTTPClient(80*time.Millisecond, time.Second, time.Second)
	_, err = client.Get("https://" + listener.Addr().String())
	if err == nil {
		t.Fatal("TLS handshake was not bounded by the connection limit")
	}
	select {
	case conn := <-accepted:
		_ = conn.Close()
	case <-time.After(time.Second):
		t.Fatal("test server never accepted the connection")
	}
}

func TestAPIBudgetBoundsHeadersAndBodyDespiteProgress(t *testing.T) {
	for _, phase := range []string{"headers", "body"} {
		t.Run(phase, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if phase == "body" {
					w.WriteHeader(http.StatusOK)
					w.(http.Flusher).Flush()
				}
				for {
					select {
					case <-r.Context().Done():
						return
					case <-time.After(10 * time.Millisecond):
						if phase == "body" {
							_, _ = io.WriteString(w, "progress")
							w.(http.Flusher).Flush()
						}
					}
				}
			}))
			defer server.Close()
			client := timeoutHTTPClient(time.Second, 80*time.Millisecond, time.Second)
			resp, err := client.Get(server.URL)
			if err == nil {
				defer resp.Body.Close()
				_, err = io.ReadAll(resp.Body)
			}
			assertTimeout(t, err, "API response")
		})
	}
}

func TestDownloadKeepsProgressingWithoutTotalDeadline(t *testing.T) {
	for _, http2 := range []bool{false, true} {
		t.Run(map[bool]string{false: "http1", true: "http2"}[http2], func(t *testing.T) {
			server := httptest.NewUnstartedServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				for i := 0; i < 8; i++ {
					_, _ = io.WriteString(w, "x")
					w.(http.Flusher).Flush()
					time.Sleep(30 * time.Millisecond)
				}
			}))
			server.EnableHTTP2 = http2
			server.StartTLS()
			defer server.Close()
			client := server.Client()
			ConfigureTimeouts(client, time.Second, 0, 150*time.Millisecond)
			started := time.Now()
			resp, err := client.Get(server.URL)
			if err != nil {
				t.Fatal(err)
			}
			defer resp.Body.Close()
			data, err := io.ReadAll(resp.Body)
			if err != nil || string(data) != "xxxxxxxx" {
				t.Fatalf("healthy download interrupted: %q, %v", data, err)
			}
			if time.Since(started) < 150*time.Millisecond {
				t.Fatal("transfer did not exceed the inactivity limit in total")
			}
			if http2 && resp.ProtoMajor != 2 {
				t.Fatal("HTTP/2 test did not negotiate HTTP/2")
			}
		})
	}
}

func TestDownloadInactivityBoundsHeadersAndBody(t *testing.T) {
	for _, phase := range []string{"headers", "body"} {
		t.Run(phase, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				if phase == "body" {
					_, _ = io.WriteString(w, "initial data")
					w.(http.Flusher).Flush()
				}
				<-r.Context().Done()
			}))
			defer server.Close()
			client := timeoutHTTPClient(time.Second, 0, 80*time.Millisecond)
			resp, err := client.Get(server.URL)
			if err == nil {
				defer resp.Body.Close()
				_, err = io.ReadAll(resp.Body)
			}
			assertTimeout(t, err, "download inactivity")
		})
	}
}

func TestDownloadDoesNotCountLocalProcessingAsInactivity(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = io.WriteString(w, "data")
	}))
	defer server.Close()
	client := timeoutHTTPClient(time.Second, 0, 30*time.Millisecond)
	resp, err := client.Get(server.URL)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	time.Sleep(80 * time.Millisecond)
	data, err := io.ReadAll(resp.Body)
	if err != nil || string(data) != "data" {
		t.Fatalf("local pause triggered network timeout: %q, %v", data, err)
	}
}

func TestCallerCancellationIsPreserved(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	client := &http.Client{Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
		cancel()
		<-req.Context().Done()
		return nil, req.Context().Err()
	})}
	ConfigureTimeouts(client, time.Second, time.Second, time.Second)
	req, _ := http.NewRequestWithContext(ctx, http.MethodGet, "http://arbiter.test", nil)
	_, err := client.Do(req)
	if !errors.Is(err, context.Canceled) {
		t.Fatalf("caller cancellation lost: %v", err)
	}
}

func assertTimeout(t *testing.T, err error, phase string) {
	t.Helper()
	if err == nil || !strings.Contains(err.Error(), phase+" timeout") || !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("expected %s timeout, got %v", phase, err)
	}
	var netErr net.Error
	if !errors.As(err, &netErr) || !netErr.Timeout() {
		t.Fatalf("timeout does not satisfy net.Error: %v", err)
	}
}

type cleanupTransport struct {
	roundTripFunc
	closed bool
}

func (t *cleanupTransport) CloseIdleConnections() { t.closed = true }

func TestTimeoutClientForwardsIdleConnectionCleanup(t *testing.T) {
	base := &cleanupTransport{}
	client := &http.Client{Transport: base}
	ConfigureTimeouts(client, time.Second, time.Second, time.Second)
	client.CloseIdleConnections()
	if !base.closed {
		t.Fatal("timeout wrapper hid idle connection cleanup")
	}
}
