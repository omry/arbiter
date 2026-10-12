package cli

import (
	"bytes"
	"encoding/json"
	"errors"
	"github.com/omry/arbiter/client/go-cli/internal/arbiterhttp"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
)

func TestStructuredErrorPreservesServerFieldsAndUsesStderr(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.err = arbiterhttp.APIError{StatusCode: 500, Code: "partial_failure", Message: "Sent copy failed.", Fields: map[string]any{
		"plugin": "smtp", "domain_code": "sent_copy_failed",
		"details": map[string]any{"steps": map[string]any{"smtp_submission": "succeeded", "sent_copy": "failed"}},
		"retry":   map[string]any{"transient": "unknown", "replay": "unsafe"},
	}}
	for _, command := range [][]string{
		{"op", "run", "smtp:send_email", "--json"},
		{"op", "desc", "smtp:send_email", "--json"},
		{"op", "list", "--json"}, {"plugins", "--json"}, {"info", "server", "--json"},
	} {
		result := runTestCLI(command...)
		if result.code != 1 || result.stdout != "" {
			t.Fatalf("bad streams/exit for %q: %#v", command, result)
		}
		var envelope map[string]map[string]any
		if err := json.Unmarshal([]byte(result.stderr), &envelope); err != nil {
			t.Fatalf("not JSON: %s", result.stderr)
		}
		if envelope["error"]["domain_code"] != "sent_copy_failed" || envelope["error"]["retry"] == nil || envelope["error"]["details"] == nil {
			t.Fatalf("lost fields: %s", result.stderr)
		}
	}
	result := runTestCLI("op", "run", "smtp:send_email", "--yaml")
	if result.code != 1 || result.stdout != "" || !strings.Contains(result.stderr, "domain_code: sent_copy_failed") || !strings.Contains(result.stderr, "retry:") {
		t.Fatalf("bad YAML: %#v", result)
	}
}

func TestStructuredUsageErrorIsSingleEnvelope(t *testing.T) {
	result := runTestCLI("op", "run", "smtp:send_email", "--args", "not-json", "--json")
	var envelope map[string]map[string]any
	if result.code != 2 || result.stdout != "" {
		t.Fatalf("bad streams/exit: %#v", result)
	}
	if err := json.Unmarshal([]byte(result.stderr), &envelope); err != nil || envelope["error"]["code"] != "usage_error" {
		t.Fatalf("bad error: %s", result.stderr)
	}
}

func TestErrorFormatHelpListsAcceptedFlags(t *testing.T) {
	for _, test := range []struct {
		name string
		args []string
		want []string
	}{
		{
			name: "info-server",
			args: []string{"info", "server", "--help"},
			want: []string{"usage: arbiter info server [--json|--yaml]", "--json", "--yaml"},
		},
		{
			name: "op-run",
			args: []string{"op", "run", "--help"},
			want: []string{"usage: arbiter op run <operation-id> [--args <json-object>] [--json|--yaml]", "--args JSON", "--json", "--yaml"},
		},
	} {
		t.Run(test.name, func(t *testing.T) {
			result := runTestCLI(test.args...)
			if result.code != 0 || result.stderr != "" {
				t.Fatalf("unexpected help result: %#v", result)
			}
			for _, want := range test.want {
				if !strings.Contains(result.stdout, want) {
					t.Fatalf("help missing %q: %s", want, result.stdout)
				}
			}
		})
	}
}

