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
	if result.Stdout != fmt.Sprintf("arbiter %s\n", cli.Version) {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
	if result.Stderr != "" {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
}

func TestGoClientHelp(t *testing.T) {
	tests := []struct {
		name       string
		args       []string
		expected   []string
		unexpected []string
	}{
		{
			name: "top-level",
			args: []string{"--help"},
			expected: []string{
				"usage: arbiter",
				"primary commands:",
				"info",
				"plugins",
				"op",
				"artifact",
				"--help --extended",
			},
			unexpected: []string{
				"bootstrap",
				"config",
			},
		},
		{
			name: "top-level extended",
			args: []string{"--help", "--extended"},
			expected: []string{
				"usage: arbiter",
				"primary commands:",
				"setup:",
				"bootstrap",
				"config",
			},
			unexpected: []string{},
		},
		{
			name: "bootstrap",
			args: []string{"bootstrap", "--help"},
			expected: []string{
				"usage: arbiter bootstrap",
				"client",
			},
		},
		{
			name: "bootstrap client",
			args: []string{"bootstrap", "client", "--help"},
			expected: []string{
				"usage: arbiter bootstrap client",
				"--force",
			},
		},
		{
			name: "config",
			args: []string{"config", "--help"},
			expected: []string{
				"usage: arbiter config",
				"url",
			},
		},
		{
			name: "config url",
			args: []string{"config", "url", "--help"},
			expected: []string{
				"usage: arbiter config url",
				"URL resolved",
			},
		},
		{
			name: "info",
			args: []string{"info", "--help"},
			expected: []string{
				"usage: arbiter info",
				"server",
			},
			unexpected: []string{
				"--short",
				"ops",
			},
		},
		{
			name: "info server",
			args: []string{"info", "server", "--help"},
			expected: []string{
				"usage: arbiter info server",
				"source metadata",
			},
		},
		{
			name: "plugins",
			args: []string{"plugins", "--help"},
			expected: []string{
				"usage: arbiter plugins",
				"accounts",
				"policy NAME",
				"--yaml",
			},
		},
		{
			name: "op",
			args: []string{"op", "--help"},
			expected: []string{
				"usage: arbiter op",
				"list",
				"<plugin>:<operation>",
				"desc",
				"run",
			},
		},
		{
			name: "op list",
			args: []string{"op", "list", "--help"},
			expected: []string{
				"usage: arbiter op list",
				"[plugin]",
				"--json",
				"--yaml",
			},
			unexpected: []string{
				"--plain",
			},
		},
		{
			name: "op desc",
			args: []string{"op", "desc", "--help"},
			expected: []string{
				"usage: arbiter op desc",
				"<plugin-or-operation-id>",
				"imap:get_message",
				"--yaml",
			},
			unexpected: []string{
				"--plain",
			},
		},
		{
			name: "op describe",
			args: []string{"op", "describe", "--help"},
			expected: []string{
				"usage: arbiter op describe",
				"<plugin-or-operation-id>",
				"imap:get_message",
				"--yaml",
			},
			unexpected: []string{
				"--plain",
			},
		},
		{
			name: "op run",
			args: []string{"op", "run", "--help"},
			expected: []string{
				"usage: arbiter op run",
				"arbiter op list <plugin>",
				"--args",
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
			for _, unexpected := range tt.unexpected {
				if strings.Contains(result.Stdout, unexpected) {
					t.Fatalf("expected stdout not to contain %q, got %q", unexpected, result.Stdout)
				}
			}
		})
	}
}

func TestGoClientInvalidSubcommandSuggestsHelp(t *testing.T) {
	result := testutil.RunCLI(t, nil, "info", "aa")

	if result.Code != 2 {
		t.Fatalf("expected exit code 2, got %d", result.Code)
	}
	if result.Stdout != "" {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
	if !strings.Contains(result.Stderr, "expected: arbiter info server") {
		t.Fatalf("expected unknown command error, got %q", result.Stderr)
	}
	if !strings.Contains(result.Stderr, "Run 'arbiter info --help' for help.") {
		t.Fatalf("expected help hint, got %q", result.Stderr)
	}
}

func TestConfigWithoutSubcommandPrintsHelp(t *testing.T) {
	result := testutil.RunCLI(t, nil, "config")

	if result.Code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.Code, result.Stderr)
	}
	if result.Stderr != "" {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
	if !strings.Contains(result.Stdout, "usage: arbiter config") ||
		!strings.Contains(result.Stdout, "url") {
		t.Fatalf("expected config help, got %q", result.Stdout)
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
		"arbiter.url=http://example.test",
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
	if string(data) != "arbiter:\n  url: \"http://example.test\"\n" {
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
	env.WriteClientConfig("arbiter:\n  url: http://old.test\n")

	result := testutil.RunCLI(t, env, "bootstrap", "client")

	if result.Code != 1 {
		t.Fatalf("expected exit code 1, got %d", result.Code)
	}
	if result.Stdout != "" {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
}

func TestConfigURLPrintsDefaultSource(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)

	result := testutil.RunCLI(t, env, "config", "url")

	if result.Code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.Code, result.Stderr)
	}
	if result.Stderr != "" {
		t.Fatalf("unexpected stderr: %q", result.Stderr)
	}
	expected := "url: " + cli.DefaultURL + "\nsource: default\n"
	if result.Stdout != expected {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
}

func TestConfigURLPrintsEnvironmentSource(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.SetEnv(cli.URLEnvVar, "http://env.test")

	result := testutil.RunCLI(t, env, "config", "url")

	if result.Code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.Code, result.Stderr)
	}
	expected := "url: http://env.test\nsource: " + cli.URLEnvVar + "\n"
	if result.Stdout != expected {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
}

func TestConfigURLPrintsClientConfigSource(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	configPath := env.WriteClientConfig("arbiter:\n  url: 'http://config.test'\n")

	result := testutil.RunCLI(t, env, "config", "url")

	if result.Code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.Code, result.Stderr)
	}
	expected := "url: http://config.test\nsource: " + configPath + "\n"
	if result.Stdout != expected {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
}

