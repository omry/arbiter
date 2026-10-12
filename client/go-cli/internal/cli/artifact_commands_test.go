package cli_test

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
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
				"usage: arbiter artifact {get,save,with-temp,with-stdin} ... [--json|--yaml]",
				"get --stdout is text-only and size-bounded",
				"save is only for when the user explicitly asks to save a file",
				"--json and --yaml format errors only",
			},
		},
		{
			name: "get",
			args: []string{"artifact", "get", "--help"},
			expected: []string{
				"usage: arbiter artifact get <url> --stdout [--max-bytes N] [--json|--yaml]",
				"--stdout",
				"--max-bytes",
				"--json",
			},
		},
		{
			name: "save",
			args: []string{"artifact", "save", "--help"},
			expected: []string{
				"usage: arbiter artifact save <url> <path> [--json|--yaml]",
				"only when the user explicitly requests saving",
				"never writes artifact bytes to stdout",
				"successful output is unchanged",
			},
		},
		{
			name: "with-temp",
			args: []string{"artifact", "with-temp", "--help"},
			expected: []string{
				"usage: arbiter artifact with-temp <url> [--max-child-stdout-bytes N] [--json|--yaml] -- <argv...>",
				"Use {} in argv",
				"without a shell",
				"successful bytes and child output are unchanged",
			},
		},
		{
			name: "with-stdin",
			args: []string{"artifact", "with-stdin", "--help"},
			expected: []string{
				"usage: arbiter artifact with-stdin <url> [--max-child-stdout-bytes N] [--json|--yaml] -- <argv...>",
				"streamed to child stdin",
				"Raw artifact bytes are never written to stdout",
				"successful bytes and child output are unchanged",
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

func TestArtifactConflictingFormatsRejectBeforeSideEffects(t *testing.T) {
	for _, formatOrder := range [][]string{{"--json", "--yaml"}, {"--yaml", "--json"}} {
		for _, commandName := range []string{"get", "save", "with-temp", "with-stdin"} {
			t.Run(commandName+"/"+formatOrder[0][2:]+"-then-"+formatOrder[1][2:], func(t *testing.T) {
				requests := 0
				server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
					requests++
					w.WriteHeader(http.StatusOK)
					_, _ = w.Write([]byte("artifact input\n"))
				}))
				outputPath := filepath.Join(t.TempDir(), "artifact.bin")
				args := []string{"artifact"}
				switch commandName {
				case "get":
					args = append(args, "get", server.URL, "--stdout")
					args = append(args, formatOrder...)
				case "save":
					args = append(args, "save", server.URL, outputPath)
					args = append(args, formatOrder...)
				case "with-temp":
					args = append(args, "with-temp", server.URL)
					args = append(args, formatOrder...)
					args = append(args, "--")
					args = append(args, artifactHelperCommand("print-path", "{}")...)
				case "with-stdin":
					args = append(args, "with-stdin", server.URL)
					args = append(args, formatOrder...)
					args = append(args, "--")
					args = append(args, artifactHelperCommand("stdin-len")...)
				}

				result := testutil.RunCLI(t, nil, args...)
				if result.Code != 2 || result.Stdout != "" {
					t.Fatalf("conflicting formats were not rejected: %#v", result)
				}
				if formatOrder[1] == "--json" {
					var envelope map[string]map[string]any
					if err := json.Unmarshal([]byte(result.Stderr), &envelope); err != nil || envelope["error"]["code"] != "usage_error" {
						t.Fatalf("conflicting formats did not produce JSON usage error: %#v", result)
					}
				} else if result.Stderr != "error:\n  code: usage_error\n  message: Invalid CLI arguments.\n" {
					t.Fatalf("conflicting formats did not produce YAML usage error: %#v", result)
				}
				if requests != 0 {
					t.Fatalf("conflicting formats reached the artifact server: %d requests", requests)
				}
				if _, err := os.Stat(outputPath); !os.IsNotExist(err) {
					t.Fatalf("conflicting formats created output path: %v", err)
				}
			})
		}
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
	if !strings.Contains(result.Stderr, "unknown artifact command") {
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

func TestArtifactGetAcceptsSelfSignedHTTPSArtifactURL(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.Method {
		case http.MethodHead:
			w.Header().Set("Content-Type", "text/plain; charset=utf-8")
			w.Header().Set("Content-Length", "12")
			w.WriteHeader(http.StatusOK)
		case http.MethodGet:
			w.Header().Set("Content-Type", "text/plain; charset=utf-8")
			fmt.Fprint(w, "hello tls\n")
		default:
			w.WriteHeader(http.StatusMethodNotAllowed)
		}
	}))
	t.Cleanup(server.Close)

	result := testutil.RunCLI(t, nil, "artifact", "get", server.URL+"/artifact", "--stdout")

	if result.Code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.Code, result.Stderr)
	}
	if result.Stdout != "hello tls\n" {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
}

