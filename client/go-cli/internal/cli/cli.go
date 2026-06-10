package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/omry/arbiter/client/go-cli/internal/mcp"
)

const (
	DefaultMCPURL                             = "http://127.0.0.1:8000/mcp"
	MCPURLEnvVar                              = "ARBITER_MCP_URL"
	DefaultConfigDir                          = ".arbiter"
	DefaultClientConfigName                   = "arbiter-client.yaml"
	DefaultArtifactMaxBytes                   = 16 * 1024
	DefaultArtifactCommandMaxChildStdoutBytes = 256 * 1024
)

type EnvLookup func(string) (string, bool)
type HomeDirFunc func() (string, error)

type ResolvedMCPURL struct {
	URL    string
	Source string
}

type mcpClient interface {
	Initialize(context.Context, string, string) error
	ListTools(context.Context) ([]mcp.Tool, error)
	CallTool(context.Context, string, map[string]any) (mcp.ToolCallResult, error)
}

var newMCPClient = func(url string) mcpClient {
	return mcp.NewClient(url)
}

type globalOptions struct {
	ConfigDir  string
	ConfigName string
	Overrides  []string
}

func Main(
	args []string,
	stdout io.Writer,
	stderr io.Writer,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) int {
	args = normalizeInfoOutputFlags(args)
	if len(args) == 0 {
		printShortUsage(stdout)
		return 2
	}

	options, remaining, err := parseGlobalOptions(args)
	if err != nil {
		fmt.Fprintf(stderr, "Arbiter usage error: %v\n", err)
		return 2
	}
	if len(remaining) == 0 {
		printShortUsage(stdout)
		return 2
	}

	switch remaining[0] {
	case "-h", "--help":
		printHelp(stdout)
		return 0
	case "--version":
		fmt.Fprintf(stdout, "arbiter-go %s\n", Version)
		return 0
	case "bootstrap":
		return runBootstrap(remaining[1:], options, stdout, stderr, lookupEnv, homeDir)
	case "config":
		return runConfig(remaining[1:], options, stdout, stderr, lookupEnv, homeDir)
	case "info":
		return runInfo(remaining[1:], options, stdout, stderr, lookupEnv, homeDir)
	case "op":
		return runOperation(remaining[1:], options, stdout, stderr, lookupEnv, homeDir)
	case "artifact":
		return runArtifact(remaining[1:], stdout, stderr)
	case "mcp":
		return runMCP(remaining[1:], options, stdout, stderr, lookupEnv, homeDir)
	default:
		fmt.Fprintf(stderr, "Arbiter usage error: unknown command: %s\n", remaining[0])
		printShortUsage(stderr)
		return 2
	}
}

func runArtifact(
	args []string,
	stdout io.Writer,
	stderr io.Writer,
) int {
	if len(args) == 0 {
		fmt.Fprintln(stderr, "Arbiter usage error: expected: arbiter artifact {get,save,with-temp,with-stdin} ...")
		fmt.Fprintln(stderr, "Run 'arbiter artifact --help' for artifact help.")
		return 2
	}
	switch args[0] {
	case "-h", "--help":
		printArtifactHelp(stdout)
		return 0
	case "get":
		if len(args) == 2 && (args[1] == "-h" || args[1] == "--help") {
			printArtifactGetHelp(stdout)
			return 0
		}
		options, err := parseArtifactGetArgs(args[1:])
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter usage error: %v\n", err)
			return 2
		}
		if err := fetchArtifact(context.Background(), options, stdout); err != nil {
			fmt.Fprintf(stderr, "Arbiter artifact error: %s\n", err)
			return 1
		}
		return 0
	case "save":
		if len(args) == 2 && (args[1] == "-h" || args[1] == "--help") {
			printArtifactSaveHelp(stdout)
			return 0
		}
		options, err := parseArtifactSaveArgs(args[1:])
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter usage error: %v\n", err)
			return 2
		}
		if err := saveArtifactToFile(context.Background(), options.URL, options.OutputPath); err != nil {
			fmt.Fprintf(stderr, "Arbiter artifact error: %s\n", err)
			return 1
		}
		return 0
	case "with-temp":
		if len(args) == 2 && (args[1] == "-h" || args[1] == "--help") {
			printArtifactWithTempHelp(stdout)
			return 0
		}
		options, err := parseArtifactCommandArgs(args[0], args[1:], true)
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter usage error: %v\n", err)
			return 2
		}
		if err := runArtifactWithTemp(context.Background(), options, stdout, stderr); err != nil {
			fmt.Fprintf(stderr, "Arbiter artifact error: %s\n", err)
			return 1
		}
		return 0
	case "with-stdin":
		if len(args) == 2 && (args[1] == "-h" || args[1] == "--help") {
			printArtifactWithStdinHelp(stdout, args[0])
			return 0
		}
		options, err := parseArtifactCommandArgs(args[0], args[1:], false)
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter usage error: %v\n", err)
			return 2
		}
		if err := runArtifactWithStdin(context.Background(), options, stdout, stderr); err != nil {
			fmt.Fprintf(stderr, "Arbiter artifact error: %s\n", err)
			return 1
		}
		return 0
	default:
		fmt.Fprintf(stderr, "Arbiter usage error: unknown artifact command: %s\n", args[0])
		return 2
	}
}