func TestResolveClientConfigUsesTLSCAFile(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	configPath := env.WriteClientConfig(
		"arbiter:\n" +
			"  url: 'https://config.test'\n" +
			"  tls_ca_file: '/tmp/arbiter-local.crt'\n",
	)

	resolved, err := cli.ResolveClientConfig(nil, env.LookupEnv, env.HomeDir)

	if err != nil {
		t.Fatal(err)
	}
	if resolved.URL != "https://config.test" {
		t.Fatalf("unexpected URL: %q", resolved.URL)
	}
	if resolved.Source != configPath {
		t.Fatalf("unexpected source: %q", resolved.Source)
	}
	if resolved.TLSCAFile != "/tmp/arbiter-local.crt" {
		t.Fatalf("unexpected TLS CA file: %q", resolved.TLSCAFile)
	}
}

func TestResolveClientConfigOverrideSkipsMalformedClientConfig(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("unsupported:\n  key: value\n")

	resolved, err := cli.ResolveClientConfig(
		[]string{"arbiter.url=https://override.test"},
		env.LookupEnv,
		env.HomeDir,
	)

	if err != nil {
		t.Fatal(err)
	}
	if resolved.URL != "https://override.test" {
		t.Fatalf("unexpected URL: %q", resolved.URL)
	}
	if resolved.Source != "override" {
		t.Fatalf("unexpected source: %q", resolved.Source)
	}
	if resolved.TLSCAFile != "" {
		t.Fatalf("unexpected TLS CA file: %q", resolved.TLSCAFile)
	}
}

func TestConfigURLPrintsOverrideSource(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.SetEnv(cli.URLEnvVar, "http://env.test")

	result := testutil.RunCLI(
		t,
		env,
		"config",
		"url",
		"arbiter.url=http://override.test",
	)

	if result.Code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.Code, result.Stderr)
	}
	expected := "url: http://override.test\nsource: override\n"
	if result.Stdout != expected {
		t.Fatalf("unexpected stdout: %q", result.Stdout)
	}
}

