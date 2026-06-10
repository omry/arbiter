package cli

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"testing"

	"github.com/omry/arbiter/client/go-cli/internal/mcp"
)

func TestInfoPluginsCallsInfoTool(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{
			"kind":    "plugins",
			"plugins": []any{map[string]any{"id": "smtp"}},
		},
		Raw: map[string]any{"structuredContent": map[string]any{"kind": "plugins"}},
	}

	result := runTestCLI("info", "plugins", "arbiter.mcp_url=http://server.test/mcp")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if fake.url != "http://server.test/mcp" {
		t.Fatalf("unexpected MCP URL: %q", fake.url)
	}
	if fake.calls[0].name != "info" {
		t.Fatalf("unexpected tool: %q", fake.calls[0].name)
	}
	if fake.calls[0].arguments["kind"] != "plugins" {
		t.Fatalf("unexpected arguments: %#v", fake.calls[0].arguments)
	}
	expected := `{"kind":"plugins","plugins":[{"id":"smtp"}],"server_url":"http://server.test/mcp"}` + "\n"
	if result.stdout != expected {
		t.Fatalf("unexpected stdout:\n%s", result.stdout)
	}
}

func TestInfoYAMLFlagCanFollowSubcommand(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{
			"kind": "plugin",
			"id":   "smtp",
		},
	}

	result := runTestCLI("info", "plugin", "smtp", "--yaml")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if !strings.Contains(result.stdout, "kind: plugin\n") {
		t.Fatalf("expected YAML-ish output, got:\n%s", result.stdout)
	}
	if fake.calls[0].arguments["kind"] != "plugin" || fake.calls[0].arguments["plugin"] != "smtp" {
		t.Fatalf("unexpected arguments: %#v", fake.calls[0].arguments)
	}
}

func TestInfoShortPrintsOnlyAccountSummary(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{
			"kind": "overview",
			"plugins": []any{
				map[string]any{
					"id":          "imap",
					"description": "Read mail",
					"accounts": []any{
						map[string]any{"name": "bot", "description": "Bot mailbox"},
						map[string]any{"name": "personal"},
					},
				},
				map[string]any{
					"id":          "smtp",
					"description": "Send mail",
					"accounts": []any{
						map[string]any{"name": "bot", "description": "Bot sender"},
					},
				},
			},
			"operations": []any{map[string]any{"id": "imap:get_message"}},
		},
	}

	result := runTestCLI("info", "--short", "arbiter.mcp_url=http://server.test/mcp")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if fake.calls[0].arguments["kind"] != "overview" {
		t.Fatalf("unexpected arguments: %#v", fake.calls[0].arguments)
	}
	expected := `{"accounts":[{"description":"Bot mailbox","id":"imap:bot"},{"id":"imap:personal"},{"description":"Bot sender","id":"smtp:bot"}],"kind":"overview_short","server_url":"http://server.test/mcp"}` + "\n"
	if result.stdout != expected {
		t.Fatalf("unexpected stdout:\n%s", result.stdout)
	}
}

func TestInfoShortYAMLFlagCanFollowSubcommand(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{
			"kind": "overview",
			"plugins": []any{
				map[string]any{
					"id": "imap",
					"accounts": []any{
						map[string]any{"name": "bot", "description": "Bot mailbox"},
					},
				},
			},
		},
	}

	result := runTestCLI("info", "--yaml", "--short")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if !strings.Contains(result.stdout, "kind: overview_short\n") {
		t.Fatalf("expected short YAML output, got:\n%s", result.stdout)
	}
	if !strings.Contains(result.stdout, "id: imap:bot\n") {
		t.Fatalf("expected account id in YAML output, got:\n%s", result.stdout)
	}
}

func TestInfoShortPrintsEmptyAccountsList(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{
			"kind":    "overview",
			"plugins": []any{},
		},
	}

	result := runTestCLI("info", "--short")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if result.stdout != `{"accounts":[],"kind":"overview_short","server_url":"http://127.0.0.1:8000/mcp"}`+"\n" {
		t.Fatalf("unexpected stdout:\n%s", result.stdout)
	}
}

func TestInfoShortRejectsSubcommands(t *testing.T) {
	fake := installFakeMCPClient(t)

	result := runTestCLI("info", "plugin", "smtp", "--short")

	if result.code != 2 {
		t.Fatalf("expected exit code 2, got %d stderr=%q", result.code, result.stderr)
	}
	if len(fake.calls) != 0 {
		t.Fatalf("expected no MCP calls, got %#v", fake.calls)
	}
	if !strings.Contains(result.stderr, "info --short is only valid for overview") {
		t.Fatalf("unexpected stderr:\n%s", result.stderr)
	}
}