func runBootstrap(
	args []string,
	options globalOptions,
	stdout io.Writer,
	stderr io.Writer,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) int {
	if len(args) == 0 || args[0] != "client" {
		fmt.Fprintln(stderr, "Arbiter usage error: expected: arbiter bootstrap client [--force]")
		return 2
	}
	force := false
	if len(args) == 2 && args[1] == "--force" {
		force = true
	} else if len(args) != 1 {
		fmt.Fprintln(stderr, "Arbiter usage error: expected: arbiter bootstrap client [--force]")
		return 2
	}
	configPath, err := clientConfigPath(options, homeDir)
	if err != nil {
		fmt.Fprintf(stderr, "Arbiter client config error: %v\n", err)
		return 1
	}
	if _, err := os.Stat(configPath); err == nil && !force {
		fmt.Fprintf(stderr, "Arbiter client config error: refusing to overwrite existing file: %s\n", configPath)
		return 1
	} else if err != nil && !os.IsNotExist(err) {
		fmt.Fprintf(stderr, "Arbiter client config error: %v\n", err)
		return 1
	}
	if err := os.MkdirAll(filepath.Dir(configPath), 0o755); err != nil {
		fmt.Fprintf(stderr, "Arbiter client config error: %v\n", err)
		return 1
	}
	mcpURL := DefaultMCPURL
	if override, ok := mcpURLOverride(options.Overrides); ok {
		mcpURL = override
	} else if value, ok := lookupEnv(MCPURLEnvVar); ok && value != "" {
		mcpURL = value
	}
	content := fmt.Sprintf("arbiter:\n  mcp_url: %q\n", mcpURL)
	if err := os.WriteFile(configPath, []byte(content), 0o644); err != nil {
		fmt.Fprintf(stderr, "Arbiter client config error: %v\n", err)
		return 1
	}
	fmt.Fprintf(stdout, "wrote %s\n", configPath)
	return 0
}

func runConfig(
	args []string,
	options globalOptions,
	stdout io.Writer,
	stderr io.Writer,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) int {
	if len(args) != 1 || args[0] != "mcp-url" {
		fmt.Fprintln(stderr, "Arbiter usage error: expected: arbiter config mcp-url")
		return 2
	}

	resolved, err := ResolveMCPURL(options.Overrides, lookupEnv, homeDir, options)
	if err != nil {
		fmt.Fprintf(stderr, "Arbiter client config error: %v\n", err)
		return 1
	}
	fmt.Fprintf(stdout, "%s\n", resolved.URL)
	return 0
}

func runInfo(
	args []string,
	options globalOptions,
	stdout io.Writer,
	stderr io.Writer,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) int {
	yaml := false
	short := false
	for len(args) > 0 {
		switch args[0] {
		case "--yaml":
			yaml = true
		case "--short":
			short = true
		default:
			goto parsedFlags
		}
		args = args[1:]
	}
parsedFlags:
	if short && len(args) > 0 {
		fmt.Fprintln(stderr, "Arbiter usage error: info --short is only valid for overview")
		return 2
	}
	arguments, err := infoArguments(args)
	if err != nil {
		fmt.Fprintf(stderr, "Arbiter usage error: %v\n", err)
		return 2
	}
	payload, err := callToolPayload("info", arguments, options, lookupEnv, homeDir)
	if err != nil {
		fmt.Fprintf(stderr, "Arbiter tool error: %s\n", toolErrorForCLI(err, args, options, lookupEnv, homeDir))
		return 1
	}
	payload = withServerURL(payload, options, lookupEnv, homeDir)
	if short {
		payload = shortInfoPayload(payload)
	}
	if yaml {
		printYAML(stdout, payload)
		return 0
	}
	printJSON(stdout, payload)
	return 0
}

func runOperation(
	args []string,
	options globalOptions,
	stdout io.Writer,
	stderr io.Writer,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) int {
	if len(args) == 0 {
		fmt.Fprintln(stderr, "Arbiter usage error: expected: arbiter op {desc,run} ...")
		return 2
	}
	switch args[0] {
	case "desc", "describe":
		if len(args) != 2 {
			fmt.Fprintln(stderr, "Arbiter usage error: expected: arbiter op desc <operation-id>")
			return 2
		}
		payload, err := callToolPayload("describe_op", map[string]any{"id": args[1]}, options, lookupEnv, homeDir)
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter tool error: %s\n", err)
			return 1
		}
		printJSON(stdout, payload)
		return 0
	case "run":
		if len(args) < 2 {
			fmt.Fprintln(stderr, "Arbiter usage error: expected: arbiter op run <operation-id> [--args <json-object>]")
			return 2
		}
		operationID := args[1]
		operationArgs, err := parseArgsFlag(args[2:])
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter usage error: %v\n", err)
			return 2
		}
		payload, err := callToolPayload(
			"run_op",
			map[string]any{"id": operationID, "arguments": operationArgs},
			options,
			lookupEnv,
			homeDir,
		)
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter tool error: %s\n", err)
			return 1
		}
		printJSON(stdout, payload)
		return 0
	default:
		fmt.Fprintf(stderr, "Arbiter usage error: unknown op command: %s\n", args[0])
		return 2
	}
}

func runMCP(
	args []string,
	options globalOptions,
	stdout io.Writer,
	stderr io.Writer,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) int {
	if len(args) == 0 {
		args = []string{"tools"}
	}
	switch args[0] {
	case "tools":
		jsonOutput := len(args) > 1 && args[1] == "--json"
		if len(args) > 1 && !jsonOutput {
			fmt.Fprintln(stderr, "Arbiter usage error: expected: arbiter mcp tools [--json]")
			return 2
		}
		client, err := newInitializedMCPClient(options, lookupEnv, homeDir)
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter connection error: %s\n", err)
			return 1
		}
		tools, err := client.ListTools(context.Background())
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter tool error: %s\n", err)
			return 1
		}
		if jsonOutput {
			printJSON(stdout, map[string]any{"tools": tools})
			return 0
		}
		for _, tool := range tools {
			fmt.Fprintln(stdout, tool.Name)
		}
		return 0
	case "call":
		if len(args) < 2 {
			fmt.Fprintln(stderr, "Arbiter usage error: expected: arbiter mcp call <tool-name> [--args <json-object>]")
			return 2
		}
		toolName := args[1]
		toolArgs, err := parseArgsFlag(args[2:])
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter usage error: %v\n", err)
			return 2
		}
		result, err := callToolRaw(toolName, toolArgs, options, lookupEnv, homeDir)
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter tool error: %s\n", err)
			return 1
		}
		if result.Raw != nil {
			printJSON(stdout, result.Raw)
		} else {
			printJSON(stdout, result)
		}
		return 0
	default:
		fmt.Fprintf(stderr, "Arbiter usage error: unknown mcp command: %s\n", args[0])
		return 2
	}
}

