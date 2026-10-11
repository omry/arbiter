package cli_test

import (
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/omry/arbiter/client/go-cli/internal/cli"
	"github.com/omry/arbiter/client/go-cli/internal/testutil"
)

func TestClientTimeoutDefaults(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	config, err := cli.ResolveClientConfig(nil, env.LookupEnv, env.HomeDir)
	if err != nil {
		t.Fatal(err)
	}
	if config.Timeout != 30*time.Second || config.ConnectTimeout != 10*time.Second || config.DownloadIdleTimeout != 30*time.Second {
		t.Fatalf("unexpected defaults: %+v", config)
	}
}

func TestAnnotatedTimeoutConfigPreservesLiteralHashes(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("arbiter: # client\n  url: https://example.test/path#fragment # URL\n  tls_ca_file: '/tmp/ca # copy.crt' # certificate\n  timeout: 30s # response budget\n  plugins: # overrides\n    imap:\n      timeout: # map\n        default: '2m' # fallback\n        search_messages: \"5m\" # slow operation\n")
	config, err := cli.ResolveClientConfig(nil, env.LookupEnv, env.HomeDir)
	if err != nil {
		t.Fatal(err)
	}
	if config.URL != "https://example.test/path#fragment" || config.TLSCAFile != "/tmp/ca # copy.crt" || config.Timeout != 30*time.Second || config.PluginTimeouts["imap"].Default != 2*time.Minute || config.PluginTimeouts["imap"].Operations["search_messages"] != 5*time.Minute {
		t.Fatalf("comments corrupted config: %+v", config)
	}
}

func TestTimeoutConfigSurvivesURLOverrides(t *testing.T) {
	for _, source := range []string{"override", "environment"} {
		t.Run(source, func(t *testing.T) {
			env := testutil.NewCLIEnvironment(t)
			env.WriteClientConfig("arbiter:\n  url: https://original.test\n  tls_ca_file: /tmp/ca.crt\n  timeout: 2m\n  connect_timeout: 15s\n  download_idle_timeout: 45s\n")
			var args []string
			if source == "override" {
				args = []string{"arbiter.url=https://override.test"}
			} else {
				env.SetEnv(cli.URLEnvVar, "https://override.test")
			}
			config, err := cli.ResolveClientConfig(args, env.LookupEnv, env.HomeDir)
			if err != nil {
				t.Fatal(err)
			}
			if config.URL != "https://override.test" || config.Timeout != 2*time.Minute || config.ConnectTimeout != 15*time.Second || config.DownloadIdleTimeout != 45*time.Second || config.TLSCAFile != "/tmp/ca.crt" {
				t.Fatalf("URL override discarded independent settings: %+v", config)
			}
		})
	}
}

func TestInvalidTimeoutConfigIsRejected(t *testing.T) {
	for name, data := range map[string]string{
		"zero":                 "arbiter:\n  timeout: 0s\n",
		"negative":             "arbiter:\n  connect_timeout: -1s\n",
		"no units":             "arbiter:\n  timeout: 30\n",
		"overflow":             "arbiter:\n  timeout: 99999999999999999999h\n",
		"boolean":              "arbiter:\n  download_idle_timeout: false\n",
		"duplicate global":     "arbiter:\n  timeout: 30s\n  timeout: 5m\n",
		"duplicate plugin":     "arbiter:\n  plugins:\n    imap:\n      timeout: 30s\n    imap:\n      timeout: 5m\n",
		"duplicate operation":  "arbiter:\n  plugins:\n    imap:\n      timeout:\n        search_messages: 5m\n        search_messages: 2m\n",
		"unknown plugin key":   "arbiter:\n  plugins:\n    imap:\n      timeuot: 5m\n",
		"bad operation budget": "arbiter:\n  plugins:\n    imap:\n      timeout:\n        search_messages: no\n",
		"nested duration":      "arbiter:\n  plugins:\n    imap:\n      timeout:\n        search_messages:\n          value: 5m\n",
		"empty timeout map":    "arbiter:\n  plugins:\n    imap:\n      timeout:\n",
		"bad indentation":      "arbiter:\n  timeout: 30s\n    connect_timeout: 10s\n",
		"tabs":                 "arbiter:\n\ttimeout: 30s\n",
	} {
		t.Run(name, func(t *testing.T) {
			env := testutil.NewCLIEnvironment(t)
			path := env.WriteClientConfig(data)
			_, err := cli.ResolveClientConfig(nil, env.LookupEnv, env.HomeDir)
			if err == nil || !strings.Contains(err.Error(), path) {
				t.Fatalf("expected invalid config error naming its path, got %v", err)
			}
		})
	}
}