func TestInfoTestStaleServerErrorMentionsServer(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callErr = errors.New("unknown info kind: tests; supported kinds: account, accounts")

	result := runTestCLI("info", "tests", "arbiter.mcp_url=http://old.test/mcp")

	if result.code != 1 {
		t.Fatalf("expected exit code 1, got %d", result.code)
	}
	if !strings.Contains(result.stderr, "server at http://old.test/mcp does not") {
		t.Fatalf("unexpected stderr:\n%s", result.stderr)
	}
}

func TestOperationRunCallsRunOp(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{"ok": true},
	}

	result := runTestCLI("op", "run", "smtp:send_email", "--args", `{"account":"bot"}`)

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if fake.calls[0].name != "run_op" {
		t.Fatalf("unexpected tool: %q", fake.calls[0].name)
	}
	if fake.calls[0].arguments["id"] != "smtp:send_email" {
		t.Fatalf("unexpected arguments: %#v", fake.calls[0].arguments)
	}
	operationArgs, ok := fake.calls[0].arguments["arguments"].(map[string]any)
	if !ok || operationArgs["account"] != "bot" {
		t.Fatalf("unexpected operation args: %#v", fake.calls[0].arguments["arguments"])
	}
	if result.stdout != `{"ok":true}`+"\n" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestOperationDescForPluginCallsInfo(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{
			"kind":        "plugin",
			"id":          "imap",
			"description": "Read mail",
		},
	}

	result := runTestCLI("op", "desc", "imap")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if len(fake.calls) != 1 {
		t.Fatalf("expected one MCP call, got %#v", fake.calls)
	}
	if fake.calls[0].name != "info" {
		t.Fatalf("unexpected tool: %q", fake.calls[0].name)
	}
	if fake.calls[0].arguments["kind"] != "plugin" || fake.calls[0].arguments["plugin"] != "imap" {
		t.Fatalf("unexpected arguments: %#v", fake.calls[0].arguments)
	}
	if result.stdout != `{"description":"Read mail","id":"imap","kind":"plugin"}`+"\n" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestOperationDescForPluginPrintsPlain(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{
			"kind":        "plugin",
			"id":          "imap",
			"description": "Read mail",
			"operations": []any{
				map[string]any{"id": "imap:get_message"},
			},
		},
	}

	result := runTestCLI("op", "desc", "imap", "--plain")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if result.stdout != "imap\nRead mail\nimap:get_message\n" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestOperationDescForPluginPrintsYAML(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{
			"kind":        "plugin",
			"id":          "imap",
			"description": "Read mail",
		},
	}

	result := runTestCLI("op", "desc", "imap", "--yaml")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if !strings.Contains(result.stdout, "description: Read mail\n") ||
		!strings.Contains(result.stdout, "id: imap\n") ||
		!strings.Contains(result.stdout, "kind: plugin\n") {
		t.Fatalf("expected YAML output, got:\n%s", result.stdout)
	}
}

