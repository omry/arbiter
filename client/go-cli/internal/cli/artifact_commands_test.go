package cli_test

import (
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/omry/arbiter/client/go-cli/internal/testutil"
)

func TestArtifactGetRequiresExplicitDestination(t *testing.T) {
	result := testutil.RunCLI(t, nil, "artifact", "get", "http://127.0.0.1:9/artifact")

	if result.Code != 2 {
		t.Fatalf("expected exit code 2, got %d", result.Code)
	}
	if !strings.Contains(result.Stderr, "requires exactly one of --stdout or --output PATH") {
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

func TestArtifactGetSavesBinaryArtifactToOutputFile(t *testing.T) {
	headCalls := 0
	getCalls := 0
	body := []byte{0x25, 0x50, 0x44, 0x46, 0x00, 0xff}
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodHead:
			headCalls++
			w.Header().Set("Content-Type", "application/pdf")
			w.Header().Set("Content-Length", fmt.Sprintf("%d", len(body)))
			w.WriteHeader(http.StatusOK)
		case http.MethodGet:
			getCalls++
			w.Header().Set("Content-Type", "application/pdf")
			w.Header().Set("Content-Length", fmt.Sprintf("%d", len(body)))
			w.WriteHeader(http.StatusOK)
			if _, err := w.Write(body); err != nil {
				t.Fatal(err)
			}
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	}))
	outputPath := filepath.Join(t.TempDir(), "attachment.pdf")

	result := testutil.RunCLI(
		t,
		nil,
		"artifact",
		"get",
		server.URL+"/artifact",
		"--output",
		outputPath,
	)

	if result.Code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.Code, result.Stderr)
	}
	if result.Stdout != "" || result.Stderr != "" {
		t.Fatalf("unexpected output stdout=%q stderr=%q", result.Stdout, result.Stderr)
	}
	if headCalls != 0 || getCalls != 1 {
		t.Fatalf("expected no HEAD and one GET, got HEAD=%d GET=%d", headCalls, getCalls)
	}
	saved, err := os.ReadFile(outputPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(saved) != string(body) {
		t.Fatalf("unexpected saved bytes: %v", saved)
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
