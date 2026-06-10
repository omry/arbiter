package cli_test

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/omry/arbiter/client/go-cli/internal/testutil"
)

func TestMain(m *testing.M) {
	if os.Getenv("ARBITER_TEST_ARTIFACT_HELPER") == "1" {
		runArtifactHelper()
		return
	}
	os.Exit(m.Run())
}

func TestArtifactGetRequiresExplicitDestination(t *testing.T) {
	result := testutil.RunCLI(t, nil, "artifact", "get", "http://127.0.0.1:9/artifact")

	if result.Code != 2 {
		t.Fatalf("expected exit code 2, got %d", result.Code)
	}
	if !strings.Contains(result.Stderr, "requires --stdout") {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
}

func TestArtifactHelp(t *testing.T) {
	tests := []struct {
		name     string
		args     []string
		expected []string
	}{
		{
			name: "artifact",
			args: []string{"artifact", "--help"},
			expected: []string{
				"usage: arbiter artifact {get,save,with-temp,with-stdin} ...",
				"get --stdout is text-only and size-bounded",
				"save is only for when the user explicitly asks to save a file",
			},
		},
		{
			name: "get",
			args: []string{"artifact", "get", "--help"},
			expected: []string{
				"usage: arbiter artifact get <url>",
				"--stdout",
				"--max-bytes",
			},
		},
		{
			name: "save",
			args: []string{"artifact", "save", "--help"},
			expected: []string{
				"usage: arbiter artifact save <url> <path>",
				"only when the user explicitly requests saving",
				"never writes artifact bytes to stdout",
			},
		},
		{
			name: "with-temp",
			args: []string{"artifact", "with-temp", "--help"},
			expected: []string{
				"usage: arbiter artifact with-temp <url>",
				"Use {} in argv",
				"without a shell",
			},
		},
		{
			name: "with-stdin",
			args: []string{"artifact", "with-stdin", "--help"},
			expected: []string{
				"usage: arbiter artifact with-stdin <url>",
				"streamed to child stdin",
				"Raw artifact bytes are never written to stdout",
			},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := testutil.RunCLI(t, nil, tt.args...)

			if result.Code != 0 {
				t.Fatalf("expected exit code 0, got %d stderr=%q", result.Code, result.Stderr)
			}
			if result.Stderr != "" {
				t.Fatalf("unexpected stderr: %q", result.Stderr)
			}
			for _, expected := range tt.expected {
				if !strings.Contains(result.Stdout, expected) {
					t.Fatalf("expected stdout to contain %q, got %q", expected, result.Stdout)
				}
			}
		})
	}
}

func TestArtifactPipeAliasIsNotAccepted(t *testing.T) {
	result := testutil.RunCLI(t, nil, "artifact", "pipe", "--help")

	if result.Code != 2 {
		t.Fatalf("expected exit code 2, got %d", result.Code)
	}
	if result.Stdout != "" {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
	if !strings.Contains(result.Stderr, "unknown artifact command: pipe") {
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

func TestArtifactWithTempRunsCommandWithPrivateTempPath(t *testing.T) {
	getCalls := 0
	body := []byte("hello temp\n")
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			getCalls++
			w.Header().Set("Content-Type", "application/octet-stream")
			w.Header().Set("Content-Disposition", `attachment; filename="sample.docx"`)
			w.Header().Set("Content-Length", fmt.Sprintf("%d", len(body)))
			w.WriteHeader(http.StatusOK)
			if _, err := w.Write(body); err != nil {
				t.Fatal(err)
			}
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	}))
	command := artifactHelperCommand("print-path", "{}")

	result := testutil.RunCLI(
		t,
		nil,
		"artifact",
		"with-temp",
		server.URL+"/artifact",
		"--",
		command[0],
		command[1],
		command[2],
		command[3],
		command[4],
	)

	if result.Code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.Code, result.Stderr)
	}
	if result.Stderr != "" {
		t.Fatalf("unexpected stderr=%q", result.Stderr)
	}
	if getCalls != 1 {
		t.Fatalf("expected one GET, got %d", getCalls)
	}
	tempPath := strings.TrimSpace(result.Stdout)
	if tempPath == "" {
		t.Fatalf("expected helper to print temp path")
	}
	if !strings.HasSuffix(tempPath, ".docx") {
		t.Fatalf("expected temp path to preserve extension, got %q", tempPath)
	}
	if _, err := os.Stat(tempPath); !os.IsNotExist(err) {
		t.Fatalf("expected temp artifact to be removed, stat err=%v", err)
	}
}

func TestArtifactWithTempUsesCommonMimeExtension(t *testing.T) {
	body := []byte("sheet")
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			w.Header().Set("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
			w.Header().Set("Content-Length", fmt.Sprintf("%d", len(body)))
			w.WriteHeader(http.StatusOK)
			if _, err := w.Write(body); err != nil {
				t.Fatal(err)
			}
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	}))
	command := artifactHelperCommand("print-path", "{}")

	result := testutil.RunCLI(
		t,
		nil,
		"artifact",
		"with-temp",
		server.URL+"/artifact",
		"--",
		command[0],
		command[1],
		command[2],
		command[3],
		command[4],
	)

	if result.Code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.Code, result.Stderr)
	}
	tempPath := strings.TrimSpace(result.Stdout)
	if !strings.HasSuffix(tempPath, ".xlsx") {
		t.Fatalf("expected temp path to use .xlsx MIME extension, got %q", tempPath)
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

func TestArtifactSaveSavesBinaryArtifactToExplicitOutputFile(t *testing.T) {
	getCalls := 0
	body := []byte{0x25, 0x50, 0x44, 0x46, 0x00, 0xff}
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
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
		"save",
		server.URL+"/artifact",
		outputPath,
	)

	if result.Code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.Code, result.Stderr)
	}
	if result.Stdout != "" || result.Stderr != "" {
		t.Fatalf("unexpected output stdout=%q stderr=%q", result.Stdout, result.Stderr)
	}
	if getCalls != 1 {
		t.Fatalf("expected one GET, got %d", getCalls)
	}
	saved, err := os.ReadFile(outputPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(saved) != string(body) {
		t.Fatalf("unexpected saved bytes: %v", saved)
	}
}