func ResolveMCPURL(
	args []string,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
	options ...globalOptions,
) (ResolvedMCPURL, error) {
	configOptions := defaultGlobalOptions()
	if len(options) > 0 {
		configOptions = options[0]
	}
	if override, ok := mcpURLOverride(args); ok {
		return ResolvedMCPURL{
			URL:    override,
			Source: "override",
		}, nil
	}

	if value, ok := lookupEnv(MCPURLEnvVar); ok && value != "" {
		return ResolvedMCPURL{URL: value, Source: MCPURLEnvVar}, nil
	}

	configPath, err := clientConfigPath(configOptions, homeDir)
	if err != nil {
		return ResolvedMCPURL{}, err
	}
	if value, ok, err := readMCPURLFromConfig(configPath); err != nil {
		return ResolvedMCPURL{}, err
	} else if ok {
		return ResolvedMCPURL{URL: value, Source: configPath}, nil
	}

	return ResolvedMCPURL{URL: DefaultMCPURL, Source: "default"}, nil
}

func mcpURLOverride(args []string) (string, bool) {
	for _, arg := range args {
		if strings.HasPrefix(arg, "arbiter.mcp_url=") {
			return strings.TrimPrefix(arg, "arbiter.mcp_url="), true
		}
	}
	return "", false
}

func clientConfigPath(options globalOptions, homeDir HomeDirFunc) (string, error) {
	home, err := homeDir()
	if err != nil {
		return "", err
	}
	configDir := options.ConfigDir
	if configDir == "" {
		configDir = DefaultConfigDir
	}
	if strings.HasPrefix(configDir, "~/") {
		configDir = filepath.Join(home, strings.TrimPrefix(configDir, "~/"))
	}
	if !filepath.IsAbs(configDir) {
		configDir = filepath.Join(home, configDir)
	}
	configName := options.ConfigName
	if configName == "" {
		configName = DefaultClientConfigName
	}
	return filepath.Join(configDir, normalizeConfigName(configName)), nil
}

func defaultGlobalOptions() globalOptions {
	return globalOptions{
		ConfigDir:  DefaultConfigDir,
		ConfigName: DefaultClientConfigName,
	}
}

func parseGlobalOptions(args []string) (globalOptions, []string, error) {
	options := defaultGlobalOptions()
	var remaining []string
	skipNext := false
	for index := 0; index < len(args); index++ {
		arg := args[index]
		if skipNext {
			remaining = append(remaining, arg)
			skipNext = false
			continue
		}
		if arg == "--args" {
			remaining = append(remaining, arg)
			skipNext = true
			continue
		}
		switch {
		case arg == "--config-dir":
			if index+1 >= len(args) {
				return options, nil, fmt.Errorf("--config-dir requires a value")
			}
			options.ConfigDir = args[index+1]
			index++
		case strings.HasPrefix(arg, "--config-dir="):
			options.ConfigDir = strings.TrimPrefix(arg, "--config-dir=")
		case arg == "--config-name":
			if index+1 >= len(args) {
				return options, nil, fmt.Errorf("--config-name requires a value")
			}
			options.ConfigName = args[index+1]
			index++
		case strings.HasPrefix(arg, "--config-name="):
			options.ConfigName = strings.TrimPrefix(arg, "--config-name=")
		case strings.HasPrefix(arg, "arbiter.mcp_url="):
			options.Overrides = append(options.Overrides, arg)
		default:
			remaining = append(remaining, arg)
		}
	}
	return options, remaining, nil
}

func normalizeConfigName(name string) string {
	if strings.HasSuffix(name, ".yaml") || strings.HasSuffix(name, ".yml") {
		return name
	}
	return name + ".yaml"
}

func infoArguments(args []string) (map[string]any, error) {
	if len(args) == 0 {
		return map[string]any{"kind": "overview"}, nil
	}
	switch args[0] {
	case "plugins":
		if len(args) != 1 {
			return nil, fmt.Errorf("expected: arbiter info plugins")
		}
		return map[string]any{"kind": "plugins"}, nil
	case "plugin":
		if len(args) != 2 {
			return nil, fmt.Errorf("expected: arbiter info plugin <plugin>")
		}
		return map[string]any{"kind": "plugin", "plugin": args[1]}, nil
	case "accounts":
		if len(args) != 2 {
			return nil, fmt.Errorf("expected: arbiter info accounts <plugin>")
		}
		return map[string]any{"kind": "accounts", "plugin": args[1]}, nil
	case "account":
		if len(args) != 3 {
			return nil, fmt.Errorf("expected: arbiter info account <plugin> <account>")
		}
		return map[string]any{"kind": "account", "plugin": args[1], "account": args[2]}, nil
	case "tests":
		if len(args) != 1 {
			return nil, fmt.Errorf("expected: arbiter info tests")
		}
		return map[string]any{"kind": "tests"}, nil
	case "test":
		if len(args) < 2 || len(args) > 3 {
			return nil, fmt.Errorf("expected: arbiter info test <plugin> [account]")
		}
		arguments := map[string]any{"kind": "test", "plugin": args[1]}
		if len(args) == 3 {
			arguments["account"] = args[2]
		}
		return arguments, nil
	case "ops":
		if len(args) != 2 {
			return nil, fmt.Errorf("expected: arbiter info ops <plugin>")
		}
		return map[string]any{"kind": "ops", "plugin": args[1]}, nil
	case "op":
		if len(args) != 3 {
			return nil, fmt.Errorf("expected: arbiter info op <plugin> <operation>")
		}
		return map[string]any{"kind": "op", "plugin": args[1], "operation": args[2]}, nil
	default:
		return nil, fmt.Errorf("unknown info command: %s", args[0])
	}
}

