package cli

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/omry/arbiter/client/go-cli/internal/mcp"
)

const (
	DefaultMCPURL           = "http://127.0.0.1:8000/mcp"
	MCPURLEnvVar            = "ARBITER_MCP_URL"
	DefaultConfigDir        = ".arbiter"
	DefaultClientConfigName = "arbiter-client.yaml"
	DefaultArtifactMaxBytes = 16 * 1024
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
		fmt.Fprintln(stderr, "Arbiter usage error: expected: arbiter artifact get <url> (--stdout [--max-bytes N] | --output PATH)")
		return 2
	}
	switch args[0] {
	case "get":
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
	if len(args) > 0 && args[0] == "--yaml" {
		yaml = true
		args = args[1:]
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
	URL        string
	Stdout     bool
	OutputPath string
	MaxBytes   int64
}

func parseArtifactGetArgs(args []string) (artifactGetOptions, error) {
	options := artifactGetOptions{MaxBytes: int64(DefaultArtifactMaxBytes)}
	if len(args) < 2 {
		if len(args) == 1 && strings.TrimSpace(args[0]) != "" {
			return options, fmt.Errorf("artifact get requires exactly one of --stdout or --output PATH")
		}
		return options, fmt.Errorf("expected: arbiter artifact get <url> (--stdout [--max-bytes N] | --output PATH)")
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
		case "--output":
			if index+1 >= len(args) {
				return options, fmt.Errorf("--output requires a value")
			}
			if strings.TrimSpace(args[index+1]) == "" {
				return options, fmt.Errorf("--output path must be non-empty")
			}
			options.OutputPath = args[index+1]
			index++
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
	if options.Stdout == (options.OutputPath != "") {
		return options, fmt.Errorf("artifact get requires exactly one of --stdout or --output PATH")
	}
	if !options.Stdout && options.MaxBytes != int64(DefaultArtifactMaxBytes) {
		return options, fmt.Errorf("--max-bytes is only valid with --stdout")
	}
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
	return saveArtifactToFile(ctx, options.URL, options.OutputPath)
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
	output, err := os.OpenFile(outputPath, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	removeOutput := true
	defer func() {
		output.Close()
		if removeOutput {
			os.Remove(outputPath)
		}
	}()

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
	if _, err := io.Copy(output, getResp.Body); err != nil {
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
	var withoutYAML []string
	foundYAML := false
	for index, arg := range normalized {
		if index > infoIndex && arg == "--yaml" {
			foundYAML = true
			continue
		}
		withoutYAML = append(withoutYAML, arg)
	}
	if !foundYAML {
		return normalized
	}
	return append(
		append([]string{}, withoutYAML[:infoIndex+1]...),
		append([]string{"--yaml"}, withoutYAML[infoIndex+1:]...)...,
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
	fmt.Fprintln(w, "  info            discover Arbiter server identity and services")
	fmt.Fprintln(w, "  op              inspect or run Arbiter operations")
	fmt.Fprintln(w, "  artifact        explicitly fetch Arbiter artifacts")
	fmt.Fprintln(w, "  mcp             inspect or call raw MCP tools")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "The Go client is experimental.")
}