func TestConfigAndBootstrapFormatFlagsPreserveSuccess(t *testing.T) {
	for _, format := range []string{"--json", "--yaml"} {
		t.Run("config/"+format[2:], func(t *testing.T) {
			home := t.TempDir()
			configDir := filepath.Join(home, "config")
			if err := os.MkdirAll(configDir, 0o755); err != nil {
				t.Fatal(err)
			}
			configPath := filepath.Join(configDir, DefaultClientConfigName)
			if err := os.WriteFile(configPath, []byte("arbiter:\n  url: https://config.test\n"), 0o644); err != nil {
				t.Fatal(err)
			}
			baseline := runTestCLIWithHome(home, "--config-dir", configDir, "config", "url")
			result := runTestCLIWithHome(home, "--config-dir", configDir, "config", "url", format)
			if baseline.code != 0 || result.code != 0 || result.stderr != "" || result.stdout != baseline.stdout {
				t.Fatalf("config success changed: baseline=%#v result=%#v", baseline, result)
			}
		})

		t.Run("bootstrap/"+format[2:], func(t *testing.T) {
			home := t.TempDir()
			configDir := filepath.Join(home, "bootstrap-config")
			configPath := filepath.Join(configDir, DefaultClientConfigName)
			result := runTestCLIWithHome(home, "--config-dir", configDir, "bootstrap", "client", format)
			if result.code != 0 || result.stderr != "" || result.stdout != "wrote "+configPath+"\n" {
				t.Fatalf("bootstrap success changed: %#v", result)
			}
			if _, err := os.Stat(configPath); err != nil {
				t.Fatalf("bootstrap did not write config: %v", err)
			}
		})
	}
}

func TestConfigAndBootstrapFormatFlagsStructureClientConfigErrors(t *testing.T) {
	for _, format := range []string{"--json", "--yaml"} {
		t.Run("config-missing/"+format[2:], func(t *testing.T) {
			home := t.TempDir()
			configDir := filepath.Join(home, "not-a-directory")
			if err := os.WriteFile(configDir, []byte("not a directory"), 0o644); err != nil {
				t.Fatal(err)
			}
			result := runTestCLIWithHome(home, "--config-dir", configDir, "config", "url", format)
			assertStructuredClientConfigError(t, result, format)
		})

		t.Run("config-malformed/"+format[2:], func(t *testing.T) {
			home := t.TempDir()
			configDir := filepath.Join(home, "config")
			if err := os.MkdirAll(configDir, 0o755); err != nil {
				t.Fatal(err)
			}
			configPath := filepath.Join(configDir, DefaultClientConfigName)
			if err := os.WriteFile(configPath, []byte("arbiter: [malformed\n"), 0o644); err != nil {
				t.Fatal(err)
			}
			result := runTestCLIWithHome(home, "--config-dir", configDir, "config", "url", format)
			assertStructuredClientConfigError(t, result, format)
		})

		t.Run("bootstrap-existing/"+format[2:], func(t *testing.T) {
			home := t.TempDir()
			configDir := filepath.Join(home, "config")
			if err := os.MkdirAll(configDir, 0o755); err != nil {
				t.Fatal(err)
			}
			configPath := filepath.Join(configDir, DefaultClientConfigName)
			if err := os.WriteFile(configPath, []byte("arbiter:\n  url: https://existing.test\n"), 0o644); err != nil {
				t.Fatal(err)
			}
			result := runTestCLIWithHome(home, "--config-dir", configDir, "bootstrap", "client", format)
			assertStructuredClientConfigError(t, result, format)
		})
	}
}

func assertStructuredClientConfigError(t *testing.T, result cliResult, format string) {
	t.Helper()
	if result.code != 1 || result.stdout != "" {
		t.Fatalf("client config error changed exit or stdout: %#v", result)
	}
	if format == "--json" {
		var envelope map[string]map[string]any
		if json.Unmarshal([]byte(result.stderr), &envelope) != nil || envelope["error"]["code"] != "client_config_error" {
			t.Fatalf("invalid JSON client config envelope: %#v", result)
		}
	} else if result.stderr != "error:\n  code: client_config_error\n  message: Invalid or unavailable client configuration.\n" {
		t.Fatalf("invalid YAML client config envelope: %#v", result)
	}
}

func runTestCLIWithHome(home string, args ...string) cliResult {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := Main(
		args,
		&stdout,
		&stderr,
		func(string) (string, bool) {
			return "", false
		},
		func() (string, error) {
			return home, nil
		},
	)
	return cliResult{code: code, stdout: stdout.String(), stderr: stderr.String()}
}

func TestStructuredLocalErrorDoesNotExposeExceptionText(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.err = errors.New("password=secret")
	result := runTestCLI("plugins", "--json")
	if result.code != 1 || strings.Contains(result.stderr, "secret") {
		t.Fatalf("unsafe output: %#v", result)
	}
}

