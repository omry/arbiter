package identity

import (
	"context"
	"errors"
	"io"
	"os"
	"path/filepath"
	"strings"
	"time"
)

var ErrProvisionalBusy = errors.New("another enrollment owns this provisional directory")
var ErrProvisionalCleanupIncomplete = errors.New("provisional cleanup batch completed; retry cleanup before enrollment")
var ErrProvisionalUnexpectedContents = errors.New("provisional cleanup made no progress; move unrelated files out of the provisional directory before retrying")

const provisionalLockName = ".enrollment.lock"
const provisionalCleanupBatch = 64

// lockProvisional holds native ownership until the actual consumer exits. The
// empty lock file persists: unlinking it would allow two independent lock owners.
func lockProvisional(ctx context.Context, directory string) (string, *os.File, error) {
	if ctx == nil {
		return "", nil, errCustody
	}
	if err := ctx.Err(); err != nil {
		return "", nil, err
	}
	root, dir, err := custodyRoot(directory, true)
	if err != nil {
		return "", nil, err
	}
	defer dir.Close()
	path := filepath.Join(root, provisionalLockName)
	f, err := nativeOpenCustody(path, false, true)
	if errors.Is(err, os.ErrExist) {
		f, err = nativeOpenCustody(path, false, false)
	}
	if err != nil {
		if nativeProvisionalBusy(err) {
			return "", nil, ErrProvisionalBusy
		}
		return "", nil, err
	}
	if err = checkBoundCustody(f, false, false, 1); err == nil {
		err = nativeLockProvisional(f)
	}
	if err == nil {
		err = checkBoundCustody(dir, true, false, 0)
	}
	if err != nil {
		f.Close()
		if nativeProvisionalBusy(err) {
			err = ErrProvisionalBusy
		}
		return "", nil, err
	}
	return root, f, nil
}

// cleanupProvisional removes only recognized provisional names while ownership
// is held. Complete records must still decode as this provisional-only format;
// prepared/enrolled state is never interpreted as an abandoned request.
func cleanupProvisional(ctx context.Context, root string) error {
	dir, err := os.Open(root)
	if err != nil {
		return err
	}
	defer dir.Close()
	if err := checkBoundCustody(dir, true, false, 0); err != nil {
		return err
	}
	names, err := dir.Readdirnames(provisionalCleanupBatch + 1)
	if err != nil && !errors.Is(err, io.EOF) {
		return err
	}
	more := len(names) > provisionalCleanupBatch
	if more {
		names = names[:provisionalCleanupBatch]
	}
	return cleanupProvisionalBatch(ctx, root, names, more)
}

func cleanupProvisionalBatch(ctx context.Context, root string, names []string, more bool) error {
	removed := false
	for _, name := range names {
		if err := ctx.Err(); err != nil {
			return err
		}
		var selector string
		switch {
		case strings.HasSuffix(name, ".json"):
			selector = strings.TrimSuffix(name, ".json")
		case strings.HasPrefix(name, ".") && strings.HasSuffix(name, ".pending"):
			selector = strings.TrimSuffix(strings.TrimPrefix(name, "."), ".pending")
		default:
			continue
		}
		final, pending, err := custodyNames(root, selector)
		if err != nil {
			continue // Unknown files belong to the caller, never to this cleanup.
		}
		links := uint32(1)
		a, ae := os.Lstat(final)
		b, be := os.Lstat(pending)
		if ae == nil && be == nil && os.SameFile(a, b) {
			links = 2 // Interrupted POSIX no-replace publication.
		}
		if ae == nil {
			if _, err := loadProvisional(root, selector, links); err != nil {
				return err
			}
		} else if !errors.Is(ae, os.ErrNotExist) {
			return ae
		}
		if err := RemoveProvisional(root, selector); err != nil {
			return err
		}
		removed = removed || ae == nil || be == nil
	}
	if more {
		if !removed {
			return ErrProvisionalUnexpectedContents
		}
		return ErrProvisionalCleanupIncomplete
	}
	return nil
}

// CleanupProvisional performs one bounded startup/restart cleanup batch. Callers
// use a dedicated provisional directory, separate from prepared/enrolled state.
// Unknown files are preserved; unsafe or unrecognized complete records fail closed.
func CleanupProvisional(ctx context.Context, directory string) (result error) {
	root, lock, err := lockProvisional(ctx, directory)
	if err != nil {
		return err
	}
	defer func() { result = errors.Join(result, lock.Close()) }()
	return cleanupProvisional(ctx, root)
}

// RunProvisional saves fresh keys before exposing anything to exchange. It
// cleans abandoned files first and removes this request when exchange returns.
// Exchange must observe cancellation; ownership remains until it actually ends.
// Any prepared/activated state must be persisted separately before returning.
func RunProvisional(ctx context.Context, directory, serverURL, name string,
	exchange func(context.Context, *Provisional, PairingSecret) error,
) error {
	return runProvisional(ctx, directory, serverURL, name, 10*time.Minute, exchange)
}

func runProvisional(ctx context.Context, directory, serverURL, name string, lifetime time.Duration,
	exchange func(context.Context, *Provisional, PairingSecret) error,
) (result error) {
	if exchange == nil {
		return errCustody
	}
	root, lock, err := lockProvisional(ctx, directory)
	if err != nil {
		return err
	}
	defer func() { result = errors.Join(result, lock.Close()) }()
	if err := cleanupProvisional(ctx, root); err != nil {
		return err
	}
	now := time.Now().UTC()
	p, secret, err := NewProvisional(serverURL, name, now)
	if err != nil {
		return err
	}
	ctx, cancel := context.WithDeadline(ctx, now.Add(lifetime))
	defer cancel()
	if err := SaveProvisional(ctx, root, p); err != nil {
		// A failed publication can leave recoverable state. Never advertise it
		// or treat an existing-record error (possibly joined with fsync) as success.
		return err
	}
	defer func() { result = errors.Join(result, RemoveProvisional(root, p.record.Selector)) }()
	if err := ctx.Err(); err != nil {
		return err
	}
	result = exchange(ctx, p, secret)
	return errors.Join(result, ctx.Err())
}
