package cli

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"mime"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"sort"
	"strconv"
	"strings"
	"time"
	"unicode/utf8"

	"github.com/omry/arbiter/client/go-cli/internal/arbiterhttp"
)

const (
	DefaultURL                                = "https://127.0.0.1:8075"
	URLEnvVar                                 = "ARBITER_URL"
	DefaultConfigDir                          = ".arbiter"
	DefaultClientConfigName                   = "arbiter-client.yaml"
	DefaultArtifactMaxBytes                   = 16 * 1024
	DefaultArtifactCommandMaxChildStdoutBytes = 256 * 1024
)

type EnvLookup func(string) (string, bool)
type HomeDirFunc func() (string, error)

type ResolvedURL struct {
	URL    string
	Source string
}

type ResolvedClientConfig struct {
	URL       string
	Source    string
	TLSCAFile string
}

type arbiterClient interface {
	Info(context.Context) (map[string]any, error)
	Plugins(context.Context) (map[string]any, error)
	PluginDetails(context.Context, string) (map[string]any, error)
	PluginAccounts(context.Context, string) (map[string]any, error)
	PluginAccount(context.Context, string, string) (map[string]any, error)
	PluginPolicy(context.Context, string, string) (map[string]any, error)
	PluginOperations(context.Context, string) (map[string]any, error)
	OperationDetails(context.Context, string) (map[string]any, error)
	RunOperation(context.Context, string, map[string]any) (map[string]any, error)
}

var newArbiterClient = func(url string) arbiterClient {
	if allowLocalSelfSignedTLS(url) {
		return arbiterhttp.NewClientInsecureTLS(url)
	}
	return arbiterhttp.NewClient(url)
}

var newArbiterClientWithConfig = func(config ResolvedClientConfig) (arbiterClient, error) {
	if config.TLSCAFile != "" {
		client, err := arbiterhttp.NewClientWithTLSCAFile(config.URL, config.TLSCAFile)
		if err != nil {
			return nil, err
		}
		return client, nil
	}
	return newArbiterClient(config.URL), nil
}

var newArtifactHTTPClient = func() *http.Client {
	return &http.Client{Timeout: 30 * time.Second}
}

func artifactHTTPClient(
	options globalOptions,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
	artifactURL string,
) (*http.Client, error) {
	config, err := ResolveClientConfig(options.Overrides, lookupEnv, homeDir, options)
	if err != nil {
		return nil, err
	}
	if config.TLSCAFile != "" {
		return arbiterhttp.NewHTTPClientWithTLSCAFile(config.TLSCAFile)
	}
	if allowLocalSelfSignedTLS(artifactURL) {
		return arbiterhttp.NewHTTPClientInsecureTLS(), nil
	}
	return newArtifactHTTPClient(), nil
}

func allowLocalSelfSignedTLS(rawURL string) bool {
	parsed, err := url.Parse(rawURL)
	if err != nil || parsed.Scheme != "https" {
		return false
	}
	host := strings.TrimSuffix(strings.ToLower(parsed.Hostname()), ".")
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

type globalOptions struct {
	ConfigDir  string
	ConfigName string
	Overrides  []string
}

type outputFormat string

const (
	outputJSON outputFormat = "json"
	outputYAML outputFormat = "yaml"
)

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
		if helpArgsIncludeExtended(remaining[1:]) {
			printExtendedHelp(stdout)
		} else {
			printHelp(stdout)
		}
		return 0
	case "--version":
		fmt.Fprintf(stdout, "arbiter %s\n", Version)
		return 0
	case "bootstrap":
		return runBootstrap(remaining[1:], options, stdout, stderr, lookupEnv, homeDir)
	case "config":
		return runConfig(remaining[1:], options, stdout, stderr, lookupEnv, homeDir)
	case "info":
		return runInfo(remaining[1:], options, stdout, stderr, lookupEnv, homeDir)
	case "plugins":
		return runPlugins(remaining[1:], options, stdout, stderr, lookupEnv, homeDir)
	case "op":
		return runOperation(remaining[1:], options, stdout, stderr, lookupEnv, homeDir)
	case "artifact":
		return runArtifact(remaining[1:], options, stdout, stderr, lookupEnv, homeDir)
	default:
		fmt.Fprintf(stderr, "Arbiter usage error: unknown command: %s\n", remaining[0])
		printShortUsage(stderr)
		return 2
	}
}