func shortInfoPayload(payload any) any {
	mapping, ok := payload.(map[string]any)
	if !ok {
		return payload
	}
	short := map[string]any{"kind": "overview_short"}
	if serverURL, ok := mapping["server_url"].(string); ok {
		short["server_url"] = serverURL
	}
	short["accounts"] = shortInfoAccounts(mapping["plugins"])
	return short
}

func shortInfoAccounts(plugins any) []any {
	pluginItems, ok := plugins.([]any)
	if !ok {
		return []any{}
	}
	accounts := []any{}
	for _, pluginItem := range pluginItems {
		plugin, ok := pluginItem.(map[string]any)
		if !ok {
			continue
		}
		pluginID, ok := plugin["id"].(string)
		if !ok || pluginID == "" {
			continue
		}
		accountItems, ok := plugin["accounts"].([]any)
		if !ok {
			continue
		}
		for _, accountItem := range accountItems {
			account, ok := accountItem.(map[string]any)
			if !ok {
				continue
			}
			name, ok := account["name"].(string)
			if !ok || name == "" {
				continue
			}
			entry := map[string]any{"id": pluginID + ":" + name}
			if description, ok := account["description"].(string); ok && description != "" {
				entry["description"] = description
			}
			accounts = append(accounts, entry)
		}
	}
	return accounts
}

func parseArgsFlag(args []string) (map[string]any, error) {
	if len(args) == 0 {
		return map[string]any{}, nil
	}
	if len(args) != 2 || args[0] != "--args" {
		return nil, fmt.Errorf("expected optional --args <json-object>")
	}
	var parsed map[string]any
	if err := json.Unmarshal([]byte(args[1]), &parsed); err != nil {
		return nil, fmt.Errorf("invalid JSON args: %w", err)
	}
	if parsed == nil {
		return nil, fmt.Errorf("JSON arguments must be an object")
	}
	return parsed, nil
}

type artifactGetOptions struct {
	URL      string
	Stdout   bool
	MaxBytes int64
}

type artifactSaveOptions struct {
	URL        string
	OutputPath string
}

type artifactCommandOptions struct {
	URL                 string
	Command             []string
	MaxChildStdoutBytes int64
}

func parseArtifactGetArgs(args []string) (artifactGetOptions, error) {
	options := artifactGetOptions{MaxBytes: int64(DefaultArtifactMaxBytes)}
	if len(args) < 2 {
		if len(args) == 1 && strings.TrimSpace(args[0]) != "" {
			return options, fmt.Errorf("artifact get requires --stdout")
		}
		return options, fmt.Errorf("expected: arbiter artifact get <url> --stdout [--max-bytes N]")
	}
	artifactURL := args[0]
	if strings.TrimSpace(artifactURL) == "" {
		return options, fmt.Errorf("artifact URL must be non-empty")
	}
	options.URL = artifactURL
	for index := 1; index < len(args); index++ {
		switch args[index] {
		case "--stdout":
			options.Stdout = true
		case "--max-bytes":
			if index+1 >= len(args) {
				return options, fmt.Errorf("--max-bytes requires a value")
			}
			parsed, err := strconv.ParseInt(args[index+1], 10, 64)
			if err != nil || parsed < 1 {
				return options, fmt.Errorf("--max-bytes must be a positive integer")
			}
			options.MaxBytes = parsed
			index++
		default:
			return options, fmt.Errorf("unknown artifact get argument: %s", args[index])
		}
	}
	if !options.Stdout {
		return options, fmt.Errorf("artifact get requires --stdout")
	}
	return options, nil
}

func parseArtifactSaveArgs(args []string) (artifactSaveOptions, error) {
	options := artifactSaveOptions{}
	if len(args) != 2 {
		return options, fmt.Errorf("expected: arbiter artifact save <url> <path>")
	}
	artifactURL := args[0]
	if strings.TrimSpace(artifactURL) == "" {
		return options, fmt.Errorf("artifact URL must be non-empty")
	}
	outputPath := args[1]
	if strings.TrimSpace(outputPath) == "" {
		return options, fmt.Errorf("output path must be non-empty")
	}
	options.URL = artifactURL
	options.OutputPath = outputPath
	return options, nil
}

func fetchArtifact(
	ctx context.Context,
	options artifactGetOptions,
	stdout io.Writer,
) error {
	if options.Stdout {
		return writeArtifactToStdout(ctx, options.URL, options.MaxBytes, stdout)
	}
	return fmt.Errorf("artifact get requires --stdout")
}

func parseArtifactCommandArgs(
	commandName string,
	args []string,
	requirePathPlaceholder bool,
) (artifactCommandOptions, error) {
	options := artifactCommandOptions{
		MaxChildStdoutBytes: int64(DefaultArtifactCommandMaxChildStdoutBytes),
	}
	expected := fmt.Sprintf(
		"expected: arbiter artifact %s <url> [--max-child-stdout-bytes N] -- <argv...>",
		commandName,
	)
	if len(args) < 3 {
		return options, fmt.Errorf("%s", expected)
	}
	artifactURL := args[0]
	if strings.TrimSpace(artifactURL) == "" {
		return options, fmt.Errorf("artifact URL must be non-empty")
	}
	options.URL = artifactURL
	separatorIndex := -1
	for index := 1; index < len(args); index++ {
		if args[index] == "--" {
			separatorIndex = index
			break
		}
		switch args[index] {
		case "--max-child-stdout-bytes":
			if index+1 >= len(args) {
				return options, fmt.Errorf("--max-child-stdout-bytes requires a value")
			}
			parsed, err := strconv.ParseInt(args[index+1], 10, 64)
			if err != nil || parsed < 1 {
				return options, fmt.Errorf("--max-child-stdout-bytes must be a positive integer")
			}
			options.MaxChildStdoutBytes = parsed
			index++
		default:
			return options, fmt.Errorf("unknown artifact %s argument before --: %s", commandName, args[index])
		}
	}
	if separatorIndex < 0 || separatorIndex == len(args)-1 {
		return options, fmt.Errorf("%s", expected)
	}
	options.Command = args[separatorIndex+1:]
	if requirePathPlaceholder && !commandContainsPathPlaceholder(options.Command) {
		return options, fmt.Errorf("artifact %s command must contain a {} path placeholder", commandName)
	}
	return options, nil
}

