package arbiterhttp

import (
	"context"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptrace"
	"sync"
	"time"
)

const (
	DefaultTimeout             = 30 * time.Second
	DefaultConnectTimeout      = 10 * time.Second
	DefaultDownloadIdleTimeout = 30 * time.Second
)

// TimeoutTransport separates connection setup from the response budget.
// A zero ResponseTimeout selects downloads: bounded header wait and body reads,
// without a deadline for the whole transfer.
type TimeoutTransport struct {
	Base            http.RoundTripper
	ConnectTimeout  time.Duration
	ResponseTimeout time.Duration
	IdleTimeout     time.Duration
}

func (t *TimeoutTransport) CloseIdleConnections() {
	if closer, ok := t.Base.(interface{ CloseIdleConnections() }); ok {
		closer.CloseIdleConnections()
	}
}

// ConfigureTimeouts replaces any existing timeout wrapper, retaining TLS settings.
func ConfigureTimeouts(client *http.Client, connect, response, idle time.Duration) {
	base := client.Transport
	if wrapped, ok := base.(*TimeoutTransport); ok {
		base = wrapped.Base
	}
	if base == nil {
		base = http.DefaultTransport.(*http.Transport).Clone()
	}
	if transport, ok := base.(*http.Transport); ok {
		transport = transport.Clone()
		transport.DialContext = (&net.Dialer{Timeout: connect, KeepAlive: 30 * time.Second}).DialContext
		transport.TLSHandshakeTimeout = connect
		// The phase timer bounds headers for downloads and the entire response
		// for API requests, including slow operations that return no early data.
		transport.ResponseHeaderTimeout = 0
		base = transport
	}
	client.Timeout = 0
	client.Transport = &TimeoutTransport{
		Base: base, ConnectTimeout: connect, ResponseTimeout: response, IdleTimeout: idle,
	}
}

type timeoutError struct {
	phase string
	limit time.Duration
}

func (err timeoutError) Error() string {
	return fmt.Sprintf("Arbiter %s timeout after %s", err.phase, err.limit)
}

func (err timeoutError) PublicMessage() string { return err.Error() }

func (err timeoutError) Timeout() bool   { return true }
func (err timeoutError) Temporary() bool { return true }
func (err timeoutError) Unwrap() error   { return context.DeadlineExceeded }

// Timers and trace callbacks may race with body close or each other. The epoch
// prevents a stopped timer's already-running callback from cancelling a new phase.
type requestTimer struct {
	mu     sync.Mutex
	timer  *time.Timer
	epoch  uint64
	closed bool
	cancel context.CancelCauseFunc
}

// responseBudget tracks API response time while excluding connection setup.
// It is separate from requestTimer because a reconnect pauses the budget
// instead of starting a new one.
type responseBudget struct {
	mu      sync.Mutex
	limit   time.Duration
	elapsed time.Duration
	started time.Time
	active  bool
}

// apiRequestState is shared by every transport round trip belonging to one
// logical API request. The standard HTTP client creates a new request for
// each redirect, so the state must travel in the request context rather than
// in a per-RoundTrip local.
type apiRequestState struct {
	mu      sync.Mutex
	phaseMu sync.Mutex
	timer   *requestTimer
	budget  *responseBudget
	cancel  context.CancelCauseFunc
}

type apiRequestStateKey struct{}

func newAPIRequestContext(ctx context.Context) (context.Context, *apiRequestState) {
	requestCtx, cancel := context.WithCancelCause(ctx)
	state := &apiRequestState{cancel: cancel}
	return context.WithValue(requestCtx, apiRequestStateKey{}, state), state
}

func apiRequestStateFrom(ctx context.Context) *apiRequestState {
	state, _ := ctx.Value(apiRequestStateKey{}).(*apiRequestState)
	return state
}

func (s *apiRequestState) ensure(response time.Duration) (*requestTimer, *responseBudget) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.timer == nil {
		s.timer = &requestTimer{cancel: s.cancel}
		s.budget = &responseBudget{limit: response}
	}
	return s.timer, s.budget
}

func (s *apiRequestState) close() {
	s.mu.Lock()
	timer := s.timer
	s.mu.Unlock()
	if timer != nil {
		timer.close()
		return
	}
	s.cancel(nil)
}

func (b *responseBudget) pause() {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.active {
		b.elapsed += time.Since(b.started)
		b.active = false
	}
}