func TestArtifactGetUsesConfiguredTLSCAFile(t *testing.T) {
	server := httptest.NewTLSServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		w.Header().Set("Content-Length", "12")
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(server.Close)
	caPath := filepath.Join(t.TempDir(), "not-a-ca.pem")
	if err := os.WriteFile(caPath, []byte("not a certificate"), 0o644); err != nil {
		t.Fatal(err)
	}
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig(fmt.Sprintf("arbiter:\n  tls_ca_file: %q\n", caPath))

	result := testutil.RunCLI(t, env, "artifact", "get", server.URL+"/artifact", "--stdout")

	if result.Code != 1 {
		t.Fatalf("expected exit code 1, got %d", result.Code)
	}
	if !strings.Contains(result.Stderr, "Invalid or unavailable client configuration.") {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
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
	if !strings.Contains(result.Stderr, "unknown artifact get argument") {
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

func TestArtifactChildStreamsPreserveStructuredSuccessAndFailure(t *testing.T) {
	for _, outputFormat := range []string{"--json", "--yaml"} {
		t.Run(outputFormat[2:], func(t *testing.T) {
			for _, childMode := range []string{"emit-success", "emit-failure"} {
				t.Run(childMode, func(t *testing.T) {
					server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
						w.WriteHeader(http.StatusOK)
						_, _ = w.Write([]byte("artifact input\n"))
					}))
					command := artifactHelperCommand(childMode)
					result := testutil.RunCLI(
						t,
						nil,
						"artifact",
						"with-stdin",
						server.URL,
						outputFormat,
						"--",
						command[0],
						command[1],
						command[2],
						command[3],
					)

					if childMode == "emit-success" {
						if result.Code != 0 || result.Stdout != "child-output\n" || result.Stderr != "child-diagnostic\n" {
							t.Fatalf("successful child streams changed: %#v", result)
						}
						return
					}
					if result.Code != 1 || result.Stdout != "" {
						t.Fatalf("failed child streams changed: %#v", result)
					}
					if strings.Contains(result.Stderr, "child-output") || strings.Contains(result.Stderr, "child-diagnostic") {
						t.Fatalf("raw failed-child output leaked: %q", result.Stderr)
					}
					if outputFormat == "--json" {
						var envelope map[string]map[string]any
						if err := json.Unmarshal([]byte(result.Stderr), &envelope); err != nil || envelope["error"] == nil {
							t.Fatalf("failed child did not produce one JSON envelope: %q", result.Stderr)
						}
					} else if !strings.HasPrefix(result.Stderr, "error:\n") {
						t.Fatalf("failed child did not produce one YAML envelope: %q", result.Stderr)
					}
				})
			}
		})
	}
}

func TestArtifactChildStreamsPreserveUnformattedSuccessAndFailure(t *testing.T) {
	for _, test := range []struct {
		childMode string
		code      int
		stderr    string
	}{
		{childMode: "emit-success", code: 0, stderr: "child-diagnostic\n"},
		{childMode: "emit-failure", code: 1, stderr: "child-diagnostic\nArbiter artifact error: Arbiter client request failed.\n"},
	} {
		t.Run(test.childMode, func(t *testing.T) {
			server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(http.StatusOK)
				_, _ = w.Write([]byte("artifact input\n"))
			}))
			command := artifactHelperCommand(test.childMode)
			result := testutil.RunCLI(
				t,
				nil,
				"artifact",
				"with-stdin",
				server.URL,
				"--",
				command[0],
				command[1],
				command[2],
				command[3],
			)
			if result.Code != test.code || result.Stdout != "child-output\n" || result.Stderr != test.stderr {
				t.Fatalf("unformatted child streams changed: %#v", result)
			}
		})
	}
}

func TestArtifactChildDataAfterDoubleDashIsPreserved(t *testing.T) {
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		_, _ = w.Write([]byte("artifact input\n"))
	}))
	command := artifactHelperCommand("print-args")
	result := testutil.RunCLI(
		t,
		nil,
		"artifact",
		"with-stdin",
		server.URL,
		"--",
		command[0],
		command[1],
		command[2],
		command[3],
		"--json",
	)
	if result.Code != 0 || result.Stdout != "arg:--json\n" || result.Stderr != "" {
		t.Fatalf("child data after -- was not preserved: %#v", result)
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

func TestArtifactStructuredErrorSuppressesRejectedChildStreams(t *testing.T) {
	for _, outputFormat := range []string{"--json", "--yaml"} {
		t.Run(outputFormat[2:], func(t *testing.T) {
			server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(http.StatusOK)
				_, _ = w.Write([]byte("artifact input\n"))
			}))
			command := artifactHelperCommand("binary-with-diagnostic")
			result := testutil.RunCLI(
				t,
				nil,
				"artifact",
				"with-stdin",
				server.URL,
				outputFormat,
				"--",
				command[0],
				command[1],
				command[2],
				command[3],
			)
			if result.Code != 1 || result.Stdout != "" || strings.Contains(result.Stderr, "child-diagnostic") {
				t.Fatalf("rejected child streams leaked: %#v", result)
			}
			if outputFormat == "--json" {
				var envelope map[string]map[string]any
				if err := json.Unmarshal([]byte(result.Stderr), &envelope); err != nil || envelope["error"] == nil {
					t.Fatalf("rejected child did not produce one JSON envelope: %q", result.Stderr)
				}
			} else if !strings.HasPrefix(result.Stderr, "error:\n") {
				t.Fatalf("rejected child did not produce one YAML envelope: %q", result.Stderr)
			}
		})
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
	case "emit-success":
		fmt.Fprint(os.Stdout, "child-output\n")
		fmt.Fprint(os.Stderr, "child-diagnostic\n")
	case "emit-failure":
		fmt.Fprint(os.Stdout, "child-output\n")
		fmt.Fprint(os.Stderr, "child-diagnostic\n")
		os.Exit(7)
	case "print-args":
		for _, arg := range args[1:] {
			fmt.Fprintf(os.Stdout, "arg:%s\n", arg)
		}
	case "binary-stdout":
		_, _ = os.Stdout.Write([]byte{0x00, 0xff})
	case "binary-with-diagnostic":
		fmt.Fprint(os.Stderr, "child-diagnostic\n")
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
