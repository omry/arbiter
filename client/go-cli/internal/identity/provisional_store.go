package identity

import (
	"context"
	"encoding/base64"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

var errCustody = errors.New("unsafe or incomplete provisional state")

func custodyNames(root, selector string) (string, string, error) {
	raw, err := base64.RawURLEncoding.DecodeString(selector)
	if err != nil || len(raw) != 16 || base64.RawURLEncoding.EncodeToString(raw) != selector {
		return "", "", errCustody
	}
	return filepath.Join(root, selector+".json"), filepath.Join(root, "."+selector+".pending"), nil
}

func checkBoundCustody(f *os.File, directory, ancestor bool, links uint32) error {
	opened, err := f.Stat()
	if err != nil {
		return err
	}
	current, err := os.Lstat(f.Name())
	if err != nil || !os.SameFile(opened, current) {
		return errCustody
	}
	return nativeCheckCustody(f, directory, ancestor, links)
}

func custodyRoot(path string, create bool) (string, *os.File, error) {
	if path == "" {
		return "", nil, errCustody
	}
	absolute, err := filepath.Abs(path)
	if err != nil {
		return "", nil, err
	}
	// Resolve parent aliases once, including macOS /tmp. The final component
	// must itself be a real private directory, never an alias.
	parent, err := filepath.EvalSymlinks(filepath.Dir(absolute))
	if err != nil {
		return "", nil, err
	}
	root := filepath.Join(parent, filepath.Base(absolute))
	for path := parent; ; path = filepath.Dir(path) {
		f, err := nativeOpenCustody(path, true, false)
		if err != nil {
			return "", nil, err
		}
		err = checkBoundCustody(f, true, true, 0)
		f.Close()
		if err != nil {
			return "", nil, fmt.Errorf("unsafe private-state parent %q: %w", path, err)
		}
		if filepath.Dir(path) == path {
			break
		}
	}
	if create {
		if err := nativeMkdirCustody(root); err != nil && !errors.Is(err, os.ErrExist) {
			return "", nil, err
		}
	}
	f, err := nativeOpenCustody(root, true, false)
	if err == nil {
		err = checkBoundCustody(f, true, false, 0)
	}
	if err != nil {
		if f != nil {
			f.Close()
		}
		return "", nil, err
	}
	return root, f, nil
}

// SaveProvisional publishes a new immutable request record. It never replaces
// existing material or persists the pairing secret. Cancellation is checked
// before mutation and publication; kernel filesystem calls are synchronous.
func SaveProvisional(ctx context.Context, directory string, p *Provisional) error {
	return saveProvisional(ctx, directory, p, func(string) error { return nil })
}

func saveProvisional(ctx context.Context, directory string, p *Provisional, phase func(string) error) (result error) {
	if ctx == nil {
		return errCustody
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	data, err := p.EncodePrivate()
	if err != nil {
		return err
	}
	root, dir, err := custodyRoot(directory, true)
	if err != nil {
		return err
	}
	defer dir.Close()
	final, pending, err := custodyNames(root, p.record.Selector)
	if err != nil {
		return err
	}
	if _, err := os.Lstat(final); err == nil {
		return errors.Join(os.ErrExist, nativeSyncCustody(root))
	} else if !errors.Is(err, os.ErrNotExist) {
		return err
	}
	f, err := nativeOpenCustody(pending, false, true)
	if err != nil {
		return err
	}
	info, err := f.Stat()
	defer func() {
		f.Close()
		current, e := os.Lstat(pending)
		if errors.Is(e, os.ErrNotExist) {
			return
		}
		if e != nil || info == nil || !os.SameFile(info, current) {
			result = errors.Join(result, errCustody)
			return
		}
		// A crash during POSIX publication can leave two links. Leave that
		// detectable state for explicit removal; never delete another record.
		result = errors.Join(result, removeCustodyFile(pending, 1), nativeSyncCustody(root))
	}()
	if err != nil {
		return err
	}
	if err := checkBoundCustody(f, false, false, 1); err != nil {
		return err
	}
	if _, err := f.Write(data); err != nil {
		return err
	}
	if err := phase("written"); err != nil {
		return err
	}
	if err := f.Sync(); err != nil {
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if err := phase("flushed"); err != nil {
		return err
	}
	if err := ctx.Err(); err != nil {
		return err
	}
	if err := checkBoundCustody(dir, true, false, 0); err != nil {
		return err
	}
	if err := nativePublishCustody(pending, final); err != nil {
		return err
	}
	if err := phase("published"); err != nil {
		return err
	}
	return nativeSyncCustody(root)
}

func LoadProvisional(directory, selector string) (*Provisional, error) {
	root, dir, err := custodyRoot(directory, false)
	if err != nil {
		return nil, err
	}
	defer dir.Close()
	final, _, err := custodyNames(root, selector)
	if err != nil {
		return nil, err
	}
	f, err := nativeOpenCustody(final, false, false)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	if err := checkBoundCustody(f, false, false, 1); err != nil {
		return nil, err
	}
	raw, err := io.ReadAll(io.LimitReader(f, MaxProvisionalBytes+1))
	if err != nil {
		return nil, err
	}
	if err := checkBoundCustody(f, false, false, 1); err != nil {
		return nil, err
	}
	p, err := DecodeProvisional(raw)
	if err != nil || p.record.Selector != selector {
		return nil, errCustody
	}
	return p, nil
}

func removeCustodyFile(path string, links uint32) error {
	f, err := nativeOpenCustody(path, false, false)
	if errors.Is(err, os.ErrNotExist) {
		return nil
	}
	if err != nil {
		return err
	}
	err = checkBoundCustody(f, false, false, links)
	f.Close()
	if err != nil {
		return err
	}
	return os.Remove(path)
}

// RemoveProvisional explicitly discards one request, including torn staging.
// It does not claim secure deletion or remove any enrolled identity state.
func RemoveProvisional(directory, selector string) error {
	root, dir, err := custodyRoot(directory, false)
	if err != nil {
		return err
	}
	defer dir.Close()
	final, pending, err := custodyNames(root, selector)
	if err != nil {
		return err
	}
	links := uint32(1)
	a, ae := os.Lstat(final)
	b, be := os.Lstat(pending)
	if ae == nil && be == nil && os.SameFile(a, b) {
		links = 2 // Only the two exact private names may alias during publication.
	}
	if err := removeCustodyFile(pending, links); err != nil {
		return err
	}
	if err := removeCustodyFile(final, 1); err != nil {
		return err
	}
	return nativeSyncCustody(root)
}
