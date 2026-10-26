package identity

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"os"
	"time"
)

const receiptRequestTag = "arbiter-enrollment-receipt-request-v1\x00"
const receiptProofTag = "arbiter-enrollment-receipt-possession-v1\x00"

// EnrollmentReceipt proves the exact committed result over current remembered
// server trust. Only this authenticated result can be durably promoted.
type EnrollmentReceipt struct {
	prepared *PreparedEnrollment
	material *Provisional
}

// ReconcileEnrollment needs no pairing secret. Keep prepared keys on transport
// failures: a lost reply does not prove that the original commit failed.
func ReconcileEnrollment(ctx context.Context, directory, selector string, timeout time.Duration) (*EnrollmentReceipt, error) {
	material, prepared, err := loadPrepared(directory, selector, time.Now().UTC(), true)
	if err != nil {
		return nil, err
	}
	const fixed = len(preparedTag) + 144 + 22 + 32 + 32 + 8
	fields := prepared.raw[fixed:]
	authority, err := preparedField(&fields)
	if err != nil {
		return nil, errControl
	}
	stream, err := DialControl(ctx, material.record.ServerURL,
		PairingServer{ServerID: prepared.ServerID, AuthorityDER: authority}, timeout)
	if err != nil {
		return nil, err
	}
	defer stream.Close()
	request, err := receiptRequest(material, prepared, stream.Exporter())
	if err != nil {
		return nil, errControl
	}
	reply, err := stream.Exchange(request)
	if err != nil || !bytes.Equal(reply, prepared.raw) {
		return nil, errControl
	}
	return &EnrollmentReceipt{prepared: prepared, material: material}, nil
}

// Promote publishes complete enrolled custody before the caller removes any
// prepared state. An ambiguous filesystem failure retains the recovery source.
// Repeated publication is successful only for the same exact private result.
// The caller must hold the same enrollment owner used by RunProvisional.
func (r *EnrollmentReceipt) Promote(ctx context.Context, directory string) error {
	if r == nil || r.prepared == nil || r.material == nil {
		return errCustody
	}
	private, err := r.material.EncodePrivate()
	if err != nil {
		return err
	}
	bundle, err := json.Marshal(preparedPrivateRecord{privateWarning, 1, private, r.prepared.raw})
	if err != nil {
		return err
	}
	if err := savePrivateRecord(ctx, directory, r.material.record.Selector, bundle, func(string) error { return nil }); err != nil {
		if !errors.Is(err, os.ErrExist) {
			return err
		}
		root, dir, openErr := custodyRoot(directory, false)
		if openErr != nil {
			return openErr
		}
		dir.Close()
		final, pending, _ := custodyNames(root, r.material.record.Selector)
		links := uint32(1)
		a, ae := os.Lstat(final)
		b, be := os.Lstat(pending)
		if errors.Is(ae, os.ErrNotExist) {
			// A killed writer can leave only staging. The authenticated source
			// is intact; discard only this selector's private single-link file.
			if err := ctx.Err(); err != nil {
				return err
			}
			if err := removeCustodyFile(pending, 1); err != nil {
				return err
			}
			return savePrivateRecord(ctx, root, r.material.record.Selector, bundle, func(string) error { return nil })
		}
		if ae != nil {
			return ae
		}
		if ae == nil && be == nil && os.SameFile(a, b) {
			links = 2
		}
		material, prepared, loadErr := loadPreparedLinked(root, r.material.record.Selector, time.Now().UTC(), true, links)
		if loadErr != nil || !bytes.Equal(prepared.raw, r.prepared.raw) {
			return errCustody
		}
		existing, loadErr := material.EncodePrivate()
		if loadErr != nil || !bytes.Equal(existing, private) {
			return errCustody
		}
		if links == 2 {
			if err := removeCustodyFile(pending, 2); err != nil {
				return err
			}
		}
		// savePrivateRecord includes the durable-directory sync in ErrExist;
		// repeat it explicitly so joined sync failures cannot become success.
		return nativeSyncCustody(directory)
	}
	return nil
}

func receiptRequest(material *Provisional, prepared *PreparedEnrollment, exporter []byte) ([]byte, error) {
	if material == nil || material.access == nil || prepared == nil || len(exporter) != 32 {
		return nil, errControl
	}
	request := []byte(receiptRequestTag + prepared.ServerID + prepared.EnrollmentID)
	digest := sha256.Sum256(prepared.raw)
	request = append(request, digest[:]...)
	var nonce [32]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return nil, err
	}
	request = append(request, nonce[:]...)
	h := sha256.New()
	h.Write([]byte(receiptProofTag))
	h.Write(exporter)
	h.Write(request)
	signature, err := proofSignature(material.access, h.Sum(nil), "receipt")
	if err != nil {
		return nil, err
	}
	return append(request, signature...), nil
}
