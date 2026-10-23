package identity

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"sync"

	"github.com/flynn/noise"
)

const MaxPairingFrame = 65535
const MaxPairingBytes = 256 * 1024

var errPairing = errors.New("pairing exchange is unavailable or invalid; start again")

// PairingInitiator refers to one Noise exchange owner, shared by all copies. It authenticates
// the shared secret and request context, not signing-key possession or durable
// server identity. The carrier must enforce its own absolute deadline and bounds.
type PairingInitiator struct {
	*pairingState
}

type pairingState struct {
	mu            sync.Mutex
	advertisement []byte
	prologue      []byte
	handshake     *noise.HandshakeState
	send, receive *noise.CipherState
	hash          []byte
	phase         int
	sent, read    int
}

func (PairingInitiator) String() string     { return "<private pairing exchange redacted>" }
func (p PairingInitiator) GoString() string { return p.String() }
func (PairingInitiator) MarshalJSON() ([]byte, error) {
	return nil, errors.New("pairing exchanges cannot be serialized")
}

func NewPairingInitiator(p *Provisional, secret PairingSecret) (*PairingInitiator, error) {
	psk, err := secret.Bytes()
	if err != nil {
		return nil, errPairing
	}
	public, err := p.Public()
	if err != nil {
		return nil, errPairing
	}
	raw, err := json.Marshal(public)
	if err != nil {
		return nil, errPairing
	}
	digest := sha256.Sum256(raw)
	prologue := append([]byte("arbiter-enrollment-v1\x00client\x00server\x00"), public.Selector...)
	prologue = append(prologue, digest[:]...)
	prologue = append(prologue, public.AccessKey...)
	prologue = append(prologue, public.PortableKey...)
	prologue = append(prologue, public.RecipientKey...)
	hs, err := noise.NewHandshakeState(noise.Config{
		CipherSuite: noise.NewCipherSuite(noise.DH25519, noise.CipherChaChaPoly, noise.HashSHA256),
		Pattern:     noise.HandshakeNN, Initiator: true,
		PresharedKey: psk[:], PresharedKeyPlacement: 0, Prologue: prologue,
	})
	if err != nil {
		return nil, errPairing
	}
	return &PairingInitiator{&pairingState{advertisement: raw, prologue: prologue, handshake: hs}}, nil
}

// Advertisement returns the exact public bytes bound by the prologue. The
// caller must register these bytes unchanged, not re-encode the JSON.
func (p *PairingInitiator) Advertisement() []byte {
	if p == nil || p.pairingState == nil {
		return nil
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	return bytes.Clone(p.advertisement)
}

func pairingPayload(prologue []byte, role string) []byte {
	digest := sha256.Sum256(prologue)
	return append([]byte("arbiter-enrollment-v1\x00"+role+"\x00"), digest[:]...)
}

func (p *PairingInitiator) fail() error {
	p.phase = 3
	p.handshake, p.send, p.receive, p.hash = nil, nil, nil, nil
	return errPairing
}

// Close prevents any later use. It does not promise erasure of runtime copies.
func (p *PairingInitiator) Close() {
	if p == nil || p.pairingState == nil {
		return
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	p.fail()
}

func (p *PairingInitiator) FirstFlight() ([]byte, error) {
	if p == nil || p.pairingState == nil {
		return nil, errPairing
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.phase != 0 || p.handshake == nil {
		return nil, p.fail()
	}
	wire, _, _, err := p.handshake.WriteMessage(nil, pairingPayload(p.prologue, "client"))
	if err != nil || len(wire) == 0 || len(wire) > MaxPairingFrame {
		return nil, p.fail()
	}
	p.sent, p.phase = len(wire), 1
	return wire, nil
}

func (p *PairingInitiator) Complete(response []byte) error {
	if p == nil || p.pairingState == nil {
		return errPairing
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.phase != 1 || len(response) == 0 || len(response) > MaxPairingFrame {
		return p.fail()
	}
	payload, send, receive, err := p.handshake.ReadMessage(nil, response)
	if err != nil || send == nil || receive == nil || !bytes.Equal(payload, pairingPayload(p.prologue, "server")) {
		return p.fail()
	}
	p.send, p.receive = send, receive
	p.hash = bytes.Clone(p.handshake.ChannelBinding())
	p.handshake = nil
	p.read, p.phase = len(response), 2
	return nil
}

// HandshakeHash is available only after authenticating the server's role-tagged
// reply. Later signing-key proofs must bind this value and the request context.
func (p *PairingInitiator) HandshakeHash() ([]byte, error) {
	if p == nil || p.pairingState == nil {
		return nil, errPairing
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.phase != 2 {
		return nil, errPairing
	}
	return bytes.Clone(p.hash), nil
}

func (p *PairingInitiator) Encrypt(plaintext []byte) ([]byte, error) {
	if p == nil || p.pairingState == nil {
		return nil, errPairing
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.phase != 2 || len(plaintext) == 0 || len(plaintext) > MaxPairingFrame-16 || p.sent+len(plaintext)+16 > MaxPairingBytes {
		return nil, p.fail()
	}
	wire, err := p.send.Encrypt(nil, nil, plaintext)
	if err != nil {
		return nil, p.fail()
	}
	p.sent += len(wire)
	return wire, nil
}

func (p *PairingInitiator) Decrypt(wire []byte) ([]byte, error) {
	if p == nil || p.pairingState == nil {
		return nil, errPairing
	}
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.phase != 2 || len(wire) < 17 || len(wire) > MaxPairingFrame || p.read+len(wire) > MaxPairingBytes {
		return nil, p.fail()
	}
	plaintext, err := p.receive.Decrypt(nil, nil, wire)
	if err != nil {
		return nil, p.fail()
	}
	p.read += len(wire)
	return plaintext, nil
}
