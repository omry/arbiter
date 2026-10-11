package cli

import (
	"fmt"
	"strings"
	"time"

	"github.com/omry/arbiter/client/go-cli/internal/arbiterhttp"
)

type PluginTimeout struct {
	Default    time.Duration
	Operations map[string]time.Duration
}

func (config ResolvedClientConfig) operationTimeout(operationID string) time.Duration {
	if config.TimeoutOverride {
		return config.Timeout
	}
	plugin, operation, _ := strings.Cut(operationID, ":")
	if settings, ok := config.PluginTimeouts[plugin]; ok {
		if timeout, ok := settings.Operations[operation]; ok {
			return timeout
		}
		if settings.Default > 0 {
			return settings.Default
		}
	}
	return config.Timeout
}

func parseTimeout(value, key string) (time.Duration, error) {
	duration, err := time.ParseDuration(value)
	if err != nil || duration <= 0 {
		return 0, fmt.Errorf("%s must be a positive duration with units (for example 30s or 5m)", key)
	}
	return duration, nil
}

func isTimeoutOverride(arg string) bool {
	key, _, ok := strings.Cut(arg, "=")
	return ok && (key == "arbiter.timeout" || key == "arbiter.connect_timeout" || key == "arbiter.download_idle_timeout")
}

func applyTimeoutOverrides(config *ResolvedClientConfig, args []string) error {
	for _, arg := range args {
		if !isTimeoutOverride(arg) {
			continue
		}
		key, value, _ := strings.Cut(arg, "=")
		duration, err := parseTimeout(value, key)
		if err != nil {
			return err
		}
		switch key {
		case "arbiter.timeout":
			config.Timeout = duration
			config.TimeoutOverride = true
		case "arbiter.connect_timeout":
			config.ConnectTimeout = duration
		case "arbiter.download_idle_timeout":
			config.DownloadIdleTimeout = duration
		}
	}
	return nil
}

func defaultResolvedClientConfig() ResolvedClientConfig {
	return ResolvedClientConfig{
		URL: DefaultURL, Source: "default",
		Timeout: arbiterhttp.DefaultTimeout, ConnectTimeout: arbiterhttp.DefaultConnectTimeout,
		DownloadIdleTimeout: arbiterhttp.DefaultDownloadIdleTimeout,
	}
}

// The client deliberately supports a small block-mapping YAML subset without
// third-party dependencies. Track indentation to reject malformed nesting and
// duplicate keys at every level, including plugin and operation names.
func parseConfigMapping(path, data string) (map[string]any, error) {
	type frame struct {
		indent, childIndent int
		values              map[string]any
	}
	root := map[string]any{}
	stack := []*frame{{indent: -1, childIndent: 0, values: root}}
	for _, line := range strings.Split(data, "\n") {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" || strings.HasPrefix(trimmed, "#") {
			continue
		}
		indent := len(line) - len(strings.TrimLeft(line, " "))
		if strings.Contains(line[:len(line)-len(strings.TrimLeft(line, " \t"))], "\t") {
			return nil, fmt.Errorf("client config indentation must use spaces: %s", path)
		}
		for len(stack) > 1 && indent <= stack[len(stack)-1].indent {
			stack = stack[:len(stack)-1]
		}
		parent := stack[len(stack)-1]
		if parent.childIndent < 0 {
			parent.childIndent = indent
		}
		if indent != parent.childIndent {
			return nil, fmt.Errorf("invalid client config indentation in %s: %s", path, trimmed)
		}
		key, value, ok := strings.Cut(trimmed, ":")
		key = strings.TrimSpace(key)
		if !ok || key == "" || strings.ContainsAny(key, " \t[]{}'\"") {
			return nil, fmt.Errorf("unsupported client config entry in %s: %s", path, trimmed)
		}
		if _, exists := parent.values[key]; exists {
			return nil, fmt.Errorf("duplicate client config key in %s: %s", path, key)
		}
		value = stripConfigComment(strings.TrimSpace(value))
		if value == "" {
			child := map[string]any{}
			parent.values[key] = child
			stack = append(stack, &frame{indent: indent, childIndent: -1, values: child})
		} else {
			parent.values[key] = value
		}
	}
	return root, nil
}