func TestArtifactGetRejectsOutputOption(t *testing.T) {
	result := testutil.RunCLI(
		t,
		nil,
		"artifact",
		"get",
		"http://127.0.0.1:9/artifact",
		"--output",
		filepath.Join(t.TempDir(), "attachment.pdf"),
	)

	if result.Code != 2 {
		t.Fatalf("expected exit code 2, got %d", result.Code)
	}
	if !strings.Contains(result.Stderr, "unknown artifact get argument: --output") {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
}

func TestArtifactWithStdinStreamsBinaryToCommand(t *testing.T) {
	getCalls := 0
	body := []byte{0x25, 0x50, 0x44, 0x46, 0x00, 0xff}
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
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
	command := artifactHelperCommand("stdin-len")

	result := testutil.RunCLI(
		t,
		nil,
		"artifact",
		"with-stdin",
		server.URL+"/artifact",
		"--",
		command[0],
		command[1],
		command[2],
		command[3],
	)

	if result.Code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.Code, result.Stderr)
	}
	if result.Stdout != "stdin:6\n" {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
	if result.Stderr != "" {
		t.Fatalf("unexpected stderr=%q", result.Stderr)
	}
	if getCalls != 1 {
		t.Fatalf("expected one GET, got %d", getCalls)
	}
}

func TestArtifactWithStdinRejectsNonTextChildStdout(t *testing.T) {
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			fmt.Fprint(w, "hello")
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	}))
	command := artifactHelperCommand("binary-stdout")

	result := testutil.RunCLI(
		t,
		nil,
		"artifact",
		"with-stdin",
		server.URL+"/artifact",
		"--",
		command[0],
		command[1],
		command[2],
		command[3],
	)

	if result.Code != 1 {
		t.Fatalf("expected exit code 1, got %d", result.Code)
	}
	if result.Stdout != "" {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
	if !strings.Contains(result.Stderr, "non-text child stdout") {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
}

func TestArtifactWithStdinRejectsOversizedChildStdout(t *testing.T) {
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			fmt.Fprint(w, "hello")
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	}))
	command := artifactHelperCommand("text-stdout")

	result := testutil.RunCLI(
		t,
		nil,
		"artifact",
		"with-stdin",
		server.URL+"/artifact",
		"--max-child-stdout-bytes",
		"4",
		"--",
		command[0],
		command[1],
		command[2],
		command[3],
	)

	if result.Code != 1 {
		t.Fatalf("expected exit code 1, got %d", result.Code)
	}
	if result.Stdout != "" {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
	if !strings.Contains(result.Stderr, "child stdout larger than 4 bytes") {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
}

func TestArtifactWithStdinTerminatesCommandWhenChildStdoutCapIsReached(t *testing.T) {
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodGet:
			fmt.Fprint(w, "hello")
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	}))
	command := artifactHelperCommand("text-stdout-sleep")

	start := time.Now()
	result := testutil.RunCLI(
		t,
		nil,
		"artifact",
		"with-stdin",
		server.URL+"/artifact",
		"--max-child-stdout-bytes",
		"4",
		"--",
		command[0],
		command[1],
		command[2],
		command[3],
	)
	elapsed := time.Since(start)

	if result.Code != 1 {
		t.Fatalf("expected exit code 1, got %d", result.Code)
	}
	if result.Stdout != "" {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
	if !strings.Contains(result.Stderr, "child stdout larger than 4 bytes") {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
	if elapsed > 2*time.Second {
		t.Fatalf("expected child command to terminate promptly, elapsed=%s", elapsed)
	}
}

func TestArtifactWithTempRequiresPathPlaceholder(t *testing.T) {
	command := artifactHelperCommand("stdin-len")

	result := testutil.RunCLI(
		t,
		nil,
		"artifact",
		"with-temp",
		"http://127.0.0.1:9/artifact",
		"--",
		command[0],
		command[1],
		command[2],
		command[3],
	)

	if result.Code != 2 {
		t.Fatalf("expected exit code 2, got %d", result.Code)
	}
	if !strings.Contains(result.Stderr, "must contain a {} path placeholder") {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
}

func artifactHelperCommand(args ...string) []string {
	os.Setenv("ARBITER_TEST_ARTIFACT_HELPER", "1")
	command := []string{os.Args[0], "-test.run=TestArtifactCommandHelper", "--"}
	return append(command, args...)
}

func runArtifactHelper() {
	args := os.Args
	for len(args) > 0 && args[0] != "--" {
		args = args[1:]
	}
	if len(args) < 2 {
		os.Exit(2)
	}
	args = args[1:]
	switch args[0] {
	case "print-path":
		if len(args) != 2 {
			os.Exit(2)
		}
		fmt.Println(args[1])
	case "stdin-len":
		data, err := io.ReadAll(os.Stdin)
		if err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
		fmt.Printf("stdin:%d\n", len(data))
	case "binary-stdout":
		_, _ = os.Stdout.Write([]byte{0x00, 0xff})
	case "text-stdout":
		fmt.Fprint(os.Stdout, "hello")
	case "text-stdout-sleep":
		fmt.Fprint(os.Stdout, "hello")
		time.Sleep(5 * time.Second)
	default:
		os.Exit(2)
	}
	os.Exit(0)
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