func TestResolveURLPrefersOverride(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.SetEnv(cli.URLEnvVar, "http://env.test")

	resolved, err := cli.ResolveURL(
		[]string{"arbiter.url=http://example.test"},
		env.LookupEnv,
		env.HomeDir,
	)

	if err != nil {
		t.Fatal(err)
	}
	if resolved.URL != "http://example.test" {
		t.Fatalf("unexpected URL: %q", resolved.URL)
	}
	if resolved.Source != "override" {
		t.Fatalf("unexpected source: %q", resolved.Source)
	}
}

func TestResolveURLUsesEnvironment(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.SetEnv(cli.URLEnvVar, "http://env.test")

	resolved, err := cli.ResolveURL(
		nil,
		env.LookupEnv,
		env.HomeDir,
	)

	if err != nil {
		t.Fatal(err)
	}
	if resolved.URL != "http://env.test" {
		t.Fatalf("unexpected URL: %q", resolved.URL)
	}
	if resolved.Source != cli.URLEnvVar {
		t.Fatalf("unexpected source: %q", resolved.Source)
	}
}

func TestResolveURLUsesClientConfig(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	configPath := env.WriteClientConfig("arbiter:\n  url: 'http://config.test'\n")

	resolved, err := cli.ResolveURL(nil, env.LookupEnv, env.HomeDir)

	if err != nil {
		t.Fatal(err)
	}
	if resolved.URL != "http://config.test" {
		t.Fatalf("unexpected URL: %q", resolved.URL)
	}
	if resolved.Source != configPath {
		t.Fatalf("unexpected source: %q", resolved.Source)
	}
}

func TestResolveURLRejectsTopLevelURL(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("url: http://config.test\n")

	_, err := cli.ResolveURL(nil, env.LookupEnv, env.HomeDir)

	if err == nil {
		t.Fatal("expected top-level url to fail")
	}
}

func TestResolveURLRejectsUnknownClientConfigKeys(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("other:\n  url: http://config.test\n")

	_, err := cli.ResolveURL(nil, env.LookupEnv, env.HomeDir)

	if err == nil {
		t.Fatal("expected unknown top-level key to fail")
	}
}

func TestResolveURLRejectsUnknownArbiterConfigKeys(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("arbiter:\n  other: http://config.test\n")

	_, err := cli.ResolveURL(nil, env.LookupEnv, env.HomeDir)

	if err == nil {
		t.Fatal("expected unknown arbiter key to fail")
	}
}

func TestResolveURLRejectsUnknownArbiterConfigKeysAfterURL(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("arbiter:\n  url: http://config.test\n  other: value\n")

	_, err := cli.ResolveURL(nil, env.LookupEnv, env.HomeDir)

	if err == nil {
		t.Fatal("expected trailing unknown arbiter key to fail")
	}
}

func TestResolveURLRejectsNonStringURL(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("arbiter:\n  url: 123\n")

	_, err := cli.ResolveURL(nil, env.LookupEnv, env.HomeDir)

	if err == nil {
		t.Fatal("expected non-string url to fail")
	}
}

func TestResolveURLRejectsScalarArbiterConfig(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)
	env.WriteClientConfig("arbiter: http://config.test\n")

	_, err := cli.ResolveURL(nil, env.LookupEnv, env.HomeDir)

	if err == nil {
		t.Fatal("expected scalar arbiter config to fail")
	}
}

func TestResolveURLDefaults(t *testing.T) {
	env := testutil.NewCLIEnvironment(t)

	resolved, err := cli.ResolveURL(nil, env.LookupEnv, env.HomeDir)

	if err != nil {
		t.Fatal(err)
	}
	if resolved.URL != cli.DefaultURL {
		t.Fatalf("unexpected URL: %q", resolved.URL)
	}
	if resolved.Source != "default" {
		t.Fatalf("unexpected source: %q", resolved.Source)
	}
}
