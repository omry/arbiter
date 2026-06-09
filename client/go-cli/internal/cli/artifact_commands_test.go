package cli_test

import (
	"fmt"
	"net/http"
	"strings"
	"testing"

	"github.com/omry/arbiter/client/go-cli/internal/testutil"
)

func TestArtifactGetRequiresExplicitStdout(t *testing.T) {
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		t.Fatalf("artifact server should not be called")
	}))

	result := testutil.RunCLI(t, nil, "artifact", "get", server.URL+"/artifact")

	if result.Code != 2 {
		t.Fatalf("expected exit code 2, got %d", result.Code)
	}
	if !strings.Contains(result.Stderr, "requires explicit --stdout") {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
}

func TestArtifactGetRejectsNonTextArtifactBeforeGet(t *testing.T) {
	getCalls := 0
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodHead:
			w.Header().Set("Content-Type", "application/pdf")
			w.Header().Set("Content-Length", "12")
			w.WriteHeader(http.StatusOK)
		case http.MethodGet:
			getCalls++
			w.WriteHeader(http.StatusOK)
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	}))

	result := testutil.RunCLI(t, nil, "artifact", "get", server.URL+"/artifact", "--stdout")

	if result.Code != 1 {
		t.Fatalf("expected exit code 1, got %d", result.Code)
	}
	if getCalls != 0 {
		t.Fatalf("expected no GET calls, got %d", getCalls)
	}
	if !strings.Contains(result.Stderr, "non-text artifact") {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
}

func TestArtifactGetWritesSmallTextToStdout(t *testing.T) {
	headCalls := 0
	getCalls := 0
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodHead:
			headCalls++
			w.Header().Set("Content-Type", "text/plain; charset=utf-8")
			w.Header().Set("Content-Length", "12")
			w.WriteHeader(http.StatusOK)
		case http.MethodGet:
			getCalls++
			w.Header().Set("Content-Type", "text/plain; charset=utf-8")
			fmt.Fprint(w, "hello world\n")
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	}))

	result := testutil.RunCLI(t, nil, "artifact", "get", server.URL+"/artifact", "--stdout")

	if result.Code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.Code, result.Stderr)
	}
	if result.Stdout != "hello world\n" {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
	if headCalls != 1 || getCalls != 1 {
		t.Fatalf("expected one HEAD and one GET, got HEAD=%d GET=%d", headCalls, getCalls)
	}
}

func TestArtifactGetRejectsOversizedTextBeforeGet(t *testing.T) {
	getCalls := 0
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodHead:
			w.Header().Set("Content-Type", "text/plain")
			w.Header().Set("Content-Length", "13")
			w.WriteHeader(http.StatusOK)
		case http.MethodGet:
			getCalls++
			w.WriteHeader(http.StatusOK)
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	}))

	result := testutil.RunCLI(
		t,
		nil,
		"artifact",
		"get",
		server.URL+"/artifact",
		"--stdout",
		"--max-bytes",
		"12",
	)

	if result.Code != 1 {
		t.Fatalf("expected exit code 1, got %d", result.Code)
	}
	if getCalls != 0 {
		t.Fatalf("expected no GET calls, got %d", getCalls)
	}
	if !strings.Contains(result.Stderr, "limit is 12 bytes") {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
}
