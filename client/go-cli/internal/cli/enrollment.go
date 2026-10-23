package cli

import (
	"context"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/omry/arbiter/client/go-cli/internal/identity"
)

func runEnrol(args []string, options globalOptions, stdout, stderr io.Writer,
	lookupEnv EnvLookup, homeDir HomeDirFunc,
) int {
	if len(args) == 1 && (args[0] == "--help" || args[0] == "-h") {
		fmt.Fprintln(stdout, "usage: arbiter enrol url=https://HOST name=NAME")
		fmt.Fprintln(stdout, "Requires an interactive terminal. Pairing proof only; enrollment activation is not available yet.")
		return 0
	}
	values := map[string]string{}
	for _, arg := range args {
		key, value, ok := strings.Cut(arg, "=")
		if !ok || (key != "url" && key != "name") || value == "" || values[key] != "" {
			printClientError(stderr, safeClientError("expected: arbiter enrol url=https://HOST name=NAME"), "usage")
			return 2
		}
		values[key] = value
	}
	if len(values) != 2 {
		printClientError(stderr, safeClientError("expected: arbiter enrol url=https://HOST name=NAME"), "usage")
		return 2
	}
	config, err := ResolveClientConfig(options.Overrides, lookupEnv, homeDir, options)
	if err != nil {
		printClientError(stderr, err, "enrollment")
		return 1
	}
	configPath, err := clientConfigPath(options, homeDir)
	if err != nil {
		printClientError(stderr, err, "enrollment")
		return 1
	}
	in, out, err := openEnrollmentTerminal()
	if err != nil {
		printClientError(stderr, safeClientError("enrollment requires an interactive terminal for deliberate secret handoff"), "enrollment")
		return 1
	}
	defer in.Close()
	defer out.Close()
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	if err := os.MkdirAll(filepath.Dir(configPath), 0700); err != nil {
		printClientError(stderr, err, "enrollment")
		return 1
	}
	err = identity.RunProvisional(ctx, filepath.Join(filepath.Dir(configPath), "provisional"), values["url"], values["name"],
		func(ctx context.Context, p *identity.Provisional, secret identity.PairingSecret) error {
			return identity.PairEnrollment(ctx, p, secret, config.ConnectTimeout, func(ctx context.Context, selector, psk string) error {
				return confirmEnrollment(ctx, in, out, selector, psk)
			})
		})
	if err != nil {
		if errors.Is(err, identity.ErrEnrollmentClosed) {
			err = safeClientError(identity.ErrEnrollmentClosed.Error())
		} else if errors.Is(err, identity.ErrEnrollmentUnavailable) {
			err = safeClientError(identity.ErrEnrollmentUnavailable.Error())
		}
		printClientError(stderr, err, "enrollment")
		return 1
	}
	fmt.Fprintln(stdout, "Pairing signing-key proofs verified. Enrollment is not active; prepared enrollment and activation are not available yet.")
	return 0
}
