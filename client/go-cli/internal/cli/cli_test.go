package cli_test

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/omry/arbiter/client/go-cli/internal/cli"
	"github.com/omry/arbiter/client/go-cli/internal/testutil"
)

func TestMainPrintsVersion(t *testing.T) {
	result := testutil.RunCLI(t, nil, "--version")

	if result.Code != 0 {
		t.Fatalf("expected exit code 0, got %d", result.Code)
	}
	if result.Stdout != fmt.Sprintf("arbiter-go %s\n", cli.Version) {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
	if result.Stderr != "" {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
}

func TestVersionMatchesArbiterServerPackage(t *testing.T) {
	serverVersion := serverPyprojectVersion(t)

	if cli.Version != serverVersion {
		t.Fatalf("Go client version %q does not match arbiter-server version %q; run go generate ./internal/cli", cli.Version, serverVersion)
	}
}

func TestBootstrapClientWritesConfig(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)

	result := testutil.RunCLI(
		t,
		env,
		"--config-dir",
		"client-conf",
		"bootstrap",
		"client",
		"arbiter.mcp_url=http://example.test/mcp",
	)

	if result.Code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.Code, result.Stderr)
	}
	configPath := filepath.Join(env.Home, "client-conf", cli.DefaultClientConfigName)
	if result.Stdout != "wrote "+configPath+"\n" {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
	data, err := os.ReadFile(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != "arbiter:\n  mcp_url: \"http://example.test/mcp\"\n" {
		t.Fatalf("unexpected config:\n%s", data)
	}
}

func serverPyprojectVersion(t *testing.T) string {
	t.Helper()
	path := filepath.Join("..", "..", "..", "..", "server", "pyproject.toml")
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	inProject := false
	for _, line := range strings.Split(string(data), "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "[project]" {
			inProject = true
			continue
		}
		if inProject && strings.HasPrefix(trimmed, "[") {
			break
		}
		if !inProject || !strings.HasPrefix(trimmed, "version") {
			continue
		}
		key, value, ok := strings.Cut(trimmed, "=")
		if !ok || strings.TrimSpace(key) != "version" {
			continue
		}
		return strings.Trim(strings.TrimSpace(value), `"`)
	}
	t.Fatalf("%s does not define [project] version", path)
	return ""
}

func TestBootstrapClientRefusesOverwrite(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("arbiter:\n  mcp_url: http://old.test/mcp\n")

	result := testutil.RunCLI(t, env, "bootstrap", "client")

	if result.Code != 1 {
		t.Fatalf("expected exit code 1, got %d", result.Code)
	}
	if result.Stdout != "" {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
}

func TestResolveMCPURLPrefersOverride(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.SetEnv(cli.MCPURLEnvVar, "http://env.test/mcp")

	resolved, err := cli.ResolveMCPURL(
		[]string{"arbiter.mcp_url=http://example.test/mcp"},
		env.LookupEnv,
		env.HomeDir,
	)

	if err != nil {
		t.Fatal(err)
	}
	if resolved.URL != "http://example.test/mcp" {
		t.Fatalf("unexpected URL: %q", resolved.URL)
	}
	if resolved.Source != "override" {
		t.Fatalf("unexpected source: %q", resolved.Source)
	}
}

func TestResolveMCPURLUsesEnvironment(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.SetEnv(cli.MCPURLEnvVar, "http://env.test/mcp")

	resolved, err := cli.ResolveMCPURL(
		nil,
		env.LookupEnv,
		env.HomeDir,
	)

	if err != nil {
		t.Fatal(err)
	}
	if resolved.URL != "http://env.test/mcp" {
		t.Fatalf("unexpected URL: %q", resolved.URL)
	}
	if resolved.Source != cli.MCPURLEnvVar {
		t.Fatalf("unexpected source: %q", resolved.Source)
	}
}

func TestResolveMCPURLUsesClientConfig(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	configPath := env.WriteClientConfig("arbiter:\n  mcp_url: 'http://config.test/mcp'\n")

	resolved, err := cli.ResolveMCPURL(nil, env.LookupEnv, env.HomeDir)

	if err != nil {
		t.Fatal(err)
	}
	if resolved.URL != "http://config.test/mcp" {
		t.Fatalf("unexpected URL: %q", resolved.URL)
	}
	if resolved.Source != configPath {
		t.Fatalf("unexpected source: %q", resolved.Source)
	}
}

func TestResolveMCPURLRejectsTopLevelMCPURL(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("mcp_url: http://config.test/mcp\n")

	_, err := cli.ResolveMCPURL(nil, env.LookupEnv, env.HomeDir)

	if err == nil {
		t.Fatal("expected top-level mcp_url to fail")
	}
}

func TestResolveMCPURLRejectsUnknownClientConfigKeys(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("other:\n  mcp_url: http://config.test/mcp\n")

	_, err := cli.ResolveMCPURL(nil, env.LookupEnv, env.HomeDir)

	if err == nil {
		t.Fatal("expected unknown top-level key to fail")
	}
}

func TestResolveMCPURLRejectsUnknownArbiterConfigKeys(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("arbiter:\n  other: http://config.test/mcp\n")

	_, err := cli.ResolveMCPURL(nil, env.LookupEnv, env.HomeDir)

	if err == nil {
		t.Fatal("expected unknown arbiter key to fail")
	}
}

func TestResolveMCPURLRejectsUnknownArbiterConfigKeysAfterMCPURL(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("arbiter:\n  mcp_url: http://config.test/mcp\n  other: value\n")

	_, err := cli.ResolveMCPURL(nil, env.LookupEnv, env.HomeDir)

	if err == nil {
		t.Fatal("expected trailing unknown arbiter key to fail")
	}
}

func TestResolveMCPURLRejectsNonStringMCPURL(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("arbiter:\n  mcp_url: 123\n")

	_, err := cli.ResolveMCPURL(nil, env.LookupEnv, env.HomeDir)

	if err == nil {
		t.Fatal("expected non-string mcp_url to fail")
	}
}

func TestResolveMCPURLRejectsScalarArbiterConfig(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("arbiter: http://config.test/mcp\n")

	_, err := cli.ResolveMCPURL(nil, env.LookupEnv, env.HomeDir)

	if err == nil {
		t.Fatal("expected scalar arbiter config to fail")
	}
}

func TestResolveMCPURLDefaults(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)

	resolved, err := cli.ResolveMCPURL(nil, env.LookupEnv, env.HomeDir)

	if err != nil {
		t.Fatal(err)
	}
	if resolved.URL != cli.DefaultMCPURL {
		t.Fatalf("unexpected URL: %q", resolved.URL)
	}
	if resolved.Source != "default" {
		t.Fatalf("unexpected source: %q", resolved.Source)
	}
}
