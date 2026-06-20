package cli

import (
	"bytes"
	"context"
	"strings"
	"testing"
)

func TestInfoWithoutSubcommandPrintsHelp(t *testing.T) {
	result := runTestCLI("info")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if result.stderr != "" {
		t.Fatalf("unexpected stderr: %q", result.stderr)
	}
	if !strings.Contains(result.stdout, "usage: arbiter info") ||
		!strings.Contains(result.stdout, "server") {
		t.Fatalf("expected info help, got:\n%s", result.stdout)
	}
}

func TestInfoServerCallsInfo(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.info = map[string]any{"name": "arbiter", "deployment_scope": "installed"}

	result := runTestCLI("info", "server", "arbiter.url=http://server.test")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if fake.url != "http://server.test" {
		t.Fatalf("unexpected URL: %q", fake.url)
	}
	if len(fake.calls) != 1 || fake.calls[0].name != "info" {
		t.Fatalf("unexpected native calls: %#v", fake.calls)
	}
	expected := `{"deployment_scope":"installed","name":"arbiter","server_url":"http://server.test"}` + "\n"
	if result.stdout != expected {
		t.Fatalf("unexpected stdout:\n%s", result.stdout)
	}
}

func TestInfoServerYAMLFlagCanFollowSubcommand(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.info = map[string]any{"name": "arbiter"}

	result := runTestCLI("info", "server", "--yaml")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if !strings.Contains(result.stdout, "name: arbiter\n") ||
		!strings.Contains(result.stdout, "server_url: https://127.0.0.1:8075\n") {
		t.Fatalf("expected YAML-ish output, got:\n%s", result.stdout)
	}
}

func TestInfoServerPrintsStagedDeploymentWarning(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.info = map[string]any{"name": "arbiter", "deployment_scope": "staged"}

	result := runTestCLI("info", "server", "arbiter.url=http://staged.test")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if result.stderr != "Heads up: connected to staged Arbiter at http://staged.test.\n" {
		t.Fatalf("unexpected stderr:\n%s", result.stderr)
	}
}

func TestInfoRejectsOldSubcommands(t *testing.T) {
	result := runTestCLI("info", "plugins", "arbiter.url=http://old.test")

	if result.code != 2 {
		t.Fatalf("expected exit code 2, got %d", result.code)
	}
	if !strings.Contains(result.stderr, "expected: arbiter info server") {
		t.Fatalf("unexpected stderr:\n%s", result.stderr)
	}
}

func TestPluginsListCallsNativePlugins(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.plugins = map[string]any{"plugins": []any{map[string]any{"id": "smtp"}}}

	result := runTestCLI("plugins", "arbiter.url=http://server.test")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if fake.calls[0].name != "plugins" {
		t.Fatalf("unexpected native call: %q", fake.calls[0].name)
	}
	expected := `{"plugins":[{"id":"smtp"}]}` + "\n"
	if result.stdout != expected {
		t.Fatalf("unexpected stdout:\n%s", result.stdout)
	}
}

