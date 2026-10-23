package identity

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"os"
	"testing"
	"time"
)

// The loopback socket is a trusted synthetic test-control channel, not the
// product carrier. Its ephemeral PSK is test input and never printed or saved.
func TestPythonPairingPeer(t *testing.T) {
	endpoint := os.Getenv("ARBITER_TEST_PAIRING_CONTROL")
	if endpoint == "" {
		t.Skip("Python pairing peer is not running")
	}
	host, _, err := net.SplitHostPort(endpoint)
	if err != nil || host != "127.0.0.1" {
		t.Fatal("pairing test control must use loopback")
	}
	conn, err := net.DialTimeout("tcp", endpoint, 5*time.Second)
	if err != nil {
		t.Fatal("pairing control unavailable")
	}
	defer conn.Close()
	if err := conn.SetDeadline(time.Now().Add(10 * time.Second)); err != nil {
		t.Fatal(err)
	}
	material, secret, err := NewProvisional("https://localhost", "Agent Smith", time.Now())
	if err != nil {
		t.Fatal(err)
	}
	p, err := NewPairingInitiator(material, secret)
	if err != nil {
		t.Fatal(err)
	}
	defer p.Close()
	first, err := p.FirstFlight()
	if err != nil {
		t.Fatal(err)
	}
	output := json.NewEncoder(conn)
	input := json.NewDecoder(io.LimitReader(conn, 32*1024))
	request := struct {
		Advertisement []byte `json:"advertisement"`
		First         []byte `json:"first"`
		Secret        string `json:"secret"`
	}{p.Advertisement(), first, secret.Encoded()}
	if output.Encode(request) != nil {
		t.Fatal("test-control request failed")
	}
	var reply struct {
		Rejected bool   `json:"rejected"`
		Response []byte `json:"response"`
		Hash     []byte `json:"hash"`
		Message  []byte `json:"message"`
		Proof    []byte `json:"proof"`
	}
	if input.Decode(&reply) != nil {
		t.Fatal("test-control response failed")
	}
	if os.Getenv("ARBITER_TEST_PAIRING_REJECT") == "1" {
		if !reply.Rejected || p.Complete(reply.Response) == nil {
			t.Fatal("invalid Python pairing context authenticated")
		}
		fmt.Println("ARBITER_PAIRING_PEER=pass")
		return
	}
	if reply.Rejected || p.Complete(reply.Response) != nil {
		t.Fatal("Python pairing response rejected")
	}
	hash, err := p.HandshakeHash()
	if err != nil || !bytes.Equal(hash, reply.Hash) {
		t.Fatal("Python/Go channel bindings differ")
	}
	message, err := p.Decrypt(reply.Message)
	if err != nil || string(message) != "Python server payload" {
		t.Fatal("Python transport cipher rejected")
	}
	wire, err := p.Encrypt([]byte("Go client payload"))
	if err != nil {
		t.Fatal(err)
	}
	proof, trust, err := p.ProveClient(reply.Proof, material, time.Now())
	if os.Getenv("ARBITER_TEST_PAIRING_PROOF_REJECT") == "1" {
		if err == nil || trust != nil {
			t.Fatal("accepted forged server possession proof")
		}
		if output.Encode(struct {
			ProofRejected bool `json:"proof_rejected"`
		}{true}) != nil {
			t.Fatal("proof rejection response failed")
		}
	} else if err != nil || trust == nil || output.Encode(struct {
		Message  []byte `json:"message"`
		Proof    []byte `json:"proof"`
		ServerID string `json:"server_id"`
	}{wire, proof, trust.ServerID}) != nil {
		t.Fatal("Go mutual possession proof failed")
	}
	var result struct{ OK bool }
	if input.Decode(&result) != nil || !result.OK {
		t.Fatal("Python could not authenticate Go ciphertext")
	}
	fmt.Println("ARBITER_PAIRING_PEER=pass")
}