func runArtifact(
	args []string,
	globalOptions globalOptions,
	stdout io.Writer,
	stderr io.Writer,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
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
		httpClient, err := artifactHTTPClient(
			globalOptions,
			lookupEnv,
			homeDir,
			options.URL,
		)
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter client config error: %v\n", err)
			return 1
		}
		if err := fetchArtifact(context.Background(), options, httpClient, stdout); err != nil {
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
		httpClient, err := artifactHTTPClient(
			globalOptions,
			lookupEnv,
			homeDir,
			options.URL,
		)
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter client config error: %v\n", err)
			return 1
		}
		if err := saveArtifactToFile(context.Background(), options.URL, options.OutputPath, httpClient); err != nil {
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
		httpClient, err := artifactHTTPClient(
			globalOptions,
			lookupEnv,
			homeDir,
			options.URL,
		)
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter client config error: %v\n", err)
			return 1
		}
		if err := runArtifactWithTemp(context.Background(), options, httpClient, stdout, stderr); err != nil {
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
		httpClient, err := artifactHTTPClient(
			globalOptions,
			lookupEnv,
			homeDir,
			options.URL,
		)
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter client config error: %v\n", err)
			return 1
		}
		if err := runArtifactWithStdin(context.Background(), options, httpClient, stdout, stderr); err != nil {
			fmt.Fprintf(stderr, "Arbiter artifact error: %s\n", err)
			return 1
		}
		return 0
	default:
		printUsageError(stderr, fmt.Sprintf("unknown artifact command: %s", args[0]), "arbiter artifact")
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
	if len(args) == 1 && isHelpFlag(args[0]) {
		printBootstrapHelp(stdout)
		return 0
	}
	if len(args) == 2 && args[0] == "client" && isHelpFlag(args[1]) {
		printBootstrapClientHelp(stdout)
		return 0
	}
	if len(args) == 0 || args[0] != "client" {
		printUsageError(stderr, "expected: arbiter bootstrap client [--force]", "arbiter bootstrap")
		return 2
	}
	force := false
	if len(args) == 2 && args[1] == "--force" {
		force = true
	} else if len(args) != 1 {
		printUsageError(stderr, "expected: arbiter bootstrap client [--force]", "arbiter bootstrap")
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
	serverURL := DefaultURL
	if override, ok := urlOverride(options.Overrides); ok {
		serverURL = override
	} else if value, ok := lookupEnv(URLEnvVar); ok && value != "" {
		serverURL = value
	}
	content := fmt.Sprintf("arbiter:\n  url: %q\n", serverURL)
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
	if len(args) == 0 || (len(args) == 1 && isHelpFlag(args[0])) {
		printConfigHelp(stdout)
		return 0
	}
	if len(args) == 2 && args[0] == "url" && isHelpFlag(args[1]) {
		printConfigURLHelp(stdout)
		return 0
	}
	if len(args) != 1 || args[0] != "url" {
		printUsageError(stderr, "expected: arbiter config url", "arbiter config")
		return 2
	}

	resolved, err := ResolveURL(options.Overrides, lookupEnv, homeDir, options)
	if err != nil {
		fmt.Fprintf(stderr, "Arbiter client config error: %v\n", err)
		return 1
	}
	fmt.Fprintf(stdout, "url: %s\nsource: %s\n", resolved.URL, resolved.Source)
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
	for len(args) > 0 {
		switch args[0] {
		case "--yaml":
			yaml = true
		default:
			goto parsedFlags
		}
		args = args[1:]
	}
parsedFlags:
	if len(args) == 0 || (len(args) == 1 && isHelpFlag(args[0])) {
		printInfoHelp(stdout)
		return 0
	}
	if len(args) == 2 && args[0] == "server" && isHelpFlag(args[1]) {
		printInfoServerHelp(stdout)
		return 0
	}
	if len(args) != 1 || args[0] != "server" {
		printUsageError(stderr, "expected: arbiter info server", "arbiter info")
		return 2
	}
	payload, err := callToolPayload(
		"info",
		map[string]any{"kind": "server"},
		options,
		lookupEnv,
		homeDir,
	)
	if err != nil {
		fmt.Fprintf(stderr, "Arbiter tool error: %s\n", toolErrorForCLI(err, args, options, lookupEnv, homeDir))
		return 1
	}
	payload = withServerURL(payload, options, lookupEnv, homeDir)
	printStagedDeploymentWarning(stderr, payload, options, lookupEnv, homeDir)
	if yaml {
		printYAML(stdout, payload)
		return 0
	}
	printJSON(stdout, payload)
	return 0
}

func runPlugins(
	args []string,
	options globalOptions,
	stdout io.Writer,
	stderr io.Writer,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) int {
	if len(args) == 1 && isHelpFlag(args[0]) {
		printPluginsHelp(stdout)
		return 0
	}
	positionals, format, err := parseOutputFormatArgs(args, 4)
	if err != nil {
		printUsageError(stderr, err.Error(), "arbiter plugins")
		return 2
	}
	if !validPluginsArgs(positionals) {
		printUsageError(
			stderr,
			"expected: arbiter plugins [plugin [accounts|account NAME|policy NAME]]",
			"arbiter plugins",
		)
		return 2
	}
	payload, err := pluginsPayload(positionals, options, lookupEnv, homeDir)
	if err != nil {
		fmt.Fprintf(stderr, "Arbiter tool error: %s\n", err)
		return 1
	}
	printStagedDeploymentWarning(stderr, payload, options, lookupEnv, homeDir)
	if format == outputYAML {
		printYAML(stdout, payload)
		return 0
	}
	printJSON(stdout, payload)
	return 0
}