func commandContainsPathPlaceholder(command []string) bool {
	for _, arg := range command {
		if strings.Contains(arg, "{}") {
			return true
		}
	}
	return false
}

func replacePathPlaceholder(command []string, path string) []string {
	replaced := make([]string, len(command))
	for index, arg := range command {
		replaced[index] = strings.ReplaceAll(arg, "{}", path)
	}
	return replaced
}

func runArtifactWithTemp(
	ctx context.Context,
	options artifactCommandOptions,
	stdout io.Writer,
	stderr io.Writer,
) error {
	httpClient := &http.Client{Timeout: 30 * time.Second}
	getReq, err := http.NewRequestWithContext(ctx, http.MethodGet, options.URL, nil)
	if err != nil {
		return err
	}
	getResp, err := httpClient.Do(getReq)
	if err != nil {
		return err
	}
	defer getResp.Body.Close()
	if getResp.StatusCode < 200 || getResp.StatusCode >= 300 {
		return fmt.Errorf("artifact fetch failed: HTTP %d", getResp.StatusCode)
	}

	tempDir, err := os.MkdirTemp("", "arbiter-artifact-*")
	if err != nil {
		return err
	}
	defer os.RemoveAll(tempDir)

	artifactPath := filepath.Join(tempDir, artifactTempFilename(getResp.Header))
	output, err := os.OpenFile(artifactPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	if _, err := io.Copy(output, getResp.Body); err != nil {
		output.Close()
		return err
	}
	if err := output.Close(); err != nil {
		return err
	}
	command := replacePathPlaceholder(options.Command, artifactPath)
	return runArtifactCommand(ctx, command, nil, stdout, stderr, options.MaxChildStdoutBytes)
}

func artifactTempFilename(headers http.Header) string {
	name := ""
	if contentDisposition := headers.Get("Content-Disposition"); contentDisposition != "" {
		if _, params, err := mime.ParseMediaType(contentDisposition); err == nil {
			name = params["filename"]
		}
	}
	name = sanitizeArtifactFilename(name)
	if name == "" {
		name = "artifact"
	}
	if filepath.Ext(name) == "" {
		if extension := artifactExtensionForContentType(headers.Get("Content-Type")); extension != "" {
			name += extension
		}
	}
	return name
}

func sanitizeArtifactFilename(name string) string {
	name = strings.TrimSpace(name)
	if name == "" {
		return ""
	}
	name = strings.ReplaceAll(name, "\\", "/")
	name = filepath.Base(name)
	var builder strings.Builder
	for _, char := range name {
		switch {
		case char >= 'a' && char <= 'z',
			char >= 'A' && char <= 'Z',
			char >= '0' && char <= '9',
			char == '.', char == '_', char == '-':
			builder.WriteRune(char)
		default:
			builder.WriteRune('_')
		}
	}
	sanitized := strings.Trim(builder.String(), ".")
	if sanitized == "" {
		return ""
	}
	return sanitized
}

func artifactExtensionForContentType(contentType string) string {
	mediaType := strings.ToLower(strings.TrimSpace(strings.Split(contentType, ";")[0]))
	switch mediaType {
	case "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
		return ".docx"
	case "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
		return ".xlsx"
	case "application/vnd.openxmlformats-officedocument.presentationml.presentation":
		return ".pptx"
	case "application/msword":
		return ".doc"
	case "application/vnd.ms-excel":
		return ".xls"
	case "application/vnd.ms-powerpoint":
		return ".ppt"
	case "application/vnd.oasis.opendocument.text":
		return ".odt"
	case "application/vnd.oasis.opendocument.spreadsheet":
		return ".ods"
	case "application/vnd.oasis.opendocument.presentation":
		return ".odp"
	case "application/pdf":
		return ".pdf"
	case "application/rtf":
		return ".rtf"
	case "application/zip":
		return ".zip"
	case "application/json":
		return ".json"
	case "application/xml":
		return ".xml"
	case "application/yaml", "application/x-yaml":
		return ".yaml"
	case "text/plain":
		return ".txt"
	case "text/csv":
		return ".csv"
	case "text/html":
		return ".html"
	case "text/markdown":
		return ".md"
	case "image/jpeg":
		return ".jpg"
	case "image/png":
		return ".png"
	case "image/gif":
		return ".gif"
	case "image/webp":
		return ".webp"
	case "image/svg+xml":
		return ".svg"
	case "audio/mpeg":
		return ".mp3"
	case "audio/wav", "audio/x-wav":
		return ".wav"
	case "video/mp4":
		return ".mp4"
	}
	extensions, err := mime.ExtensionsByType(mediaType)
	if err != nil || len(extensions) == 0 {
		return ""
	}
	return extensions[0]
}

func runArtifactWithStdin(
	ctx context.Context,
	options artifactCommandOptions,
	stdout io.Writer,
	stderr io.Writer,
) error {
	httpClient := &http.Client{Timeout: 30 * time.Second}
	getReq, err := http.NewRequestWithContext(ctx, http.MethodGet, options.URL, nil)
	if err != nil {
		return err
	}
	getResp, err := httpClient.Do(getReq)
	if err != nil {
		return err
	}
	defer getResp.Body.Close()
	if getResp.StatusCode < 200 || getResp.StatusCode >= 300 {
		return fmt.Errorf("artifact fetch failed: HTTP %d", getResp.StatusCode)
	}
	return runArtifactCommand(
		ctx,
		options.Command,
		getResp.Body,
		stdout,
		stderr,
		options.MaxChildStdoutBytes,
	)
}

func runArtifactCommand(
	ctx context.Context,
	command []string,
	stdin io.Reader,
	stdout io.Writer,
	stderr io.Writer,
	maxStdoutBytes int64,
) error {
	if len(command) == 0 || strings.TrimSpace(command[0]) == "" {
		return fmt.Errorf("artifact command must be non-empty")
	}
	commandCtx, cancelCommand := context.WithCancel(ctx)
	defer cancelCommand()
	cmd := exec.CommandContext(commandCtx, command[0], command[1:]...)
	cmd.Stdin = stdin
	childStdout := newCappedOutputBuffer(maxStdoutBytes, cancelCommand)
	childStderr := newCappedOutputBuffer(maxStdoutBytes, cancelCommand)
	cmd.Stdout = childStdout
	cmd.Stderr = childStderr

	commandErr := cmd.Run()
	writeCapturedStderr(stderr, childStderr)
	if childStdout.Exceeded() {
		return fmt.Errorf("refusing to write child stdout larger than %d bytes", maxStdoutBytes)
	}
	stdoutBytes := childStdout.Bytes()
	if !isTextOutput(stdoutBytes) {
		return fmt.Errorf("refusing to write non-text child stdout")
	}
	if len(stdoutBytes) > 0 {
		if _, err := stdout.Write(stdoutBytes); err != nil {
			return err
		}
	}
	if commandErr != nil {
		return fmt.Errorf("command failed: %w", commandErr)
	}
	return nil
}

type cappedOutputBuffer struct {
	buffer   bytes.Buffer
	limit    int64
	exceeded bool
	onExceed func()
}

func newCappedOutputBuffer(limit int64, onExceed func()) *cappedOutputBuffer {
	return &cappedOutputBuffer{limit: limit, onExceed: onExceed}
}

func (buffer *cappedOutputBuffer) Write(data []byte) (int, error) {
	if buffer.limit < 1 {
		buffer.markExceeded()
		return len(data), nil
	}
	remaining := buffer.limit + 1 - int64(buffer.buffer.Len())
	if remaining > 0 {
		toWrite := data
		if int64(len(toWrite)) > remaining {
			toWrite = toWrite[:remaining]
			buffer.markExceeded()
		}
		_, _ = buffer.buffer.Write(toWrite)
		if int64(buffer.buffer.Len()) > buffer.limit {
			buffer.markExceeded()
		}
	} else if len(data) > 0 {
		buffer.markExceeded()
	}
	return len(data), nil
}

func (buffer *cappedOutputBuffer) markExceeded() {
	buffer.exceeded = true
	if buffer.onExceed != nil {
		buffer.onExceed()
	}
}

func (buffer *cappedOutputBuffer) Bytes() []byte {
	data := buffer.buffer.Bytes()
	if int64(len(data)) > buffer.limit {
		return data[:buffer.limit]
	}
	return data
}

func (buffer *cappedOutputBuffer) Exceeded() bool {
	return buffer.exceeded || int64(buffer.buffer.Len()) > buffer.limit
}

func writeCapturedStderr(stderr io.Writer, childStderr *cappedOutputBuffer) {
	data := childStderr.Bytes()
	if len(data) == 0 {
		return
	}
	if !isTextOutput(data) {
		fmt.Fprintln(stderr, "Arbiter artifact warning: child stderr omitted because it was not text")
		return
	}
	_, _ = stderr.Write(data)
	if childStderr.Exceeded() {
		fmt.Fprintf(stderr, "\nArbiter artifact warning: child stderr truncated at %d bytes\n", childStderr.limit)
	}
}

func isTextOutput(data []byte) bool {
	return !bytes.Contains(data, []byte{0}) && utf8.Valid(data)
}

func writeArtifactToStdout(
	ctx context.Context,
	artifactURL string,
	maxBytes int64,
	stdout io.Writer,
) error {
	httpClient := &http.Client{Timeout: 30 * time.Second}
	headReq, err := http.NewRequestWithContext(ctx, http.MethodHead, artifactURL, nil)
	if err != nil {
		return err
	}
	headResp, err := httpClient.Do(headReq)
	if err != nil {
		return err
	}
	headResp.Body.Close()
	if headResp.StatusCode < 200 || headResp.StatusCode >= 300 {
		return fmt.Errorf("artifact metadata request failed: HTTP %d", headResp.StatusCode)
	}
	contentType := headResp.Header.Get("Content-Type")
	if !isTextualArtifactContentType(contentType) {
		return fmt.Errorf("refusing to write non-text artifact to stdout: %s", contentType)
	}
	if headResp.ContentLength < 0 {
		return fmt.Errorf("refusing to write artifact with unknown size to stdout")
	}
	if headResp.ContentLength > maxBytes {
		return fmt.Errorf("refusing to write %d byte artifact to stdout; limit is %d bytes", headResp.ContentLength, maxBytes)
	}

	getReq, err := http.NewRequestWithContext(ctx, http.MethodGet, artifactURL, nil)
	if err != nil {
		return err
	}
	getResp, err := httpClient.Do(getReq)
	if err != nil {
		return err
	}
	defer getResp.Body.Close()
	if getResp.StatusCode < 200 || getResp.StatusCode >= 300 {
		return fmt.Errorf("artifact fetch failed: HTTP %d", getResp.StatusCode)
	}
	getContentType := getResp.Header.Get("Content-Type")
	if !isTextualArtifactContentType(getContentType) {
		return fmt.Errorf("refusing to write non-text artifact to stdout: %s", getContentType)
	}
	data, err := io.ReadAll(io.LimitReader(getResp.Body, maxBytes+1))
	if err != nil {
		return err
	}
	if int64(len(data)) > maxBytes {
		return fmt.Errorf("refusing to write artifact larger than %d bytes to stdout", maxBytes)
	}
	_, err = stdout.Write(data)
	return err
}

func saveArtifactToFile(
	ctx context.Context,
	artifactURL string,
	outputPath string,
) error {
	httpClient := &http.Client{Timeout: 30 * time.Second}
	getReq, err := http.NewRequestWithContext(ctx, http.MethodGet, artifactURL, nil)
	if err != nil {
		return err
	}
	getResp, err := httpClient.Do(getReq)
	if err != nil {
		return err
	}
	defer getResp.Body.Close()
	if getResp.StatusCode < 200 || getResp.StatusCode >= 300 {
		return fmt.Errorf("artifact fetch failed: HTTP %d", getResp.StatusCode)
	}

	output, err := os.OpenFile(outputPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	removeOutput := true
	defer func() {
		if removeOutput {
			_ = os.Remove(outputPath)
		}
	}()
	if _, err := io.Copy(output, getResp.Body); err != nil {
		_ = output.Close()
		return err
	}
	if err := output.Close(); err != nil {
		return err
	}
	removeOutput = false
	return nil
}

func isTextualArtifactContentType(contentType string) bool {
	mediaType := strings.ToLower(strings.TrimSpace(strings.Split(contentType, ";")[0]))
	if strings.HasPrefix(mediaType, "text/") {
		return true
	}
	switch mediaType {
	case "application/json",
		"application/ld+json",
		"application/xml",
		"application/yaml",
		"application/x-yaml",
		"application/toml",
		"application/javascript":
		return true
	default:
		return strings.HasSuffix(mediaType, "+json") || strings.HasSuffix(mediaType, "+xml")
	}
}

func callToolPayload(
	name string,
	arguments map[string]any,
	options globalOptions,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) (any, error) {
	result, err := callToolRaw(name, arguments, options, lookupEnv, homeDir)
	if err != nil {
		return nil, err
	}
	return mcp.Payload(result), nil
}

func callToolRaw(
	name string,
	arguments map[string]any,
	options globalOptions,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) (mcp.ToolCallResult, error) {
	client, err := newInitializedMCPClient(options, lookupEnv, homeDir)
	if err != nil {
		return mcp.ToolCallResult{}, err
	}
	return client.CallTool(context.Background(), name, arguments)
}

func newInitializedMCPClient(
	options globalOptions,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) (mcpClient, error) {
	resolved, err := ResolveMCPURL(options.Overrides, lookupEnv, homeDir, options)
	if err != nil {
		return nil, err
	}
	client := newMCPClient(resolved.URL)
	if err := client.Initialize(context.Background(), "arbiter-go", Version); err != nil {
		return nil, err
	}
	return client, nil
}

func withServerURL(payload any, options globalOptions, lookupEnv EnvLookup, homeDir HomeDirFunc) any {
	resolved, err := ResolveMCPURL(options.Overrides, lookupEnv, homeDir, options)
	if err != nil {
		return payload
	}
	if mapping, ok := payload.(map[string]any); ok {
		withURL := map[string]any{"server_url": resolved.URL}
		for key, value := range mapping {
			withURL[key] = value
		}
		return withURL
	}
	return payload
}

func toolErrorForCLI(err error, infoArgs []string, options globalOptions, lookupEnv EnvLookup, homeDir HomeDirFunc) string {
	message := err.Error()
	if len(infoArgs) > 0 && (infoArgs[0] == "test" || infoArgs[0] == "tests") && strings.HasPrefix(message, "unknown info kind:") {
		resolved, resolveErr := ResolveMCPURL(options.Overrides, lookupEnv, homeDir, options)
		url := "the configured server"
		if resolveErr == nil {
			url = resolved.URL
		}
		return message + "\n" +
			fmt.Sprintf("The local Arbiter client understands 'info %s', but the server at %s does not. This usually means the running server is older than the client or was not restarted after updating the wheelhouse.", infoArgs[0], url)
	}
	return message
}

func printJSON(w io.Writer, value any) {
	encoded, err := json.Marshal(value)
	if err != nil {
		fmt.Fprintf(w, "{\"error\":%q}\n", err.Error())
		return
	}
	fmt.Fprintf(w, "%s\n", encoded)
}

func printYAML(w io.Writer, value any) {
	writeYAML(w, value, 0)
}

func writeYAML(w io.Writer, value any, indent int) {
	prefix := strings.Repeat(" ", indent)
	switch typed := value.(type) {
	case map[string]any:
		keys := make([]string, 0, len(typed))
		for key := range typed {
			keys = append(keys, key)
		}
		sort.Strings(keys)
		for _, key := range keys {
			child := typed[key]
			if isScalar(child) {
				fmt.Fprintf(w, "%s%s: %v\n", prefix, key, child)
			} else {
				fmt.Fprintf(w, "%s%s:\n", prefix, key)
				writeYAML(w, child, indent+2)
			}
		}
	case []any:
		for _, item := range typed {
			if isScalar(item) {
				fmt.Fprintf(w, "%s- %v\n", prefix, item)
			} else {
				fmt.Fprintf(w, "%s-\n", prefix)
				writeYAML(w, item, indent+2)
			}
		}
	default:
		fmt.Fprintf(w, "%s%v\n", prefix, typed)
	}
}

func isScalar(value any) bool {
	switch value.(type) {
	case nil, string, bool, float64, int, int64:
		return true
	default:
		return false
	}
}

func normalizeInfoOutputFlags(args []string) []string {
	normalized := append([]string(nil), args...)
	infoIndex := -1
	for index, arg := range normalized {
		if arg == "info" {
			infoIndex = index
			break
		}
	}
	if infoIndex == -1 {
		return normalized
	}
	var withoutOutputFlags []string
	var outputFlags []string
	for index, arg := range normalized {
		if index > infoIndex && (arg == "--yaml" || arg == "--short") {
			outputFlags = append(outputFlags, arg)
			continue
		}
		withoutOutputFlags = append(withoutOutputFlags, arg)
	}
	if len(outputFlags) == 0 {
		return normalized
	}
	return append(
		append([]string{}, withoutOutputFlags[:infoIndex+1]...),
		append(outputFlags, withoutOutputFlags[infoIndex+1:]...)...,
	)
}

func readMCPURLFromConfig(path string) (string, bool, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return "", false, nil
		}
		return "", false, err
	}

	return parseMCPURLConfig(path, string(data))
}

