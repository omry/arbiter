package identity

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/sha256"
	"time"
)

const receiptRequestTag = "arbiter-enrollment-receipt-request-v1\x00"
const receiptProofTag = "arbiter-enrollment-receipt-possession-v1\x00"

// EnrollmentReceipt proves retrieval of the exact committed result over current
// remembered server trust. It cannot by itself activate ordinary API access.
// Durable promotion is a separate operation, added with the activation writer.
type EnrollmentReceipt struct{ prepared *PreparedEnrollment }

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
	return &EnrollmentReceipt{prepared: prepared}, nil
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