func validPluginsArgs(args []string) bool {
	return len(args) == 0 ||
		len(args) == 1 ||
		(len(args) == 2 && args[1] == "accounts") ||
		(len(args) == 3 && (args[1] == "account" || args[1] == "policy"))
}

func runOperation(
	args []string,
	options globalOptions,
	stdout io.Writer,
	stderr io.Writer,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) int {
	if len(args) == 1 && isHelpFlag(args[0]) {
		printOperationHelp(stdout)
		return 0
	}
	if len(args) == 0 {
		printUsageError(stderr, "expected: arbiter op {list,desc,run} ...", "arbiter op")
		return 2
	}
	switch args[0] {
	case "list":
		if len(args) == 2 && isHelpFlag(args[1]) {
			printOperationListHelp(stdout)
			return 0
		}
		positionals, format, err := parseOutputFormatArgs(args[1:], 1)
		if err != nil {
			printUsageError(stderr, err.Error(), "arbiter op list")
			return 2
		}
		if len(positionals) > 1 {
			printUsageError(stderr, "expected: arbiter op list [plugin]", "arbiter op list")
			return 2
		}
		payload, err := listOperationPayload(positionals, options, lookupEnv, homeDir)
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter tool error: %s\n", err)
			return 1
		}
		renderOperationPayload(stdout, payload, format)
		return 0
	case "desc", "describe":
		if len(args) == 2 && isHelpFlag(args[1]) {
			printOperationDescHelp(stdout, args[0])
			return 0
		}
		positionals, format, err := parseOutputFormatArgs(args[1:], 1)
		if err != nil {
			printUsageError(stderr, err.Error(), "arbiter op "+args[0])
			return 2
		}
		if len(positionals) != 1 {
			printUsageError(stderr, "expected: arbiter op desc <plugin-or-operation-id>", "arbiter op desc")
			return 2
		}
		payload, err := describeOperationTarget(positionals[0], options, lookupEnv, homeDir)
		if err != nil {
			fmt.Fprintf(stderr, "Arbiter tool error: %s\n", err)
			return 1
		}
		renderOperationPayload(stdout, payload, format)
		return 0
	case "run":
		if len(args) == 2 && isHelpFlag(args[1]) {
			printOperationRunHelp(stdout)
			return 0
		}
		if len(args) < 2 {
			printUsageError(stderr, "expected: arbiter op run <operation-id> [--args <json-object>]", "arbiter op run")
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
		printUsageError(stderr, fmt.Sprintf("unknown op command: %s", args[0]), "arbiter op")
		return 2
	}
}

func describeOperationTarget(
	target string,
	options globalOptions,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) (any, error) {
	if strings.Contains(target, ":") {
		return callToolPayload("describe_op", map[string]any{"id": target}, options, lookupEnv, homeDir)
	}
	return callToolPayload("info", map[string]any{"kind": "plugin", "plugin": target}, options, lookupEnv, homeDir)
}

func listOperationPayload(
	args []string,
	options globalOptions,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) (any, error) {
	if len(args) == 1 {
		payload, err := callToolPayload("info", map[string]any{"kind": "ops", "plugin": args[0]}, options, lookupEnv, homeDir)
		if err != nil {
			return nil, err
		}
		structuredPayload, err := operationListStructuredPayload(payload)
		if err != nil {
			return nil, err
		}
		return structuredPayload, nil
	}
	payload, err := callToolPayload("info", map[string]any{"kind": "plugins"}, options, lookupEnv, homeDir)
	if err != nil {
		return nil, err
	}
	pluginIDs, err := pluginIDsFromInfoPayload(payload)
	if err != nil {
		return nil, err
	}
	return map[string]any{"plugins": pluginIDs}, nil
}

func ResolveURL(
	args []string,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
	options ...globalOptions,
) (ResolvedURL, error) {
	resolved, err := ResolveClientConfig(args, lookupEnv, homeDir, options...)
	if err != nil {
		return ResolvedURL{}, err
	}
	return ResolvedURL{URL: resolved.URL, Source: resolved.Source}, nil
}