func parseMCPURLConfig(path string, data string) (string, bool, error) {
	inArbiter := false
	foundArbiter := false
	foundMCPURL := false
	mcpURL := ""
	for _, line := range strings.Split(data, "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		indented := strings.HasPrefix(line, " ") || strings.HasPrefix(line, "\t")
		if !indented {
			key, value, ok := strings.Cut(trimmed, ":")
			if !ok {
				return "", false, fmt.Errorf("unsupported client config entry in %s: %s", path, trimmed)
			}
			key = strings.TrimSpace(key)
			value = strings.TrimSpace(value)
			if key != "arbiter" {
				return "", false, fmt.Errorf("unsupported client config key(s) in %s: %s", path, key)
			}
			if foundArbiter {
				return "", false, fmt.Errorf("duplicate client config key in %s: arbiter", path)
			}
			foundArbiter = true
			if value != "" {
				return "", false, fmt.Errorf("client config arbiter must be a mapping: %s", path)
			}
			inArbiter = true
			continue
		}

		if !inArbiter {
			return "", false, fmt.Errorf("unsupported indented client config entry in %s: %s", path, trimmed)
		}
		key, value, ok := strings.Cut(trimmed, ":")
		if !ok {
			return "", false, fmt.Errorf("unsupported client config arbiter entry in %s: %s", path, trimmed)
		}
		key = strings.TrimSpace(key)
		if key != "mcp_url" {
			return "", false, fmt.Errorf("unsupported client config arbiter key(s) in %s: %s", path, key)
		}
		if foundMCPURL {
			return "", false, fmt.Errorf("duplicate client config arbiter key in %s: mcp_url", path)
		}
		parsedMCPURL, err := parseConfigStringScalar(strings.TrimSpace(value), path)
		if err != nil {
			return "", false, err
		}
		foundMCPURL = true
		mcpURL = parsedMCPURL
	}
	return mcpURL, foundMCPURL, nil
}