func TestHumanTransportErrorOmitsCredentialURL(t *testing.T) {
	var out bytes.Buffer
	err := &url.Error{Op: "Get", URL: "https://user:secret@example.com/path?token=secret", Err: errors.New("private body")}
	printClientError(&out, err, "tool")
	if strings.Contains(out.String(), "secret") || strings.Contains(out.String(), "private body") {
		t.Fatalf("unsafe diagnostic: %s", out.String())
	}
}

func TestYAMLErrorEscapesMessageAndPreservesRetryStrings(t *testing.T) {
	var out bytes.Buffer
	err := arbiterhttp.APIError{Code: "plugin_error", Message: "failure: selected\nexplanation", Fields: map[string]any{"retry": map[string]any{"transient": "no", "replay": "unknown"}}}
	printClientError(&errorWriter{Writer: &out, format: outputYAML}, err, "tool")
	if !strings.Contains(out.String(), `message: "failure: selected\nexplanation"`) || !strings.Contains(out.String(), `transient: "no"`) {
		t.Fatalf("invalid YAML string rendering: %s", out.String())
	}
}

func TestYAMLScalarQuotesIndicatorLeadingAndReservedStrings(t *testing.T) {
	values := []string{
		"%directive", "&anchor", "*alias", "!tag", "|block", ">fold", "@reserved", "`reserved`",
		"-dash", "?question", ":colon", "[sequence]", "{mapping}", ",comma", "#comment",
		"yes", "NO", "true", "Null", "~", "<<", "0", "+1", ".nan", " leading", "trailing ",
		"before\x00after", "before\u0085after", "before\u2028after", "smtp:send_email",
	}
	for _, value := range values {
		got, ok := yamlScalar(value).(string)
		if !ok {
			t.Fatalf("yamlScalar(%q) returned %T", value, yamlScalar(value))
		}
		want := strconv.QuoteToASCII(value)
		if got != want {
			t.Errorf("yamlScalar(%q) = %q, want %q", value, got, want)
		}
	}
	for _, value := range []string{"arbiter", "smtp_send", "path/to-artifact", "Send mail"} {
		if got := yamlScalar(value); got != value {
			t.Errorf("yamlScalar(%q) = %v, want plain scalar", value, got)
		}
	}
}

func TestStructuredArtifactErrorsAndEmptyUsage(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(410)
		w.Write([]byte(`{"error":{"code":"artifact_consumed","message":"Already consumed.","details":{"state":"consumed"}}}`))
	}))
	defer server.Close()
	for _, args := range [][]string{
		{"artifact", "--json"},
		{"artifact", "save", server.URL, t.TempDir() + "/artifact", "--json"},
		{"artifact", "with-temp", server.URL, "--json", "--", "unused", "{}"},
		{"artifact", "with-stdin", server.URL, "--json", "--", "unused"},
	} {
		result := runTestCLI(args...)
		var envelope map[string]map[string]any
		if result.stdout != "" || json.Unmarshal([]byte(result.stderr), &envelope) != nil {
			t.Fatalf("invalid streams/envelope: %#v", result)
		}
		if len(args) == 2 {
			if result.code != 2 || envelope["error"]["code"] != "usage_error" {
				t.Fatal(result)
			}
		} else if result.code != 1 || envelope["error"]["code"] != "artifact_consumed" || envelope["error"]["details"] == nil {
			t.Fatalf("lost server fields: %#v", result)
		}
	}
}

func TestErrorFormatDoesNotReadPayloadOrChildArguments(t *testing.T) {
	for _, args := range [][]string{
		{"op", "run", "smtp:send_email", "--args", "--yaml"},
		{"artifact", "with-temp", "url", "--", "child", "--json"},
		{"--config-dir", "--yaml", "plugins"},
		{"artifact", "get", "url", "--max-bytes", "--json"},
		{"artifact", "get", "url", "--max-bytes", "--yaml"},
		{"artifact", "with-stdin", "url", "--max-child-stdout-bytes", "--json"},
		{"artifact", "with-stdin", "url", "--max-child-stdout-bytes", "--yaml"},
	} {
		if got := requestedErrorFormat(args); got != "" {
			t.Fatalf("data became format: %q %q", args, got)
		}
	}
}