func TestOperationDescForOperationCallsDescribeOp(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{"id": "imap:get_message"},
	}

	result := runTestCLI("op", "desc", "imap:get_message")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if len(fake.calls) != 1 {
		t.Fatalf("expected one MCP call, got %#v", fake.calls)
	}
	if fake.calls[0].name != "describe_op" {
		t.Fatalf("unexpected tool: %q", fake.calls[0].name)
	}
	if fake.calls[0].arguments["id"] != "imap:get_message" {
		t.Fatalf("unexpected arguments: %#v", fake.calls[0].arguments)
	}
	if result.stdout != `{"id":"imap:get_message"}`+"\n" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestOperationListForPluginPrintsJSONByDefault(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{
			"kind":   "ops",
			"plugin": "smtp",
			"operations": []any{
				map[string]any{"id": "smtp:send_email"},
				map[string]any{"id": "smtp:verify_connection"},
			},
		},
	}

	result := runTestCLI("op", "list", "smtp")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if len(fake.calls) != 1 {
		t.Fatalf("expected one MCP call, got %#v", fake.calls)
	}
	if fake.calls[0].name != "info" {
		t.Fatalf("unexpected tool: %q", fake.calls[0].name)
	}
	if fake.calls[0].arguments["kind"] != "ops" || fake.calls[0].arguments["plugin"] != "smtp" {
		t.Fatalf("unexpected arguments: %#v", fake.calls[0].arguments)
	}
	expected := `{"kind":"ops","operations":{"smtp:send_email":{},"smtp:verify_connection":{}},"plugin":"smtp"}` + "\n"
	if result.stdout != expected {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestOperationListForPluginPrintsPlainOperationIDs(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{
			"kind":   "ops",
			"plugin": "smtp",
			"operations": []any{
				map[string]any{"id": "smtp:send_email"},
				map[string]any{"id": "smtp:verify_connection"},
			},
		},
	}

	result := runTestCLI("op", "list", "smtp", "--plain")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if result.stdout != "smtp:send_email\nsmtp:verify_connection\n" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestOperationListForPluginPrintsYAML(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{
			"kind":   "ops",
			"plugin": "smtp",
			"operations": []any{
				map[string]any{
					"description": "Send one email.",
					"id":          "smtp:send_email",
					"name":        "send_email",
				},
			},
		},
	}

	result := runTestCLI("op", "list", "smtp", "--yaml")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	expected := "" +
		"kind: ops\n" +
		"operations:\n" +
		"  smtp:send_email:\n" +
		"    description: Send one email.\n" +
		"    name: send_email\n" +
		"plugin: smtp\n"
	if result.stdout != expected {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestOperationListPrintsPluginsJSONByDefault(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{
			"kind": "plugins",
			"plugins": []any{
				map[string]any{"id": "smtp"},
				map[string]any{"id": "imap"},
			},
		},
	}

	result := runTestCLI("op", "list")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if len(fake.calls) != 1 {
		t.Fatalf("expected one MCP call, got %#v", fake.calls)
	}
	if fake.calls[0].arguments["kind"] != "plugins" {
		t.Fatalf("unexpected first call arguments: %#v", fake.calls[0].arguments)
	}
	if result.stdout != `{"plugins":["imap","smtp"]}`+"\n" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestOperationListPrintsPlainPlugins(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{
			"kind": "plugins",
			"plugins": []any{
				map[string]any{"id": "smtp"},
				map[string]any{"id": "imap"},
			},
		},
	}

	result := runTestCLI("op", "list", "--plain")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if result.stdout != "imap\nsmtp\n" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestOperationListPrintsPluginsYAML(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		StructuredContent: map[string]any{
			"kind": "plugins",
			"plugins": []any{
				map[string]any{"id": "smtp"},
				map[string]any{"id": "imap"},
			},
		},
	}

	result := runTestCLI("op", "list", "--yaml")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if result.stdout != "plugins:\n  - imap\n  - smtp\n" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestMCPToolsPrintsToolNames(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.tools = []mcp.Tool{{Name: "info"}, {Name: "run_op"}}

	result := runTestCLI("mcp", "tools")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if result.stdout != "info\nrun_op\n" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestMCPCallPrintsRawToolResult(t *testing.T) {
	fake := installFakeMCPClient(t)
	fake.callResult = mcp.ToolCallResult{
		Raw: map[string]any{
			"structuredContent": map[string]any{"version": "1.2.3"},
		},
	}

	result := runTestCLI("mcp", "call", "version_info")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if fake.calls[0].name != "version_info" {
		t.Fatalf("unexpected tool: %q", fake.calls[0].name)
	}
	if result.stdout != `{"structuredContent":{"version":"1.2.3"}}`+"\n" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

type cliResult struct {
	code   int
	stdout string
	stderr string
}

func runTestCLI(args ...string) cliResult {
	var stdout bytes.Buffer
	var stderr bytes.Buffer
	env := map[string]string{}
	code := Main(
		args,
		&stdout,
		&stderr,
		func(name string) (string, bool) {
			value, ok := env[name]
			return value, ok
		},
		func() (string, error) {
			return "/tmp/arbiter-go-cli-test-home", nil
		},
	)
	return cliResult{code: code, stdout: stdout.String(), stderr: stderr.String()}
}

type fakeClient struct {
	url        string
	tools      []mcp.Tool
	callResult mcp.ToolCallResult
	callErr    error
	calls      []fakeCall
}

type fakeCall struct {
	name      string
	arguments map[string]any
}

func installFakeMCPClient(t *testing.T) *fakeClient {
	t.Helper()
	fake := &fakeClient{}
	previous := newMCPClient
	newMCPClient = func(url string) mcpClient {
		fake.url = url
		return fake
	}
	t.Cleanup(func() {
		newMCPClient = previous
	})
	return fake
}

func (f *fakeClient) Initialize(context.Context, string, string) error {
	return nil
}

func (f *fakeClient) ListTools(context.Context) ([]mcp.Tool, error) {
	return f.tools, nil
}

func (f *fakeClient) CallTool(_ context.Context, name string, arguments map[string]any) (mcp.ToolCallResult, error) {
	f.calls = append(f.calls, fakeCall{name: name, arguments: arguments})
	if f.callErr != nil {
		return mcp.ToolCallResult{}, f.callErr
	}
	return f.callResult, nil
}