func TestPluginsSubcommandsCallNativePluginRoutes(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.pluginDetails = map[string]map[string]any{
		"smtp": {"id": "smtp", "summary": "Send mail"},
	}
	fake.pluginAccounts = map[string]map[string]any{
		"smtp": {"plugin": "smtp", "accounts": []any{map[string]any{"account": "bot"}}},
	}
	fake.pluginAccountDetails = map[string]map[string]any{
		"smtp/bot": {"kind": "account", "plugin": "smtp", "account": "bot"},
	}
	fake.pluginPolicies = map[string]map[string]any{
		"smtp/bot_policy": {"kind": "policy", "plugin": "smtp", "policy": "bot_policy"},
	}

	cases := []struct {
		name     string
		args     []string
		callName string
		callID   string
		expected string
	}{
		{
			name:     "plugin",
			args:     []string{"plugins", "smtp"},
			callName: "plugin_details",
			callID:   "smtp",
			expected: `{"id":"smtp","summary":"Send mail"}` + "\n",
		},
		{
			name:     "accounts",
			args:     []string{"plugins", "smtp", "accounts"},
			callName: "plugin_accounts",
			callID:   "smtp",
			expected: `{"accounts":[{"account":"bot"}],"plugin":"smtp"}` + "\n",
		},
		{
			name:     "account",
			args:     []string{"plugins", "smtp", "account", "bot"},
			callName: "plugin_account",
			callID:   "smtp/bot",
			expected: `{"account":"bot","kind":"account","plugin":"smtp"}` + "\n",
		},
		{
			name:     "policy",
			args:     []string{"plugins", "smtp", "policy", "bot_policy"},
			callName: "plugin_policy",
			callID:   "smtp/bot_policy",
			expected: `{"kind":"policy","plugin":"smtp","policy":"bot_policy"}` + "\n",
		},
	}

	for _, tt := range cases {
		t.Run(tt.name, func(t *testing.T) {
			fake.calls = nil
			result := runTestCLI(tt.args...)

			if result.code != 0 {
				t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
			}
			if fake.calls[0].name != tt.callName || fake.calls[0].id != tt.callID {
				t.Fatalf("unexpected native call: %#v", fake.calls[0])
			}
			if result.stdout != tt.expected {
				t.Fatalf("unexpected stdout:\n%s", result.stdout)
			}
		})
	}
}

func TestPluginsYAMLFlagCanFollowSubcommand(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.pluginDetails = map[string]map[string]any{
		"smtp": {"id": "smtp", "summary": "Send mail"},
	}

	result := runTestCLI("plugins", "smtp", "--yaml")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if !strings.Contains(result.stdout, "id: smtp\n") ||
		!strings.Contains(result.stdout, "summary: Send mail\n") {
		t.Fatalf("expected YAML-ish output, got:\n%s", result.stdout)
	}
}