func ResolveClientConfig(
	args []string,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
	options ...globalOptions,
) (ResolvedClientConfig, error) {
	configOptions := defaultGlobalOptions()
	if len(options) > 0 {
		configOptions = options[0]
	}

	if override, ok := urlOverride(args); ok {
		return ResolvedClientConfig{
			URL:    override,
			Source: "override",
		}, nil
	}

	if value, ok := lookupEnv(URLEnvVar); ok && value != "" {
		return ResolvedClientConfig{
			URL:    value,
			Source: URLEnvVar,
		}, nil
	}

	configPath, err := clientConfigPath(configOptions, homeDir)
	if err != nil {
		return ResolvedClientConfig{}, err
	}
	config, configFound, err := readClientConfig(configPath)
	if err != nil {
		return ResolvedClientConfig{}, err
	}

	if configFound && config.URL != "" {
		return ResolvedClientConfig{
			URL:       config.URL,
			Source:    configPath,
			TLSCAFile: config.TLSCAFile,
		}, nil
	}

	return ResolvedClientConfig{
		URL:       DefaultURL,
		Source:    "default",
		TLSCAFile: config.TLSCAFile,
	}, nil
}

func urlOverride(args []string) (string, bool) {
	for _, arg := range args {
		if strings.HasPrefix(arg, "arbiter.url=") {
			return strings.TrimPrefix(arg, "arbiter.url="), true
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
		case strings.HasPrefix(arg, "arbiter.url="):
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

func pluginIDsFromInfoPayload(payload any) ([]string, error) {
	mapping, ok := payload.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("unexpected plugins payload")
	}
	pluginItems, ok := mapping["plugins"].([]any)
	if !ok {
		return nil, fmt.Errorf("unexpected plugins payload")
	}
	plugins := []string{}
	for _, pluginItem := range pluginItems {
		plugin, ok := pluginItem.(map[string]any)
		if !ok {
			return nil, fmt.Errorf("unexpected plugins payload")
		}
		pluginID, ok := plugin["id"].(string)
		if !ok || pluginID == "" {
			return nil, fmt.Errorf("unexpected plugins payload")
		}
		plugins = append(plugins, pluginID)
	}
	sort.Strings(plugins)
	return plugins, nil
}

func operationListStructuredPayload(payload any) (any, error) {
	mapping, ok := payload.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("unexpected operations payload")
	}
	operationsByID, _, err := operationsByIDFromInfoPayload(payload)
	if err != nil {
		return nil, err
	}
	structured := make(map[string]any, len(mapping))
	for key, value := range mapping {
		structured[key] = value
	}
	structured["operations"] = operationsByID
	return structured, nil
}

func operationsByIDFromInfoPayload(payload any) (map[string]any, []string, error) {
	mapping, ok := payload.(map[string]any)
	if !ok {
		return nil, nil, fmt.Errorf("unexpected operations payload")
	}
	operationItems, ok := mapping["operations"].([]any)
	if !ok {
		return nil, nil, fmt.Errorf("unexpected operations payload")
	}
	operationsByID := map[string]any{}
	operationIDs := []string{}
	for _, operationItem := range operationItems {
		operation, ok := operationItem.(map[string]any)
		if !ok {
			return nil, nil, fmt.Errorf("unexpected operations payload")
		}
		operationID, ok := operation["id"].(string)
		if !ok || operationID == "" {
			return nil, nil, fmt.Errorf("unexpected operations payload")
		}
		operationSummary := make(map[string]any, len(operation))
		for key, value := range operation {
			if key != "id" {
				operationSummary[key] = value
			}
		}
		operationsByID[operationID] = operationSummary
		operationIDs = append(operationIDs, operationID)
	}
	sort.Strings(operationIDs)
	return operationsByID, operationIDs, nil
}

func parseOutputFormatArgs(args []string, maxPositionals int) ([]string, outputFormat, error) {
	format := outputJSON
	selected := ""
	positionals := []string{}
	for _, arg := range args {
		switch arg {
		case "--json", "--yaml":
			if selected != "" && selected != arg {
				return nil, "", fmt.Errorf("choose only one output format: --json or --yaml")
			}
			selected = arg
			switch arg {
			case "--json":
				format = outputJSON
			case "--yaml":
				format = outputYAML
			}
		default:
			if strings.HasPrefix(arg, "-") {
				return nil, "", fmt.Errorf("unknown output option: %s", arg)
			}
			positionals = append(positionals, arg)
			if len(positionals) > maxPositionals {
				return positionals, format, nil
			}
		}
	}
	return positionals, format, nil
}

func renderOperationPayload(w io.Writer, payload any, format outputFormat) {
	switch format {
	case outputYAML:
		printYAML(w, payload)
	default:
		printJSON(w, payload)
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
	httpClient *http.Client,
	stdout io.Writer,
) error {
	if options.Stdout {
		return writeArtifactToStdout(ctx, options.URL, options.MaxBytes, httpClient, stdout)
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
	httpClient *http.Client,
	stdout io.Writer,
	stderr io.Writer,
) error {
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
	httpClient *http.Client,
	stdout io.Writer,
	stderr io.Writer,
) error {
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
	httpClient *http.Client,
	stdout io.Writer,
) error {
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
	httpClient *http.Client,
) error {
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
	client, err := newInitializedArbiterClient(options, lookupEnv, homeDir)
	if err != nil {
		return nil, err
	}
	ctx := context.Background()
	switch name {
	case "info":
		return nativeInfoPayload(ctx, client, arguments)
	case "describe_op":
		operationID, ok := arguments["id"].(string)
		if !ok || operationID == "" {
			return nil, fmt.Errorf("operation id must be non-empty")
		}
		return client.OperationDetails(ctx, operationID)
	case "run_op":
		operationID, ok := arguments["id"].(string)
		if !ok || operationID == "" {
			return nil, fmt.Errorf("operation id must be non-empty")
		}
		operationArgs, ok := arguments["arguments"].(map[string]any)
		if !ok || operationArgs == nil {
			operationArgs = map[string]any{}
		}
		payload, err := client.RunOperation(ctx, operationID, operationArgs)
		if err != nil {
			return nil, err
		}
		return payload, nil
	default:
		return nil, fmt.Errorf("unsupported Arbiter client action: %s", name)
	}
}

func newInitializedArbiterClient(
	options globalOptions,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) (arbiterClient, error) {
	resolved, err := ResolveClientConfig(options.Overrides, lookupEnv, homeDir, options)
	if err != nil {
		return nil, err
	}
	return newArbiterClientWithConfig(resolved)
}

func nativeInfoPayload(
	ctx context.Context,
	client arbiterClient,
	arguments map[string]any,
) (any, error) {
	kind, _ := arguments["kind"].(string)
	if kind == "" {
		kind = "server"
	}
	switch kind {
	case "server":
		return client.Info(ctx)
	case "plugins":
		plugins, err := client.Plugins(ctx)
		if err != nil {
			return nil, err
		}
		payload := copyStringMap(plugins)
		payload["kind"] = "plugins"
		return payload, nil
	case "plugin":
		plugin, ok := arguments["plugin"].(string)
		if !ok || plugin == "" {
			return nil, fmt.Errorf("plugin id must be non-empty")
		}
		return nativePluginPayload(ctx, client, plugin)
	case "ops":
		plugin, ok := arguments["plugin"].(string)
		if !ok || plugin == "" {
			return nil, fmt.Errorf("plugin id must be non-empty")
		}
		return nativeOperationsPayload(ctx, client, plugin)
	case "op":
		plugin, pluginOK := arguments["plugin"].(string)
		operation, operationOK := arguments["operation"].(string)
		if !pluginOK || !operationOK || plugin == "" || operation == "" {
			return nil, fmt.Errorf("operation id must be non-empty")
		}
		return client.OperationDetails(ctx, plugin+":"+operation)
	default:
		return nil, fmt.Errorf("unknown info kind: %s", kind)
	}
}

func nativePluginPayload(
	ctx context.Context,
	client arbiterClient,
	plugin string,
) (map[string]any, error) {
	selected, err := client.PluginDetails(ctx, plugin)
	if err != nil {
		return nil, err
	}
	operations, err := nativeOperationsPayload(ctx, client, plugin)
	if err != nil {
		return nil, err
	}
	payload := map[string]any{
		"kind":       "plugin",
		"id":         plugin,
		"operations": operations["operations"],
	}
	if summary, ok := selected["summary"].(string); ok && summary != "" {
		payload["summary"] = summary
	}
	return payload, nil
}

func pluginsPayload(
	args []string,
	options globalOptions,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) (any, error) {
	client, err := newInitializedArbiterClient(options, lookupEnv, homeDir)
	if err != nil {
		return nil, err
	}
	ctx := context.Background()
	switch {
	case len(args) == 0:
		return client.Plugins(ctx)
	case len(args) == 1:
		return client.PluginDetails(ctx, args[0])
	case len(args) == 2 && args[1] == "accounts":
		return client.PluginAccounts(ctx, args[0])
	case len(args) == 3 && args[1] == "account":
		return client.PluginAccount(ctx, args[0], args[2])
	case len(args) == 3 && args[1] == "policy":
		return client.PluginPolicy(ctx, args[0], args[2])
	default:
		return nil, fmt.Errorf("expected: arbiter plugins [plugin [accounts|account NAME|policy NAME]]")
	}
}

func nativeOperationsPayload(
	ctx context.Context,
	client arbiterClient,
	plugin string,
) (map[string]any, error) {
	operations, err := client.PluginOperations(ctx, plugin)
	if err != nil {
		return nil, err
	}
	payload := copyStringMap(operations)
	payload["kind"] = "ops"
	payload["plugin"] = plugin
	return payload, nil
}

func copyStringMap(source map[string]any) map[string]any {
	target := make(map[string]any, len(source)+1)
	for key, value := range source {
		target[key] = value
	}
	return target
}

func anySlice(value any) []any {
	items, ok := value.([]any)
	if !ok {
		return nil
	}
	return items
}

func withServerURL(payload any, options globalOptions, lookupEnv EnvLookup, homeDir HomeDirFunc) any {
	resolved, err := ResolveURL(options.Overrides, lookupEnv, homeDir, options)
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

func printStagedDeploymentWarning(
	stderr io.Writer,
	payload any,
	options globalOptions,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) {
	if deploymentScope, known := deploymentScopeFromPayload(payload); known {
		if deploymentScope == "staged" {
			printStagedDeploymentWarningForPayload(stderr, payload, options, lookupEnv, homeDir)
		}
		return
	}

	client, err := newInitializedArbiterClient(options, lookupEnv, homeDir)
	if err != nil {
		return
	}
	info, err := client.Info(context.Background())
	if err != nil {
		return
	}
	if deploymentScope, known := deploymentScopeFromPayload(info); known && deploymentScope == "staged" {
		printStagedDeploymentWarningForPayload(stderr, payload, options, lookupEnv, homeDir)
	}
}

func printStagedDeploymentWarningForPayload(
	stderr io.Writer,
	payload any,
	options globalOptions,
	lookupEnv EnvLookup,
	homeDir HomeDirFunc,
) {
	url := serverURLFromPayload(payload)
	if url == "" {
		if resolved, err := ResolveURL(options.Overrides, lookupEnv, homeDir, options); err == nil {
			url = resolved.URL
		}
	}
	if url == "" {
		fmt.Fprintln(stderr, "Heads up: connected to staged Arbiter.")
		return
	}
	fmt.Fprintf(stderr, "Heads up: connected to staged Arbiter at %s.\n", url)
}

func deploymentScopeFromPayload(payload any) (string, bool) {
	mapping, ok := payload.(map[string]any)
	if !ok {
		return "", false
	}
	if deploymentScope, ok := mapping["deployment_scope"].(string); ok {
		return deploymentScope, true
	}
	if server, ok := mapping["server"].(map[string]any); ok {
		if deploymentScope, ok := server["deployment_scope"].(string); ok {
			return deploymentScope, true
		}
		return "", true
	}
	return "", false
}

func serverURLFromPayload(payload any) string {
	mapping, ok := payload.(map[string]any)
	if !ok {
		return ""
	}
	url, _ := mapping["server_url"].(string)
	return url
}

func toolErrorForCLI(err error, infoArgs []string, options globalOptions, lookupEnv EnvLookup, homeDir HomeDirFunc) string {
	message := err.Error()
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
		writeYAMLList(w, typed, indent)
	default:
		if writeReflectedYAML(w, value, indent) {
			return
		}
		fmt.Fprintf(w, "%s%v\n", prefix, typed)
	}
}

func writeYAMLList(w io.Writer, items []any, indent int) {
	prefix := strings.Repeat(" ", indent)
	for _, item := range items {
		if isScalar(item) {
			fmt.Fprintf(w, "%s- %v\n", prefix, item)
		} else if mapping, ok := yamlStringMap(item); ok {
			writeYAMLMapListItem(w, mapping, indent)
		} else {
			fmt.Fprintf(w, "%s-\n", prefix)
			writeYAML(w, item, indent+2)
		}
	}
}

func writeYAMLMapListItem(w io.Writer, mapping map[string]any, indent int) {
	prefix := strings.Repeat(" ", indent)
	if len(mapping) == 0 {
		fmt.Fprintf(w, "%s- {}\n", prefix)
		return
	}
	keys := make([]string, 0, len(mapping))
	for key := range mapping {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for index, key := range keys {
		child := mapping[key]
		keyPrefix := prefix + "  "
		if index == 0 {
			keyPrefix = prefix + "- "
		}
		if isScalar(child) {
			fmt.Fprintf(w, "%s%s: %v\n", keyPrefix, key, child)
		} else {
			fmt.Fprintf(w, "%s%s:\n", keyPrefix, key)
			writeYAML(w, child, indent+4)
		}
	}
}

func writeReflectedYAML(w io.Writer, value any, indent int) bool {
	if value == nil {
		return false
	}
	reflected := reflect.ValueOf(value)
	switch reflected.Kind() {
	case reflect.Slice, reflect.Array:
		items := make([]any, 0, reflected.Len())
		for index := 0; index < reflected.Len(); index++ {
			items = append(items, reflected.Index(index).Interface())
		}
		writeYAMLList(w, items, indent)
		return true
	case reflect.Map:
		if reflected.Type().Key().Kind() != reflect.String {
			return false
		}
		mapping := make(map[string]any, reflected.Len())
		for _, key := range reflected.MapKeys() {
			mapping[key.String()] = reflected.MapIndex(key).Interface()
		}
		writeYAML(w, mapping, indent)
		return true
	default:
		return false
	}
}

func yamlStringMap(value any) (map[string]any, bool) {
	if mapping, ok := value.(map[string]any); ok {
		return mapping, true
	}
	if value == nil {
		return nil, false
	}
	reflected := reflect.ValueOf(value)
	if reflected.Kind() != reflect.Map || reflected.Type().Key().Kind() != reflect.String {
		return nil, false
	}
	mapping := make(map[string]any, reflected.Len())
	for _, key := range reflected.MapKeys() {
		mapping[key.String()] = reflected.MapIndex(key).Interface()
	}
	return mapping, true
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
		if index > infoIndex && arg == "--yaml" {
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

func readURLFromConfig(path string) (string, bool, error) {
	config, ok, err := readClientConfig(path)
	return config.URL, ok && config.URL != "", err
}

type clientConfig struct {
	URL       string
	TLSCAFile string
}

func readClientConfig(path string) (clientConfig, bool, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		if os.IsNotExist(err) {
			return clientConfig{}, false, nil
		}
		return clientConfig{}, false, err
	}

	config, ok, err := parseClientConfig(path, string(data))
	return config, ok, err
}

func parseURLConfig(path string, data string) (string, bool, error) {
	config, ok, err := parseClientConfig(path, data)
	return config.URL, ok && config.URL != "", err
}

func parseClientConfig(path string, data string) (clientConfig, bool, error) {
	inArbiter := false
	foundArbiter := false
	foundURL := false
	foundTLSCAFile := false
	config := clientConfig{}
	for _, line := range strings.Split(data, "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		indented := strings.HasPrefix(line, " ") || strings.HasPrefix(line, "\t")
		if !indented {
			key, value, ok := strings.Cut(trimmed, ":")
			if !ok {
				return clientConfig{}, false, fmt.Errorf("unsupported client config entry in %s: %s", path, trimmed)
			}
			key = strings.TrimSpace(key)
			value = strings.TrimSpace(value)
			if key != "arbiter" {
				return clientConfig{}, false, fmt.Errorf("unsupported client config key(s) in %s: %s", path, key)
			}
			if foundArbiter {
				return clientConfig{}, false, fmt.Errorf("duplicate client config key in %s: arbiter", path)
			}
			foundArbiter = true
			if value != "" {
				return clientConfig{}, false, fmt.Errorf("client config arbiter must be a mapping: %s", path)
			}
			inArbiter = true
			continue
		}

		if !inArbiter {
			return clientConfig{}, false, fmt.Errorf("unsupported indented client config entry in %s: %s", path, trimmed)
		}
		key, value, ok := strings.Cut(trimmed, ":")
		if !ok {
			return clientConfig{}, false, fmt.Errorf("unsupported client config arbiter entry in %s: %s", path, trimmed)
		}
		key = strings.TrimSpace(key)
		if key != "url" && key != "tls_ca_file" {
			return clientConfig{}, false, fmt.Errorf("unsupported client config arbiter key(s) in %s: %s", path, key)
		}
		parsedValue, err := parseConfigStringScalar(strings.TrimSpace(value), path, "arbiter."+key)
		if err != nil {
			return clientConfig{}, false, err
		}
		if key == "tls_ca_file" {
			if foundTLSCAFile {
				return clientConfig{}, false, fmt.Errorf("duplicate client config arbiter key in %s: tls_ca_file", path)
			}
			foundTLSCAFile = true
			config.TLSCAFile = parsedValue
			continue
		}
		if foundURL {
			return clientConfig{}, false, fmt.Errorf("duplicate client config arbiter key in %s: url", path)
		}
		foundURL = true
		config.URL = parsedValue
	}
	return config, foundURL || foundTLSCAFile, nil
}

func parseConfigStringScalar(value string, path string, key string) (string, error) {
	if value == "" {
		return "", fmt.Errorf("client config %s must be a string: %s", key, path)
	}
	if strings.HasPrefix(value, `"`) || strings.HasPrefix(value, `'`) {
		quote := value[:1]
		if !strings.HasSuffix(value, quote) || len(value) == 1 {
			return "", fmt.Errorf("client config %s must be a string: %s", key, path)
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
		return "", fmt.Errorf("client config %s must be a string: %s", key, path)
	}
	if _, err := strconv.ParseFloat(value, 64); err == nil {
		return "", fmt.Errorf("client config %s must be a string: %s", key, path)
	}
	return value, nil
}

func printShortUsage(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter {info,plugins,op,artifact} ...")
	fmt.Fprintln(w, "Run 'arbiter --help' for help, or 'arbiter --help --extended' for setup and advanced commands.")
}

func printUsageError(w io.Writer, message string, helpCommand string) {
	fmt.Fprintf(w, "Arbiter usage error: %s\n", message)
	if helpCommand != "" {
		fmt.Fprintf(w, "Run '%s --help' for help.\n", helpCommand)
	}
}

func printHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter [--version] {info,plugins,op,artifact} ...")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Native Arbiter client.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "primary commands:")
	fmt.Fprintln(w, "  info            inspect Arbiter server identity")
	fmt.Fprintln(w, "  plugins         inspect Arbiter plugins, accounts, and policies")
	fmt.Fprintln(w, "  op              inspect or run Arbiter operations")
	fmt.Fprintln(w, "  artifact        safely read, process, or explicitly save artifacts")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Run 'arbiter --help --extended' for setup and advanced commands.")
}

func printExtendedHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter [--version] {info,plugins,op,artifact,bootstrap,config} ...")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Native Arbiter client.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "primary commands:")
	fmt.Fprintln(w, "  info             inspect Arbiter server identity")
	fmt.Fprintln(w, "  plugins          inspect Arbiter plugins, accounts, and policies")
	fmt.Fprintln(w, "  op               inspect or run Arbiter operations")
	fmt.Fprintln(w, "  artifact         safely read, process, or explicitly save artifacts")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "setup:")
	fmt.Fprintln(w, "  bootstrap client create the Arbiter client config")
	fmt.Fprintln(w, "  config url   print the resolved URL and source")
}

func helpArgsIncludeExtended(args []string) bool {
	for _, arg := range args {
		if arg == "--extended" {
			return true
		}
	}
	return false
}

func isHelpFlag(arg string) bool {
	return arg == "-h" || arg == "--help"
}

func printBootstrapHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter bootstrap {client} ...")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Create Arbiter bootstrap files.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "commands:")
	fmt.Fprintln(w, "  client  write the Arbiter client config")
}

func printBootstrapClientHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter bootstrap client [--force]")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Write the Arbiter client config file.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "options:")
	fmt.Fprintln(w, "  --force  overwrite an existing client config")
}

func printConfigHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter config {url} ...")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Inspect Arbiter client configuration.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "commands:")
	fmt.Fprintln(w, "  url  print the resolved URL and source")
}

func printConfigURLHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter config url")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Print the URL resolved from overrides, environment, config, or default.")
	fmt.Fprintln(w, "The source is override, ARBITER_URL, a config file path, or default.")
}

func printInfoHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter info {server} ...")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Inspect Arbiter server identity.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "commands:")
	fmt.Fprintln(w, "  server  show server identity and connection URL")
}

func printInfoServerHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter info server [--yaml]")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Show Arbiter server identity, deployment scope, source metadata, and resolved URL.")
}

func printPluginsHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter plugins [plugin [accounts|account NAME|policy NAME]] [--json|--yaml]")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Inspect Arbiter plugins, accounts, and read-only policy details.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "commands:")
	fmt.Fprintln(w, "  plugins                         list plugins")
	fmt.Fprintln(w, "  plugins <plugin>                describe one plugin")
	fmt.Fprintln(w, "  plugins <plugin> accounts       list plugin accounts")
	fmt.Fprintln(w, "  plugins <plugin> account NAME   show one account")
	fmt.Fprintln(w, "  plugins <plugin> policy NAME    show one policy")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "options:")
	fmt.Fprintln(w, "  --json   print structured JSON (default)")
	fmt.Fprintln(w, "  --yaml   print structured YAML")
}

func printOperationHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter op {list,desc,run} ...")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Discover, inspect, or run Arbiter operations.")
	fmt.Fprintln(w, "Operation ids use <plugin>:<operation> syntax.")
	fmt.Fprintln(w, "Discovery and inspection commands print JSON by default.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "commands:")
	fmt.Fprintln(w, "  list                            list plugins")
	fmt.Fprintln(w, "  list <plugin>                   list operations for one plugin")
	fmt.Fprintln(w, "  desc <plugin>                   describe one plugin's operation surface")
	fmt.Fprintln(w, "  desc <operation-id>             describe one operation")
	fmt.Fprintln(w, "  run <operation-id> [--args JSON] run one operation")
}

func printOperationListHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter op list [plugin] [--json|--yaml]")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "List plugins. Pass a plugin to list operation summaries keyed by operation id.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "options:")
	fmt.Fprintln(w, "  --json   print structured JSON (default)")
	fmt.Fprintln(w, "  --yaml   print structured YAML")
}

func printOperationDescHelp(w io.Writer, commandName string) {
	fmt.Fprintf(w, "usage: arbiter op %s <plugin-or-operation-id> [--json|--yaml]\n", commandName)
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Describe a plugin's operation surface or one Arbiter operation.")
	fmt.Fprintln(w, "Use a plugin id such as imap, or an operation id such as imap:get_message.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "options:")
	fmt.Fprintln(w, "  --json   print structured JSON (default)")
	fmt.Fprintln(w, "  --yaml   print structured YAML")
}

func printOperationRunHelp(w io.Writer) {
	fmt.Fprintln(w, "usage: arbiter op run <operation-id> [--args <json-object>]")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "Run one Arbiter operation.")
	fmt.Fprintln(w, "Discover plugins with 'arbiter op list', then operation ids with 'arbiter op list <plugin>'.")
	fmt.Fprintln(w)
	fmt.Fprintln(w, "options:")
	fmt.Fprintln(w, "  --args JSON  operation arguments as a JSON object")
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
