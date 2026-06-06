package testutil

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"

	"github.com/omry/arbiter/client/go-cli/internal/cli"
)

type CLIEnvironment struct {
	t    *testing.T
	Home string
	Vars map[string]string
}

type CLIResult struct {
	Code   int
	Stdout string
	Stderr string
}

func NewCLIEnvironment(t *testing.T) *CLIEnvironment {
	t.Helper()
	return &CLIEnvironment{
		t:    t,
		Home: t.TempDir(),
		Vars: map[string]string{},
	}
}

func (e *CLIEnvironment) SetEnv(name string, value string) {
	e.Vars[name] = value
}

func (e *CLIEnvironment) LookupEnv(name string) (string, bool) {
	value, ok := e.Vars[name]
	return value, ok
}

func (e *CLIEnvironment) HomeDir() (string, error) {
	return e.Home, nil
}

func (e *CLIEnvironment) WriteClientConfig(content string) string {
	e.t.Helper()
	configDir := filepath.Join(e.Home, cli.DefaultConfigDir)
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		e.t.Fatal(err)
	}
	configPath := filepath.Join(configDir, cli.DefaultClientConfigName)
	if err := os.WriteFile(configPath, []byte(content), 0o644); err != nil {
		e.t.Fatal(err)
	}
	return configPath
}

func RunCLI(t *testing.T, env *CLIEnvironment, args ...string) CLIResult {
	t.Helper()
	if env == nil {
		env = NewCLIEnvironment(t)
	}

	var stdout bytes.Buffer
	var stderr bytes.Buffer
	code := cli.Main(args, &stdout, &stderr, env.LookupEnv, env.HomeDir)
	return CLIResult{
		Code:   code,
		Stdout: stdout.String(),
		Stderr: stderr.String(),
	}
}

func NewHTTPServer(t *testing.T, handler http.Handler) *httptest.Server {
	t.Helper()
	server := httptest.NewServer(handler)
	t.Cleanup(server.Close)
	return server
}

func WriteJSON(t *testing.T, w http.ResponseWriter, status int, value any) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(value); err != nil {
		t.Fatal(err)
	}
}