func TestOperationTimeoutPrecedenceThroughCLI(t *testing.T) {
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
			return
		case <-time.After(120 * time.Millisecond):
			_, _ = io.WriteString(w, `{"result":{"ok":true}}`)
		}
	}))
	for _, tc := range []struct {
		name, plugins, operation, override string
		pass                               bool
	}{
		{name: "global fallback", operation: "imap:search_messages"},
		{name: "scalar plugin", plugins: "    imap:\n      timeout: 300ms\n", operation: "imap:search_messages", pass: true},
		{name: "map default", plugins: "    imap:\n      timeout:\n        default: 300ms\n", operation: "imap:other", pass: true},
		{name: "operation wins", plugins: "    imap:\n      timeout:\n        default: 60ms\n        search_messages: 300ms\n", operation: "imap:search_messages", pass: true},
		{name: "operation shortens", plugins: "    imap:\n      timeout:\n        default: 300ms\n        search_messages: 60ms\n", operation: "imap:search_messages"},
		{name: "map without default", plugins: "    imap:\n      timeout:\n        search_messages: 300ms\n", operation: "imap:other"},
		{name: "other plugin", plugins: "    imap:\n      timeout: 300ms\n", operation: "smtp:send_email"},
		{name: "CLI wins", plugins: "    imap:\n      timeout:\n        default: 300ms\n        search_messages: 300ms\n", operation: "imap:search_messages", override: "arbiter.timeout=60ms"},
		{name: "CLI extends", operation: "imap:search_messages", override: "arbiter.timeout=300ms", pass: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			env := testutil.NewCLIEnvironment(t)
			config := fmt.Sprintf("arbiter:\n  url: %s\n  timeout: 60ms\n", server.URL)
			if tc.plugins != "" {
				config += "  plugins:\n" + tc.plugins
			}
			env.WriteClientConfig(config)
			args := []string{"op", "run", tc.operation, "--args", "{}"}
			if tc.override != "" {
				args = append(args, tc.override)
			}
			result := testutil.RunCLI(t, env, args...)
			if tc.pass {
				if result.Code != 0 || !strings.Contains(result.Stdout, `"ok":true`) {
					t.Fatalf("expected success: %+v", result)
				}
			} else if result.Code == 0 || !strings.Contains(result.Stderr, "API response timeout") {
				t.Fatalf("expected response timeout: %+v", result)
			}
		})
	}
}

func TestDiscoveryUsesGlobalBudget(t *testing.T) {
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-r.Context().Done():
		case <-time.After(120 * time.Millisecond):
			_, _ = io.WriteString(w, `{"plugins":[]}`)
		}
	}))
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig(fmt.Sprintf("arbiter:\n  url: %s\n  timeout: 60ms\n  plugins:\n    imap:\n      timeout: 300ms\n", server.URL))
	result := testutil.RunCLI(t, env, "plugins", "imap")
	if result.Code == 0 || !strings.Contains(result.Stderr, "API response timeout") {
		t.Fatalf("plugin execution timeout leaked into discovery: %+v", result)
	}
}

func TestArtifactSaveIgnoresAPIBudgetAndResetsIdleBudget(t *testing.T) {
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		if r.Method == http.MethodHead {
			w.Header().Set("Content-Length", "8")
			return
		}
		for i := 0; i < 8; i++ {
			_, _ = io.WriteString(w, "x")
			w.(http.Flusher).Flush()
			time.Sleep(30 * time.Millisecond)
		}
	}))
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("arbiter:\n  timeout: 20ms\n  download_idle_timeout: 150ms\n")
	output := env.Home + "/download"
	result := testutil.RunCLI(t, env, "artifact", "save", server.URL, output, "arbiter.timeout=10ms")
	if result.Code != 0 {
		t.Fatalf("healthy download used API budget: %+v", result)
	}
	data, err := os.ReadFile(output)
	if err != nil || string(data) != "xxxxxxxx" {
		t.Fatalf("unexpected download: %q, %v", data, err)
	}
}

func TestFailedIdleDownloadRemovesPartialOutput(t *testing.T) {
	server := testutil.NewHTTPServer(t, http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain")
		if r.Method == http.MethodHead {
			w.Header().Set("Content-Length", "100")
			return
		}
		_, _ = io.WriteString(w, "partial")
		w.(http.Flusher).Flush()
		<-r.Context().Done()
	}))
	env := testutil.NewCLIEnvironment(t)
	output := env.Home + "/download"
	result := testutil.RunCLI(t, env, "artifact", "save", server.URL, output, "arbiter.download_idle_timeout=60ms")
	if result.Code == 0 || !strings.Contains(result.Stderr, "download inactivity timeout") {
		t.Fatalf("expected inactivity timeout: %+v", result)
	}
	if _, err := os.Stat(output); !os.IsNotExist(err) {
		t.Fatalf("partial output survived timeout: %v", err)
	}
}

func TestTimeoutOverridesValidateBeforeConnecting(t *testing.T) {
	for _, key := range []string{"timeout", "connect_timeout", "download_idle_timeout"} {
		result := testutil.RunCLI(t, nil, "info", "server", "arbiter."+key+"=0s")
		if result.Code == 0 || !strings.Contains(result.Stderr, "arbiter."+key+" must be a positive duration") {
			t.Fatalf("invalid override reached transport: %+v", result)
		}
	}
}

func TestBootstrapPersistsTimeoutOverrides(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	result := testutil.RunCLI(t, env, "bootstrap", "client", "arbiter.timeout=5m", "arbiter.connect_timeout=15s", "arbiter.download_idle_timeout=45s")
	if result.Code != 0 {
		t.Fatalf("bootstrap failed: %+v", result)
	}
	config, err := cli.ResolveClientConfig(nil, env.LookupEnv, env.HomeDir)
	if err != nil || config.Timeout != 5*time.Minute || config.ConnectTimeout != 15*time.Second || config.DownloadIdleTimeout != 45*time.Second {
		t.Fatalf("bootstrap discarded overrides: %+v, %v", config, err)
	}
}
