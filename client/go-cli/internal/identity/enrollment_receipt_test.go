package identity

import (
	"bytes"
	"context"
	"crypto/ecdsa"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"io"
	"math/big"
	"net"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestReceiptProofBindsPurposeResultAndFreshChannel(t *testing.T) {
	material, _, err := NewProvisional("https://localhost", "test", time.Now())
	if err != nil {
		t.Fatal(err)
	}
	prepared := &PreparedEnrollment{ServerID: "11111111-1111-4111-8111-111111111111", EnrollmentID: "22222222-2222-4222-8222-222222222222", raw: []byte("complete exact result")}
	exporter := bytes.Repeat([]byte{1}, 32)
	first, err := receiptRequest(material, prepared, exporter)
	if err != nil || len(first) != len(receiptRequestTag)+200 {
		t.Fatal(err)
	}
	second, _ := receiptRequest(material, prepared, exporter)
	if bytes.Equal(first, second) {
		t.Fatal("nonce reused")
	}
	for _, mode := range []string{"ok", "purpose", "channel", "result"} {
		request, binding := bytes.Clone(first), bytes.Clone(exporter)
		switch mode {
		case "purpose":
			request[0] ^= 1
		case "channel":
			binding[0] ^= 1
		case "result":
			request[len(receiptRequestTag)+72] ^= 1
		}
		h := sha256.New()
		h.Write([]byte(receiptProofTag))
		h.Write(binding)
		h.Write(request[:len(request)-64])
		d := proofDigest(h.Sum(nil), "receipt")
		sig := request[len(request)-64:]
		valid := ecdsa.Verify(&material.access.PublicKey, d[:], new(big.Int).SetBytes(sig[:32]), new(big.Int).SetBytes(sig[32:]))
		if valid != (mode == "ok") {
			t.Fatal("proof binding", mode)
		}
	}
}

// A trusted loopback fixture creates synthetic committed rows only. Product
// reconciliation uses real Python/Go TLS and no pairing secret or private handoff.
func TestPythonReceiptPeer(t *testing.T) {
	endpoint := os.Getenv("ARBITER_TEST_RECEIPT_CONTROL")
	if endpoint == "" {
		t.Skip("Python receipt peer is not running")
	}
	host, _, err := net.SplitHostPort(endpoint)
	if err != nil || host != "127.0.0.1" {
		t.Fatal("loopback required")
	}
	conn, err := net.DialTimeout("tcp", endpoint, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close()
	conn.SetDeadline(time.Now().Add(20 * time.Second))
	material, _, err := NewProvisional("https://localhost", "Agent Smith", time.Now())
	if err != nil {
		t.Fatal(err)
	}
	public, err := material.Public()
	if err != nil {
		t.Fatal(err)
	}
	if json.NewEncoder(conn).Encode(public) != nil {
		t.Fatal("fixture advertisement")
	}
	var fixture struct {
		URL, Mode string
		Result    []byte
	}
	if json.NewDecoder(io.LimitReader(conn, 131072)).Decode(&fixture) != nil {
		t.Fatal("fixture result")
	}
	material.record.ServerURL = fixture.URL
	private, err := material.EncodePrivate()
	if err != nil {
		t.Fatal(err)
	}
	bundle, err := json.Marshal(preparedPrivateRecord{privateWarning, 1, private, fixture.Result})
	if err != nil {
		t.Fatal(err)
	}
	directory := filepath.Join(custodyTestDir(t), "prepared")
	if savePrivateRecord(context.Background(), directory, material.record.Selector, bundle, func(string) error { return nil }) != nil {
		t.Fatal("fixture custody")
	}
	if fixture.Mode == "replay" {
		p, prepared, err := loadPrepared(directory, material.record.Selector, time.Now(), true)
		if err != nil {
			t.Fatal(err)
		}
		fields := prepared.raw[len(preparedTag)+144+22+32+32+8:]
		authority, _ := preparedField(&fields)
		trust := PairingServer{ServerID: prepared.ServerID, AuthorityDER: authority}
		stream, err := DialControl(context.Background(), fixture.URL, trust, 5*time.Second)
		if err != nil {
			t.Fatal(err)
		}
		request, _ := receiptRequest(p, prepared, stream.Exporter())
		if _, err = stream.Exchange(request); err != nil {
			t.Fatal(err)
		}
		stream, err = DialControl(context.Background(), fixture.URL, trust, 5*time.Second)
		if err != nil {
			t.Fatal(err)
		}
		if _, err = stream.Exchange(request); err == nil {
			t.Fatal("replayed proof accepted")
		}
	}
	if fixture.Mode == "lost-reply" {
		if _, err := ReconcileEnrollment(context.Background(), directory, material.record.Selector, 5*time.Second); err == nil {
			t.Fatal("lost reply succeeded")
		}
	}
	receipt, err := ReconcileEnrollment(context.Background(), directory, material.record.Selector, 5*time.Second)
	want := fixture.Mode == "ok" || fixture.Mode == "lost-reply" || fixture.Mode == "replay" || fixture.Mode == "expired-prepare"
	if (err == nil) != want || want && (receipt == nil || !bytes.Equal(receipt.prepared.raw, fixture.Result)) {
		t.Fatal("receipt result", fixture.Mode, err)
	}
	after, err := os.ReadFile(filepath.Join(directory, material.record.Selector+".json"))
	if err != nil || !bytes.Equal(after, bundle) {
		t.Fatal("prepared material changed")
	}
	fmt.Println("ARBITER_RECEIPT_PEER=pass")
}