func (b *responseBudget) resume() (time.Duration, bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.active {
		return 0, false
	}
	b.active = true
	b.started = time.Now()
	remaining := b.limit - b.elapsed
	if remaining < 0 {
		remaining = 0
	}
	return remaining, true
}

func (t *requestTimer) arm(limit time.Duration, phase string) {
	t.armWithErrorLimit(limit, limit, phase)
}

func (t *requestTimer) armWithErrorLimit(limit, errorLimit time.Duration, phase string) {
	t.mu.Lock()
	defer t.mu.Unlock()
	if t.closed {
		return
	}
	t.stopLocked()
	epoch := t.epoch
	t.timer = time.AfterFunc(limit, func() {
		t.mu.Lock()
		defer t.mu.Unlock()
		if !t.closed && t.epoch == epoch {
			t.closed = true
			t.cancel(timeoutError{phase, errorLimit})
		}
	})
}

func (t *requestTimer) stopLocked() {
	t.epoch++
	if t.timer != nil {
		t.timer.Stop()
	}
}

func (t *requestTimer) stop() {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.stopLocked()
}

func (t *requestTimer) close() {
	t.mu.Lock()
	defer t.mu.Unlock()
	t.closed = true
	t.stopLocked()
	t.cancel(nil)
}

func (t *TimeoutTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	state := apiRequestStateFrom(req.Context())
	shared := state != nil && t.ResponseTimeout > 0
	var ctx context.Context
	var timer *requestTimer
	var cancel context.CancelCauseFunc
	var phaseMu *sync.Mutex
	var budget *responseBudget
	if shared {
		ctx = req.Context()
		timer, budget = state.ensure(t.ResponseTimeout)
		phaseMu = &state.phaseMu
	} else {
		ctx, cancel = context.WithCancelCause(req.Context())
		timer = &requestTimer{cancel: cancel}
		if t.ResponseTimeout > 0 {
			budget = &responseBudget{limit: t.ResponseTimeout}
		}
		phaseMu = new(sync.Mutex)
	}
	phaseMu.Lock()
	timer.stop()
	if budget != nil {
		budget.pause()
	}
	timer.arm(t.ConnectTimeout, "connection")
	phaseMu.Unlock()
	trace := &httptrace.ClientTrace{GotConn: func(httptrace.GotConnInfo) {
		phaseMu.Lock()
		defer phaseMu.Unlock()
		if budget != nil {
			if remaining, started := budget.resume(); started {
				timer.armWithErrorLimit(remaining, t.ResponseTimeout, "API response")
			}
		} else {
			timer.arm(t.IdleTimeout, "download inactivity")
		}
	}, GetConn: func(string) {
		phaseMu.Lock()
		defer phaseMu.Unlock()
		timer.stop()
		if budget != nil {
			budget.pause()
		}
		timer.arm(t.ConnectTimeout, "connection")
	}}
	resp, err := t.Base.RoundTrip(req.Clone(httptrace.WithClientTrace(ctx, trace)))
	if err != nil {
		cause := context.Cause(ctx)
		if !shared {
			timer.close()
		}
		if cause != nil {
			return nil, cause
		}
		return nil, err
	}
	if t.ResponseTimeout == 0 {
		// Local output processing does not count as network inactivity. Arm the
		// idle timer only while awaiting headers or actively reading the body.
		timer.stop()
	}
	resp.Body = &timedBody{ReadCloser: resp.Body, ctx: ctx, timer: timer, idle: t.IdleTimeout, download: t.ResponseTimeout == 0, shared: shared}
	return resp, nil
}

type timedBody struct {
	io.ReadCloser
	ctx      context.Context
	timer    *requestTimer
	idle     time.Duration
	download bool
	shared   bool
}

func (b *timedBody) Read(p []byte) (int, error) {
	if b.download {
		b.timer.arm(b.idle, "download inactivity")
	}
	n, err := b.ReadCloser.Read(p)
	if b.download {
		b.timer.stop()
	}
	if cause := context.Cause(b.ctx); cause != nil {
		return n, cause
	}
	if err != nil && !b.shared {
		b.timer.close()
	}
	return n, err
}

func (b *timedBody) Close() error {
	if !b.shared {
		b.timer.close()
	}
	return b.ReadCloser.Close()
}