func TestArtifactErrorFormatValuesAreData(t *testing.T) {
	for _, command := range []struct {
		prefix []string
		option string
		suffix []string
	}{
		{[]string{"artifact", "get", "http://127.0.0.1:9/artifact", "--stdout"}, "--max-bytes", nil},
		{[]string{"artifact", "with-stdin", "http://127.0.0.1:9/artifact"}, "--max-child-stdout-bytes", []string{"--", "unused"}},
	} {
		for _, value := range []string{"--json", "--yaml"} {
			t.Run(command.option+"/"+value, func(t *testing.T) {
				args := append(append([]string{}, command.prefix...), command.option, value)
				plain := append(append([]string{}, args...), command.suffix...)
				result := runTestCLI(plain...)
				if result.code != 2 || result.stdout != "" || !strings.HasPrefix(result.stderr, "Arbiter usage error:") {
					t.Fatalf("option value selected an error format: %#v", result)
				}
				for _, format := range []string{"--json", "--yaml"} {
					formatted := append(append([]string{}, args...), format)
					formatted = append(formatted, command.suffix...)
					result = runTestCLI(formatted...)
					if result.code != 2 || result.stdout != "" {
						t.Fatalf("genuine %s output flag changed exit or stdout: %#v", format, result)
					}
					if format == "--json" {
						var envelope map[string]map[string]any
						if json.Unmarshal([]byte(result.stderr), &envelope) != nil || envelope["error"]["code"] != "usage_error" {
							t.Fatalf("genuine JSON output flag lost: %#v", result)
						}
					} else if result.stderr != "error:\n  code: usage_error\n  message: Invalid CLI arguments.\n" {
						t.Fatalf("genuine YAML output flag lost: %#v", result)
					}
				}
			})
		}
	}
}

func TestNonArtifactSizeLikeOptionsPreserveFormats(t *testing.T) {
	for _, command := range []struct {
		name   string
		prefix []string
	}{
		{name: "op-list", prefix: []string{"op", "list"}},
		{name: "op-list-artifact-operand", prefix: []string{"op", "list", "artifact"}},
	} {
		for _, option := range []string{"--max-bytes", "--max-child-stdout-bytes"} {
			for _, format := range []string{"--json", "--yaml"} {
				t.Run(command.name+"/"+option+"/"+format, func(t *testing.T) {
					args := append(append([]string{}, command.prefix...), option, format)
					assertStructuredUsageFormat(t, runTestCLI(args...), format)
				})
			}
		}
	}
}

func TestArtifactErrorFormatValuesWithGlobalOptions(t *testing.T) {
	for _, command := range []struct {
		name   string
		prefix []string
		option string
		suffix []string
	}{
		{
			name:   "get",
			prefix: []string{"--config-dir", "test-config", "arbiter.timeout=1s", "artifact", "get", "url", "--stdout"},
			option: "--max-bytes",
		},
		{
			name:   "with-stdin",
			prefix: []string{"--config-name", "test.yaml", "arbiter.connect_timeout=1s", "artifact", "with-stdin", "url"},
			option: "--max-child-stdout-bytes",
			suffix: []string{"--", "unused"},
		},
	} {
		for _, value := range []string{"--json", "--yaml"} {
			for _, format := range []string{"--json", "--yaml"} {
				t.Run(command.name+"/"+value+"/"+format, func(t *testing.T) {
					args := append(append(append([]string{}, command.prefix...), command.option, value), format)
					args = append(args, command.suffix...)
					assertStructuredUsageFormat(t, runTestCLI(args...), format)
				})
			}
		}
	}
}

func TestForeignArtifactSizeOptionsPreserveFormats(t *testing.T) {
	for _, command := range []struct {
		name   string
		prefix []string
		option string
		suffix []string
	}{
		{
			name:   "save-max-bytes",
			prefix: []string{"artifact", "save", "url", "path"},
			option: "--max-bytes",
		},
		{
			name:   "save-max-child-stdout-bytes",
			prefix: []string{"artifact", "save", "url", "path"},
			option: "--max-child-stdout-bytes",
		},
		{
			name:   "get-max-child-stdout-bytes",
			prefix: []string{"artifact", "get", "url", "--stdout"},
			option: "--max-child-stdout-bytes",
		},
		{
			name:   "with-temp-max-bytes",
			prefix: []string{"artifact", "with-temp", "url"},
			option: "--max-bytes",
			suffix: []string{"--", "child"},
		},
		{
			name:   "with-stdin-max-bytes",
			prefix: []string{"artifact", "with-stdin", "url"},
			option: "--max-bytes",
			suffix: []string{"--", "child"},
		},
	} {
		for _, format := range []string{"--json", "--yaml"} {
			t.Run(command.name+"/"+format[2:], func(t *testing.T) {
				args := append(append(append([]string{}, command.prefix...), command.option, "10", format), command.suffix...)
				assertStructuredUsageFormat(t, runTestCLI(args...), format)
			})
		}
	}
}

