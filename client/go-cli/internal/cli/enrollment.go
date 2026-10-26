package cli

import (
	"context"
	"encoding/base64"
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
		fmt.Fprintln(stdout, "usage: arbiter enrol url=https://HOST name=NAME | arbiter enrol resume=SELECTOR")
		fmt.Fprintln(stdout, "New enrollment requires an interactive terminal. Resume uses saved keys and remembered server trust, without a pairing secret.")
		return 0
	}
	values := map[string]string{}
	for _, arg := range args {
		key, value, ok := strings.Cut(arg, "=")
		if !ok || (key != "url" && key != "name" && key != "resume") || value == "" || values[key] != "" {
			printClientError(stderr, safeClientError("expected: arbiter enrol url=https://HOST name=NAME"), "usage")
			return 2
		}
		values[key] = value
	}
	resume := len(values) == 1 && values["resume"] != ""
	if resume {
		decoded, err := base64.RawURLEncoding.DecodeString(values["resume"])
		if err != nil || len(decoded) != 16 || base64.RawURLEncoding.EncodeToString(decoded) != values["resume"] {
			printClientError(stderr, safeClientError("expected a valid saved enrollment selector"), "usage")
			return 2
		}
	}
	if !resume && (len(values) != 2 || values["url"] == "" || values["name"] == "") {
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
	ctx, cancel := context.WithTimeout(context.Background(), 10*time.Minute)
	defer cancel()
	preparedDirectory := filepath.Join(filepath.Dir(configPath), "prepared")
	finish := func(selector string) (bool, error) {
		receipt, err := identity.ReconcileEnrollment(ctx, preparedDirectory, selector, config.ConnectTimeout)
		fromEnrolled := errors.Is(err, os.ErrNotExist)
		saved := !fromEnrolled
		if fromEnrolled {
			receipt, err = identity.ReconcileEnrollment(ctx, filepath.Join(filepath.Dir(configPath), "enrolled"), selector, config.ConnectTimeout)
			saved = !errors.Is(err, os.ErrNotExist)
		}
		if err == nil {
			err = receipt.Promote(ctx, filepath.Join(filepath.Dir(configPath), "enrolled"))
		}
		if err != nil {
			return saved, safeClientError("Enrollment is not confirmed. Saved private state is retained; retry arbiter enrol resume=" + selector + ". If the server did not commit, ask the operator and start a new enrollment.")
		}
		if !fromEnrolled {
			return true, identity.RemoveProvisional(preparedDirectory, selector)
		}
		return true, nil
	}
	if resume {
		err := identity.WithEnrollmentOwner(ctx, filepath.Join(filepath.Dir(configPath), "provisional"), func() error {
			_, err := finish(values["resume"])
			return err
		})
		if err != nil {
			printClientError(stderr, err, "enrollment")
			return 1
		}
		fmt.Fprintln(stdout, "Enrollment activated and saved. Ordinary authenticated API access is not available in this milestone.")
		return 0
	}
	in, out, err := openEnrollmentTerminal()
	if err != nil {
		printClientError(stderr, safeClientError("enrollment requires an interactive terminal for deliberate secret handoff"), "enrollment")
		return 1
	}
	defer in.Close()
	defer out.Close()
	if err := os.MkdirAll(filepath.Dir(configPath), 0700); err != nil {
		printClientError(stderr, err, "enrollment")
		return 1
	}
	err = identity.RunProvisional(ctx, filepath.Join(filepath.Dir(configPath), "provisional"), values["url"], values["name"],
		func(ctx context.Context, p *identity.Provisional, secret identity.PairingSecret) error {
			// RunProvisional holds the one live enrollment owner. A second
			// invocation cannot remove a result while the first sends ready.
			if err := identity.CleanupPrepared(ctx, preparedDirectory); err != nil {
				return err
			}
			pairErr := identity.PairEnrollment(ctx, p, secret, config.ConnectTimeout, preparedDirectory, func(ctx context.Context, selector, psk string) error {
				return confirmEnrollment(ctx, in, out, selector, psk)
			})
			public, _ := p.Public()
			saved, finishErr := finish(public.Selector)
			if finishErr != nil {
				if !saved {
					if pairErr == nil {
						return safeClientError("Prepared enrollment is unavailable; enrollment was not locally promoted.")
					}
					return pairErr
				}
				return finishErr
			}
			return nil
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
	fmt.Fprintln(stdout, "Enrollment activated and saved. Ordinary authenticated API access is not available in this milestone.")
	return 0
}