func stripConfigComment(value string) string {
	var quote byte
	if len(value) > 0 && (value[0] == '\'' || value[0] == '"') {
		quote = value[0]
	}
	for i := 0; i < len(value); i++ {
		if quote != 0 {
			if i == 0 {
				continue
			}
			if quote == '"' && value[i] == '\\' {
				i++
				continue
			}
			if value[i] == quote {
				if quote == '\'' && i+1 < len(value) && value[i+1] == '\'' {
					i++
					continue
				}
				quote = 0
			}
		} else if value[i] == '#' && (i == 0 || value[i-1] == ' ' || value[i-1] == '\t') {
			return strings.TrimSpace(value[:i])
		}
	}
	return strings.TrimSpace(value)
}

func configMapping(value any, path, key string) (map[string]any, error) {
	mapping, ok := value.(map[string]any)
	if !ok {
		return nil, fmt.Errorf("client config %s must be a mapping: %s", key, path)
	}
	return mapping, nil
}

func configString(value any, path, key string) (string, error) {
	text, ok := value.(string)
	if !ok {
		return "", fmt.Errorf("client config %s must be a string: %s", key, path)
	}
	return parseConfigStringScalar(text, path, key)
}

func configTimeout(value any, path, key string) (time.Duration, error) {
	text, err := configString(value, path, key)
	if err != nil {
		return 0, err
	}
	duration, err := parseTimeout(text, key)
	if err != nil {
		return 0, fmt.Errorf("client config %s: %w", path, err)
	}
	return duration, nil
}

func parseTimeoutClientConfig(path, data string) (clientConfig, bool, error) {
	root, err := parseConfigMapping(path, data)
	if err != nil {
		return clientConfig{}, false, err
	}
	for key := range root {
		if key != "arbiter" {
			return clientConfig{}, false, fmt.Errorf("unsupported client config key(s) in %s: %s", path, key)
		}
	}
	if _, exists := root["arbiter"]; !exists {
		return clientConfig{}, false, nil
	}
	mapping, err := configMapping(root["arbiter"], path, "arbiter")
	if err != nil {
		return clientConfig{}, false, err
	}
	config := clientConfig{}
	for key, value := range mapping {
		qualified := "arbiter." + key
		switch key {
		case "url":
			config.URL, err = configString(value, path, qualified)
		case "tls_ca_file":
			config.TLSCAFile, err = configString(value, path, qualified)
		case "timeout":
			config.Timeout, err = configTimeout(value, path, qualified)
		case "connect_timeout":
			config.ConnectTimeout, err = configTimeout(value, path, qualified)
		case "download_idle_timeout":
			config.DownloadIdleTimeout, err = configTimeout(value, path, qualified)
		case "plugins":
			config.PluginTimeouts, err = parsePluginTimeouts(value, path)
		default:
			err = fmt.Errorf("unsupported client config arbiter key(s) in %s: %s", path, key)
		}
		if err != nil {
			return clientConfig{}, false, err
		}
	}
	return config, len(mapping) > 0, nil
}

func parsePluginTimeouts(value any, path string) (map[string]PluginTimeout, error) {
	plugins, err := configMapping(value, path, "arbiter.plugins")
	if err != nil {
		return nil, err
	}
	result := map[string]PluginTimeout{}
	for plugin, value := range plugins {
		key := "arbiter.plugins." + plugin
		settings, err := configMapping(value, path, key)
		if err != nil {
			return nil, err
		}
		for name := range settings {
			if name != "timeout" {
				return nil, fmt.Errorf("unsupported client config key in %s: %s.%s", path, key, name)
			}
		}
		timeoutValue, exists := settings["timeout"]
		if !exists {
			continue
		}
		key += ".timeout"
		timeouts := PluginTimeout{Operations: map[string]time.Duration{}}
		if mapping, ok := timeoutValue.(map[string]any); ok {
			if len(mapping) == 0 {
				return nil, fmt.Errorf("client config %s must contain a duration or a non-empty timeout mapping: %s", key, path)
			}
			for operation, duration := range mapping {
				parsed, err := configTimeout(duration, path, key+"."+operation)
				if err != nil {
					return nil, err
				}
				if operation == "default" {
					timeouts.Default = parsed
				} else {
					timeouts.Operations[operation] = parsed
				}
			}
		} else {
			timeouts.Default, err = configTimeout(timeoutValue, path, key)
			if err != nil {
				return nil, err
			}
		}
		result[plugin] = timeouts
	}
	return result, nil
}