func TestArtifactFormatBeforeSubcommandPreservesForeignValueContext(t *testing.T) {
	for _, format := range []string{"--json", "--yaml"} {
		args := []string{"artifact", format, "save", "url", "path", "--max-bytes", "10"}
		assertStructuredUsageFormat(t, runTestCLI(args...), format)
	}
}

func TestOperationArgsDoNotSelectErrorFormat(t *testing.T) {
	for _, format := range []string{"--json", "--yaml"} {
		result := runTestCLI("op", "run", "smtp:send_email", "--args", format)
		if result.code != 2 || result.stdout != "" || !strings.HasPrefix(result.stderr, "Arbiter usage error:") {
			t.Fatalf("operation args selected an error format: %#v", result)
		}
	}
}

func TestNonOperationArgsPreserveErrorFormat(t *testing.T) {
	for _, command := range []string{"plugins", "op", "artifact"} {
		for _, format := range []string{"--json", "--yaml"} {
			args := []string{command, "--args", format}
			if command == "op" {
				args = []string{"op", "list", "--args", format}
			}
			result := runTestCLI(args...)
			assertStructuredUsageFormat(t, result, format)
		}
	}
}

func TestUnknownTopLevelSizeLikeOptionsPreserveFormats(t *testing.T) {
	for _, test := range []struct {
		command string
		option  string
	}{
		{command: "get", option: "--max-bytes"},
		{command: "with-temp", option: "--max-child-stdout-bytes"},
		{command: "with-stdin", option: "--max-child-stdout-bytes"},
	} {
		for _, format := range []string{"--json", "--yaml"} {
			t.Run(test.command+"/"+format[2:], func(t *testing.T) {
				result := runTestCLI(test.command, test.option, format)
				assertStructuredUsageFormat(t, result, format)
			})
		}
	}
}

func assertStructuredUsageFormat(t *testing.T, result cliResult, format string) {
	t.Helper()
	if result.code != 2 || result.stdout != "" {
		t.Fatalf("genuine %s output flag changed exit or stdout: %#v", format, result)
	}
	if format == "--json" {
		var envelope map[string]map[string]any
		if json.Unmarshal([]byte(result.stderr), &envelope) != nil || envelope["error"]["code"] != "usage_error" {
			t.Fatalf("genuine JSON output flag lost: %#v", result)
		}
	} else if result.stderr != "error:\n  code: usage_error\n  message: Invalid CLI arguments.\n" {
		t.Fatalf("genuine YAML output flag lost: %#v", result)
	}
}

func TestHumanAPIErrorWithEmptyMessageShowsCode(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.err = arbiterhttp.APIError{StatusCode: 500, Code: "plugin_error"}
	result := runTestCLI("plugins")
	if result.code != 1 || result.stdout != "" || !strings.Contains(result.stderr, "plugin_error") {
		t.Fatalf("missing error classification: %#v", result)
	}
}

func TestOperationRunRejectsStrayPositionalsWithoutInvoking(t *testing.T) {
	for _, args := range [][]string{
		{"op", "run", "smtp:send_email", "stray"},
		{"op", "run", "smtp:send_email", "stray", "--yaml"},
		{"op", "run", "smtp:send_email", "--json", "stray"},
		{"op", "run", "smtp:send_email", "--args", "{}", "stray"},
	} {
		t.Run(strings.Join(args, "/"), func(t *testing.T) {
			fake := installFakeArbiterClient(t)
			result := runTestCLI(args...)
			if result.code != 2 || result.stdout != "" || len(fake.calls) != 0 {
				t.Fatalf("stray positional ran an operation: result=%#v calls=%#v", result, fake.calls)
			}
		})
	}
}