func parseConfigStringScalar(value string, path string) (string, error) {
	if value == "" {
		return "", fmt.Errorf("client config arbiter.mcp_url must be a string: %s", path)
	}
	if strings.HasPrefix(value, `"`) || strings.HasPrefix(value, `'`) {
		quote := value[:1]
		if !strings.HasSuffix(value, quote) || len(value) == 1 {
			return "", fmt.Errorf("client config arbiter.mcp_url must be a string: %s", path)
		}
		return strings.TrimSuffix(strings.TrimPrefix(value, quote), quote), nil
	}

	normalized := strings.ToLower(value)
	if strings.HasPrefix(value, "[") ||
		strings.HasPrefix(value, "{") ||
		normalized == "true" ||
		normalized == "false" ||
		normalized == "null" ||
		normalized == "~" {
		return "", fmt.Errorf("client config arbiter.mcp_url must be a string: %s", path)
	}
	if _, err := strconv.ParseFloat(value, 64); err == nil {
		return "", fmt.Errorf("client config arbiter.mcp_url must be a string: %s", path)
	}
	return value, nil
}

func printShortUsage(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter {bootstrap,config,info,op,artifact,mcp} ...")
	fmt.Fprintln(w, "Run 'arbiter --help' for full help.")
}

func printHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter [--version] {bootstrap,config,info,op,artifact,mcp} ...")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Native Arbiter client.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "commands:")
	fmt.Fprintln(w, "  bootstrap client  create the Arbiter client config")
	fmt.Fprintln(w, "  config mcp-url  print the resolved MCP URL")
	fmt.Fprintln(w, "  info [--short]  discover Arbiter server identity and services")
	fmt.Fprintln(w, "  op              inspect or run Arbiter operations")
	fmt.Fprintln(w, "  artifact        explicitly fetch Arbiter artifacts")
	fmt.Fprintln(w, "  mcp             inspect or call raw MCP tools")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "The Go client is experimental.")
}

func printArtifactHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter artifact {get,save,with-temp,with-stdin} ...")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Explicitly access Arbiter artifacts.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "commands:")
	fmt.Fprintln(w, "  get         write a small textual artifact to stdout")
	fmt.Fprintln(w, "  save        save an artifact to a file only on explicit user request")
	fmt.Fprintln(w, "  with-temp   run a command with the artifact as a private temporary file")
	fmt.Fprintln(w, "  with-stdin  run a command with the artifact bytes on stdin")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Safety:")
	fmt.Fprintln(w, "  get --stdout is text-only and size-bounded.")
	fmt.Fprintln(w, "  save is only for when the user explicitly asks to save a file.")
	fmt.Fprintln(w, "  with-temp and with-stdin never write raw artifact bytes to stdout.")
}

func printArtifactGetHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter artifact get <url> --stdout [--max-bytes N]")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Fetch one small textual artifact URL to stdout.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "options:")
	fmt.Fprintln(w, "  --stdout       write a small textual artifact to stdout")
	fmt.Fprintln(w, "  --max-bytes N  maximum bytes to write with --stdout")
}

func printArtifactSaveHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter artifact save <url> <path>")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Save one artifact URL to a local file.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Use only when the user explicitly requests saving the artifact to a file.")
	fmt.Fprintln(w, "This command never writes artifact bytes to stdout.")
	fmt.Fprintln(w, "The output path must not already exist.")
}

func printArtifactWithTempHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter artifact with-temp <url> [--max-child-stdout-bytes N] -- <argv...>")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Run a command with the artifact downloaded to a private temporary file.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Use {} in argv where the temporary path should be substituted.")
	fmt.Fprintln(w, "The command is executed directly, without a shell.")
	fmt.Fprintln(w, "Only bounded textual child stdout is written back.")
}

func printArtifactWithStdinHelp(w io.Writer, commandName string) {
	fmt.Fprintf(w, "usage: arbiter artifact %s <url> [--max-child-stdout-bytes N] -- <argv...>\n", commandName)
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Run a command with the artifact bytes streamed to child stdin.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "The command is executed directly, without a shell.")
	fmt.Fprintln(w, "Raw artifact bytes are never written to stdout by Arbiter.")
	fmt.Fprintln(w, "Only bounded textual child stdout is written back.")
}