func TestOperationRunCallsRunOp(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.runResults = map[string]map[string]any{
		"smtp:send_email": {
			"artifacts": []any{},
			"result":    map[string]any{"ok": true},
			"warnings":  []any{},
		},
	}

	result := runTestCLI("op", "run", "smtp:send_email", "--args", `{"account":"bot"}`)

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if fake.calls[0].name != "run_operation" {
		t.Fatalf("unexpected native call: %q", fake.calls[0].name)
	}
	if fake.calls[0].id != "smtp:send_email" {
		t.Fatalf("unexpected operation id: %#v", fake.calls[0].id)
	}
	operationArgs := fake.calls[0].arguments
	if operationArgs["account"] != "bot" {
		t.Fatalf("unexpected operation args: %#v", operationArgs)
	}
	if result.stdout != `{"artifacts":[],"result":{"ok":true},"warnings":[]}`+"\n" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestOperationDescForPluginCallsInfo(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.pluginDetails = map[string]map[string]any{
		"imap": {"id": "imap", "summary": "Read mail"},
	}
	fake.pluginOperations = map[string]map[string]any{
		"imap": {"operations": []any{}},
	}

	result := runTestCLI("op", "desc", "imap")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if len(fake.calls) != 2 {
		t.Fatalf("expected plugin and operation native calls, got %#v", fake.calls)
	}
	if fake.calls[0].name != "plugin_details" || fake.calls[1].name != "plugin_operations" {
		t.Fatalf("unexpected native calls: %#v", fake.calls)
	}
	if result.stdout != `{"id":"imap","kind":"plugin","operations":[],"summary":"Read mail"}`+"\n" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestOperationDescForPluginPrintsYAML(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.pluginDetails = map[string]map[string]any{
		"imap": {"id": "imap", "summary": "Read mail"},
	}
	fake.pluginOperations = map[string]map[string]any{
		"imap": {"operations": []any{}},
	}

	result := runTestCLI("op", "desc", "imap", "--yaml")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if !strings.Contains(result.stdout, "summary: Read mail\n") ||
		!strings.Contains(result.stdout, "id: imap\n") ||
		!strings.Contains(result.stdout, "kind: plugin\n") {
		t.Fatalf("expected YAML output, got:\n%s", result.stdout)
	}
}

func TestOperationDescForOperationCallsDescribeOp(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.operationDetails = map[string]map[string]any{
		"imap:get_message": {"id": "imap:get_message"},
	}

	result := runTestCLI("op", "desc", "imap:get_message")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if len(fake.calls) != 1 {
		t.Fatalf("expected one native call, got %#v", fake.calls)
	}
	if fake.calls[0].name != "operation_details" {
		t.Fatalf("unexpected native call: %q", fake.calls[0].name)
	}
	if fake.calls[0].id != "imap:get_message" {
		t.Fatalf("unexpected operation id: %#v", fake.calls[0].id)
	}
	if result.stdout != `{"id":"imap:get_message"}`+"\n" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestOperationListForPluginPrintsJSONByDefault(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.pluginOperations = map[string]map[string]any{
		"smtp": {
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
		t.Fatalf("expected one native call, got %#v", fake.calls)
	}
	if fake.calls[0].name != "plugin_operations" || fake.calls[0].id != "smtp" {
		t.Fatalf("unexpected native call: %#v", fake.calls[0])
	}
	expected := `{"kind":"ops","operations":{"smtp:send_email":{},"smtp:verify_connection":{}},"plugin":"smtp"}` + "\n"
	if result.stdout != expected {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestOperationListForPluginPrintsYAML(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.pluginOperations = map[string]map[string]any{
		"smtp": {
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
	fake := installFakeArbiterClient(t)
	fake.plugins = map[string]any{
		"plugins": []any{
			map[string]any{"id": "smtp"},
			map[string]any{"id": "imap"},
		},
	}

	result := runTestCLI("op", "list")

	if result.code != 0 {
		t.Fatalf("expected exit code 0, got %d stderr=%q", result.code, result.stderr)
	}
	if len(fake.calls) != 1 {
		t.Fatalf("expected one native call, got %#v", fake.calls)
	}
	if fake.calls[0].name != "plugins" {
		t.Fatalf("unexpected native call: %#v", fake.calls[0])
	}
	if result.stdout != `{"plugins":["imap","smtp"]}`+"\n" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
}

func TestOperationListPrintsPluginsYAML(t *testing.T) {
	fake := installFakeArbiterClient(t)
	fake.plugins = map[string]any{
		"plugins": []any{
			map[string]any{"id": "smtp"},
			map[string]any{"id": "imap"},
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

func TestOperationOutputRejectsPlain(t *testing.T) {
	result := runTestCLI("op", "list", "--plain")

	if result.code != 2 {
		t.Fatalf("expected exit code 2, got %d", result.code)
	}
	if result.stdout != "" {
		t.Fatalf("unexpected stdout: %q", result.stdout)
	}
	if !strings.Contains(result.stderr, "unknown output option: --plain") {
		t.Fatalf("expected plain rejection, got %q", result.stderr)
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

type fakeCall struct {
	name      string
	id        string
	arguments map[string]any
}

type fakeArbiterClient struct {
	url                  string
	info                 map[string]any
	plugins              map[string]any
	pluginDetails        map[string]map[string]any
	pluginAccounts       map[string]map[string]any
	pluginAccountDetails map[string]map[string]any
	pluginPolicies       map[string]map[string]any
	pluginOperations     map[string]map[string]any
	operationDetails     map[string]map[string]any
	runResults           map[string]map[string]any
	err                  error
	calls                []fakeCall
}

func installFakeArbiterClient(t *testing.T) *fakeArbiterClient {
	t.Helper()
	fake := &fakeArbiterClient{
		info:                 map[string]any{"name": "arbiter"},
		plugins:              map[string]any{"plugins": []any{}},
		pluginDetails:        map[string]map[string]any{},
		pluginAccounts:       map[string]map[string]any{},
		pluginAccountDetails: map[string]map[string]any{},
		pluginPolicies:       map[string]map[string]any{},
		pluginOperations:     map[string]map[string]any{},
		operationDetails:     map[string]map[string]any{},
		runResults:           map[string]map[string]any{},
	}
	previous := newArbiterClient
	newArbiterClient = func(url string) arbiterClient {
		fake.url = url
		return fake
	}
	t.Cleanup(func() {
		newArbiterClient = previous
	})
	return fake
}

func (f *fakeArbiterClient) Info(context.Context) (map[string]any, error) {
	f.calls = append(f.calls, fakeCall{name: "info"})
	if f.err != nil {
		return nil, f.err
	}
	return f.info, nil
}

func (f *fakeArbiterClient) Plugins(context.Context) (map[string]any, error) {
	f.calls = append(f.calls, fakeCall{name: "plugins"})
	if f.err != nil {
		return nil, f.err
	}
	return f.plugins, nil
}

func (f *fakeArbiterClient) PluginDetails(_ context.Context, plugin string) (map[string]any, error) {
	f.calls = append(f.calls, fakeCall{name: "plugin_details", id: plugin})
	if f.err != nil {
		return nil, f.err
	}
	if payload, ok := f.pluginDetails[plugin]; ok {
		return payload, nil
	}
	return map[string]any{"id": plugin}, nil
}

func (f *fakeArbiterClient) PluginAccounts(_ context.Context, plugin string) (map[string]any, error) {
	f.calls = append(f.calls, fakeCall{name: "plugin_accounts", id: plugin})
	if f.err != nil {
		return nil, f.err
	}
	if payload, ok := f.pluginAccounts[plugin]; ok {
		return payload, nil
	}
	return map[string]any{"plugin": plugin, "accounts": []any{}}, nil
}

func (f *fakeArbiterClient) PluginAccount(_ context.Context, plugin string, account string) (map[string]any, error) {
	id := plugin + "/" + account
	f.calls = append(f.calls, fakeCall{name: "plugin_account", id: id})
	if f.err != nil {
		return nil, f.err
	}
	if payload, ok := f.pluginAccountDetails[id]; ok {
		return payload, nil
	}
	return map[string]any{"kind": "account", "plugin": plugin, "account": account}, nil
}

func (f *fakeArbiterClient) PluginPolicy(_ context.Context, plugin string, policy string) (map[string]any, error) {
	id := plugin + "/" + policy
	f.calls = append(f.calls, fakeCall{name: "plugin_policy", id: id})
	if f.err != nil {
		return nil, f.err
	}
	if payload, ok := f.pluginPolicies[id]; ok {
		return payload, nil
	}
	return map[string]any{"kind": "policy", "plugin": plugin, "policy": policy}, nil
}

func (f *fakeArbiterClient) PluginOperations(_ context.Context, plugin string) (map[string]any, error) {
	f.calls = append(f.calls, fakeCall{name: "plugin_operations", id: plugin})
	if f.err != nil {
		return nil, f.err
	}
	if payload, ok := f.pluginOperations[plugin]; ok {
		return payload, nil
	}
	return map[string]any{"operations": []any{}}, nil
}

func (f *fakeArbiterClient) OperationDetails(_ context.Context, operationID string) (map[string]any, error) {
	f.calls = append(f.calls, fakeCall{name: "operation_details", id: operationID})
	if f.err != nil {
		return nil, f.err
	}
	if payload, ok := f.operationDetails[operationID]; ok {
		return payload, nil
	}
	return map[string]any{"id": operationID}, nil
}

func (f *fakeArbiterClient) RunOperation(_ context.Context, operationID string, args map[string]any) (map[string]any, error) {
	f.calls = append(f.calls, fakeCall{name: "run_operation", id: operationID, arguments: args})
	if f.err != nil {
		return nil, f.err
	}
	if payload, ok := f.runResults[operationID]; ok {
		return payload, nil
	}
	return map[string]any{"result": map[string]any{}}, nil
}
