package cli

import (
	"errors"
	"fmt"
	"io"
	"net"
	"net/url"
	"strings"

	"github.com/omry/arbiter/client/go-cli/internal/arbiterhttp"
)

// Carry the explicitly requested error format without changing child I/O.
type errorWriter struct {
	io.Writer
	format outputFormat
}

type safeClientError string

func (err safeClientError) Error() string { return string(err) }

func stripErrorFormatFlags(args []string) ([]string, error) {
	remaining := make([]string, 0, len(args))
	formatArgs := make([]string, 0, 2)
	for _, arg := range args {
		switch arg {
		case "--json", "--yaml":
			formatArgs = append(formatArgs, arg)
		default:
			remaining = append(remaining, arg)
		}
	}
	_, _, err := parseOutputFormatArgs(formatArgs, 0)
	if err != nil {
		return nil, err
	}
	return remaining, nil
}

func requestedErrorFormat(args []string) outputFormat {
	var format outputFormat
	_, normalized, err := parseGlobalOptions(args)
	opRunCommand := err == nil && len(normalized) > 1 && normalized[0] == "op" && normalized[1] == "run"
	artifactCommand := ""
	if err == nil && len(normalized) > 0 && normalized[0] == "artifact" {
		artifactCommand = artifactSubcommand(normalized[1:])
	}
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--":
			return format
		case "--args":
			if opRunCommand {
				i++ // Only op run owns the --args value.
			}
		case "--config-dir", "--config-name":
			i++ // Values and child arguments are data, not output flags.
		case "--max-bytes", "--max-child-stdout-bytes":
			if artifactOptionTakesValue(artifactCommand, args[i]) {
				i++ // Artifact size values are data, not output flags.
			}
		case "--json":
			format = outputJSON
		case "--yaml":
			format = outputYAML
		}
	}
	return format
}

func artifactSubcommand(args []string) string {
	normalized := args
	for len(normalized) > 0 && (normalized[0] == "--json" || normalized[0] == "--yaml") {
		normalized = normalized[1:]
	}
	if len(normalized) == 0 {
		return ""
	}
	switch normalized[0] {
	case "get", "save", "with-temp", "with-stdin":
		return normalized[0]
	default:
		return ""
	}
}

func artifactOptionTakesValue(command, option string) bool {
	switch command {
	case "get":
		return option == "--max-bytes"
	case "with-temp", "with-stdin":
		return option == "--max-child-stdout-bytes"
	default:
		return false
	}
}

func printClientError(w io.Writer, err error, kind string) {
	var apiErr arbiterhttp.APIError
	fields := map[string]any{}
	if errors.As(err, &apiErr) {
		for key, value := range apiErr.Fields {
			fields[key] = value
		}
		fields["code"] = apiErr.Code
		fields["message"] = apiErr.Message
	} else {
		code, message := "client_error", "Arbiter client request failed."
		switch kind {
		case "usage":
			code, message = "usage_error", "Invalid CLI arguments."
		case "client config":
			code, message = "client_config_error", "Invalid or unavailable client configuration."
		default:
			var netErr net.Error
			if errors.As(err, &netErr) {
				code, message = "transport_error", "Connection to Arbiter failed."
				var urlErr *url.Error
				if errors.As(err, &urlErr) {
					if endpoint, parseErr := url.Parse(urlErr.URL); parseErr == nil && endpoint.Host != "" {
						message += " Destination: " + endpoint.Scheme + "://" + endpoint.Host
					}
				}
				if netErr.Timeout() {
					code, message = "transport_timeout", "Request to Arbiter timed out; operation outcome may be unknown."
				}
			}
		}
		fields["code"], fields["message"] = code, message
		var safeErr safeClientError
		var diagnostic interface{ PublicMessage() string }
		if errors.As(err, &safeErr) {
			fields["message"] = string(safeErr)
		}
		if errors.As(err, &diagnostic) {
			fields["message"] = diagnostic.PublicMessage() + "; operation outcome may be unknown."
		}
	}
	if output, ok := w.(*errorWriter); ok && output.format != "" {
		renderOperationPayload(output.Writer, map[string]any{"error": fields}, output.format)
		return
	}
	if kind == "usage" {
		// Usage diagnostics are parser-owned; omit the supplied value after ':'
		// rather than echoing arbitrary arguments or payload contents.
		if strings.HasPrefix(err.Error(), "expected:") {
			fields["message"] = err.Error() // Only static syntax templates use this prefix.
		} else {
			fields["message"] = strings.SplitN(err.Error(), ":", 2)[0]
		}
	}
	if fields["message"] == "" {
		fields["message"] = fields["code"]
	}
	fmt.Fprintf(w, "Arbiter %s error: %s\n", kind, fields["message"])
}

// Artifact output is bytes or child output. These flags select only errors,
// and arguments after -- always belong to the child.
func artifactErrorFlags(args []string) ([]string, error) {
	var remaining []string
	var formatArgs []string
	command := artifactSubcommand(args)
	for i := 0; i < len(args); i++ {
		switch args[i] {
		case "--":
			remaining = append(remaining, args[i:]...)
			return remaining, validateErrorFormatFlags(formatArgs)
		case "--max-bytes", "--max-child-stdout-bytes":
			remaining = append(remaining, args[i])
			if artifactOptionTakesValue(command, args[i]) && i+1 < len(args) {
				i++
				remaining = append(remaining, args[i])
			}
		case "--json", "--yaml":
			formatArgs = append(formatArgs, args[i])
		default:
			remaining = append(remaining, args[i])
		}
	}
	return remaining, validateErrorFormatFlags(formatArgs)
}

func validateErrorFormatFlags(args []string) error {
	_, _, err := parseOutputFormatArgs(args, 0)
	return err
}

func parseOperationRunArgs(args []string) (map[string]any, outputFormat, error) {
	var argumentFlags []string
	var outputFlags []string
	for i := 0; i < len(args); i++ {
		if args[i] == "--args" {
			argumentFlags = append(argumentFlags, args[i])
			if i+1 < len(args) {
				i++
				argumentFlags = append(argumentFlags, args[i])
			}
		} else {
			outputFlags = append(outputFlags, args[i])
		}
	}
	_, format, err := parseOutputFormatArgs(outputFlags, 0)
	if err != nil {
		return nil, format, err
	}
	arguments, err := parseArgsFlag(argumentFlags)
	return arguments, format, err
}
