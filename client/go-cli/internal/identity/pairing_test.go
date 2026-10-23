package identity

import (
	"bytes"
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/flynn/noise"
)

func pairingFixture(t *testing.T) (*PairingInitiator, PairingSecret) {
	t.Helper()
	p, secret, err := NewProvisional("https://localhost", "Agent Smith", time.Now())
	if err != nil {
		t.Fatal(err)
	}
	initiator, err := NewPairingInitiator(p, secret)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(initiator.Close)
	return initiator, secret
}

func responderFixture(t *testing.T, prologue []byte, secret PairingSecret) *noise.HandshakeState {
	t.Helper()
	psk, err := secret.Bytes()
	if err != nil {
		t.Fatal(err)
	}
	hs, err := noise.NewHandshakeState(noise.Config{
		CipherSuite: noise.NewCipherSuite(noise.DH25519, noise.CipherChaChaPoly, noise.HashSHA256),
		Pattern:     noise.HandshakeNN, Initiator: false,
		PresharedKey: psk[:], PresharedKeyPlacement: 0, Prologue: prologue,
	})
	if err != nil {
		t.Fatal(err)
	}
	return hs
}

func pairedFixture(t *testing.T) (*PairingInitiator, *noise.CipherState, *noise.CipherState) {
	t.Helper()
	p, secret := pairingFixture(t)
	server := responderFixture(t, p.prologue, secret)
	first, err := p.FirstFlight()
	if err != nil {
		t.Fatal(err)
	}
	if _, _, _, err := server.ReadMessage(nil, first); err != nil {
		t.Fatal(err)
	}
	reply, receive, send, err := server.WriteMessage(nil, pairingPayload(p.prologue, "server"))
	if err != nil || p.Complete(reply) != nil {
		t.Fatal("handshake failed", err)
	}
	hash, err := p.HandshakeHash()
	if err != nil || len(hash) != 32 || !bytes.Equal(hash, server.ChannelBinding()) {
		t.Fatal("handshake bindings differ")
	}
	hash[0] ^= 1
	actual, _ := p.HandshakeHash()
	if bytes.Equal(actual, hash) {
		t.Fatal("mutable handshake binding escaped")
	}
	return p, send, receive
}

func TestPairingExactAdvertisementAndPrologue(t *testing.T) {
	p, _ := pairingFixture(t)
	raw := p.Advertisement()
	var public ProvisionalPublic
	if err := json.Unmarshal(raw, &public); err != nil {
		t.Fatal(err)
	}
	digest := sha256.Sum256(raw)
	expected := []byte("arbiter-enrollment-v1\x00client\x00server\x00" + public.Selector)
	expected = append(expected, digest[:]...)
	for _, key := range [][]byte{public.AccessKey, public.PortableKey, public.RecipientKey} {
		expected = append(expected, key...)
	}
	if !bytes.Equal(p.prologue, expected) {
		t.Fatal("request, selector or proposed keys omitted from prologue")
	}
	raw[0] ^= 1
	if bytes.Equal(p.Advertisement(), raw) {
		t.Fatal("mutable advertisement escaped")
	}
	if _, err := NewPairingInitiator(nil, PairingSecret{}); err == nil {
		t.Fatal("accepted absent key or secret")
	}
	_, valid := pairingFixture(t)
	if _, err := NewPairingInitiator(&Provisional{}, valid); err == nil {
		t.Fatal("accepted uninitialized keys")
	}
	for _, value := range []any{p, *p} {
		if _, err := json.Marshal(value); err == nil {
			t.Fatal("serialized private exchange")
		}
		for _, format := range []string{"%v", "%+v", "%#v"} {
			if got := fmt.Sprintf(format, value); got != "<private pairing exchange redacted>" {
				t.Fatal("exchange formatting did not redact internals")
			}
		}
	}
}

func TestPairingRejectsWrongPSKAndContext(t *testing.T) {
	for _, changed := range []string{"psk", "selector", "request", "access", "portable", "recipient"} {
		t.Run(changed, func(t *testing.T) {
			p, secret := pairingFixture(t)
			prologue := bytes.Clone(p.prologue)
			if changed == "psk" {
				secret.value[0] ^= 1
			} else {
				selector := len("arbiter-enrollment-v1\x00client\x00server\x00")
				access := selector + 22 + 32
				index := map[string]int{"selector": selector, "request": selector + 22, "access": access, "portable": access + 91, "recipient": access + 182}[changed]
				prologue[index] ^= 1
			}
			server := responderFixture(t, prologue, secret)
			wire, err := p.FirstFlight()
			if err != nil {
				t.Fatal(err)
			}
			if _, _, _, err := server.ReadMessage(nil, wire); err == nil {
				t.Fatal("substituted pairing context authenticated")
			}
			if _, err := p.HandshakeHash(); err == nil {
				t.Fatal("unconfirmed handshake exposed a binding")
			}
		})
	}
}

func TestPairingReplyRoleAndFailureAreTerminal(t *testing.T) {
	for _, bad := range []string{"client-role", "empty-role", "truncated", "corrupt", "empty", "oversized"} {
		t.Run(bad, func(t *testing.T) {
			p, secret := pairingFixture(t)
			server := responderFixture(t, p.prologue, secret)
			first, _ := p.FirstFlight()
			if _, _, _, err := server.ReadMessage(nil, first); err != nil {
				t.Fatal(err)
			}
			payload := pairingPayload(p.prologue, "server")
			if bad == "client-role" {
				payload = pairingPayload(p.prologue, "client")
			} else if bad == "empty-role" {
				payload = nil
			}
			reply, _, _, err := server.WriteMessage(nil, payload)
			if err != nil {
				t.Fatal(err)
			}
			switch bad {
			case "truncated":
				reply = reply[:len(reply)-1]
			case "corrupt":
				reply[len(reply)-1] ^= 1
			case "empty":
				reply = nil
			case "oversized":
				reply = make([]byte, MaxPairingFrame+1)
			}
			if p.Complete(reply) == nil || p.Complete(reply) == nil {
				t.Fatal("invalid reply accepted or revived failed exchange")
			}
			if _, err := p.HandshakeHash(); err == nil {
				t.Fatal("failed exchange exposed a binding")
			}
		})
	}
}

func TestPairingCipherDirectionsBoundariesAndReplay(t *testing.T) {
	p, send, receive := pairedFixture(t)
	plaintext := bytes.Repeat([]byte("a"), MaxPairingFrame-16)
	wire, err := p.Encrypt(plaintext)
	if err != nil || len(wire) != MaxPairingFrame {
		t.Fatal("exact frame boundary rejected")
	}
	actual, err := receive.Decrypt(nil, nil, wire)
	if err != nil || !bytes.Equal(actual, plaintext) {
		t.Fatal("wrong outbound cipher direction")
	}
	reply, err := send.Encrypt(nil, nil, plaintext)
	if err != nil {
		t.Fatal(err)
	}
	actual, err = p.Decrypt(reply)
	if err != nil || !bytes.Equal(actual, plaintext) {
		t.Fatal("wrong inbound cipher direction")
	}
	if _, err := p.Decrypt(reply); err == nil {
		t.Fatal("replayed ciphertext accepted")
	}
	fresh, _ := send.Encrypt(nil, nil, []byte("fresh"))
	if _, err := p.Decrypt(fresh); err == nil {
		t.Fatal("replay failure did not close the exchange")
	}
}

func TestPairingPerDirectionTotalBudget(t *testing.T) {
	for _, direction := range []string{"send", "receive"} {
		t.Run(direction, func(t *testing.T) {
			p, server, _ := pairedFixture(t)
			alias := *p
			owners := []*PairingInitiator{p, &alias}
			index := 0
			for {
				used := p.sent
				if direction == "receive" {
					used = p.read
				}
				remaining := MaxPairingBytes - used
				if remaining == 0 {
					break
				}
				size := min(remaining, MaxPairingFrame)
				plain := make([]byte, size-16)
				owner := owners[index%2]
				index++
				if direction == "send" {
					if _, err := owner.Encrypt(plain); err != nil {
						t.Fatal("exact cumulative boundary rejected", err)
					}
				} else {
					wire, _ := server.Encrypt(nil, nil, plain)
					if _, err := owner.Decrypt(wire); err != nil {
						t.Fatal("exact cumulative boundary rejected", err)
					}
				}
			}
			var err error
			if direction == "send" {
				_, err = p.Encrypt([]byte("x"))
			} else {
				wire, _ := server.Encrypt(nil, nil, []byte("x"))
				_, err = p.Decrypt(wire)
			}
			if err == nil {
				t.Fatal("cumulative byte budget exceeded")
			}
			if _, err := alias.HandshakeHash(); err == nil {
				t.Fatal("copied alias retained a failed owner's state")
			}
		})
	}
}

func TestPairingCopiedAliasesShareCloseAndCipherOwnership(t *testing.T) {
	p, _, _ := pairedFixture(t)
	alias := *p
	start := p.sent
	var owners sync.WaitGroup
	for range 16 {
		owners.Add(1)
		go func(value PairingInitiator) {
			defer owners.Done()
			if _, err := value.Encrypt([]byte("x")); err != nil {
				t.Error("copied exchange owner failed", err)
			}
		}(alias)
	}
	owners.Wait()
	if p.sent != start+16*17 {
		t.Fatal("copied aliases did not share one byte counter")
	}
	p.Close()
	if _, err := alias.Encrypt([]byte("x")); err == nil {
		t.Fatal("copied alias survived owner close")
	}
	if alias.Complete(nil) == nil {
		t.Fatal("copied alias revived closed handshake")
	}
	for _, empty := range []*PairingInitiator{nil, {}} {
		empty.Close()
		if empty.Advertisement() != nil || empty.Complete(nil) == nil {
			t.Fatal("uninitialized owner succeeded")
		}
		if _, err := empty.FirstFlight(); err == nil {
			t.Fatal("uninitialized first flight succeeded")
		}
		if _, err := empty.HandshakeHash(); err == nil {
			t.Fatal("uninitialized binding succeeded")
		}
		if _, err := empty.Encrypt([]byte("x")); err == nil {
			t.Fatal("uninitialized encryption succeeded")
		}
		if _, err := empty.Decrypt([]byte("x")); err == nil {
			t.Fatal("uninitialized decryption succeeded")
		}
	}
}

func TestPairingInvalidOrderAndSizes(t *testing.T) {
	for _, operation := range []string{"early-encrypt", "early-decrypt", "early-complete", "repeat-first", "close", "oversized-send", "oversized-receive", "empty-send", "short-receive"} {
		t.Run(operation, func(t *testing.T) {
			p, _ := pairingFixture(t)
			if strings.Contains(operation, "send") || strings.Contains(operation, "receive") {
				p, _, _ = pairedFixture(t)
			}
			var err error
			switch operation {
			case "early-encrypt":
				_, err = p.Encrypt([]byte("x"))
			case "early-decrypt":
				_, err = p.Decrypt(make([]byte, 17))
			case "early-complete":
				err = p.Complete([]byte("x"))
			case "repeat-first":
				p.FirstFlight()
				_, err = p.FirstFlight()
			case "close":
				p.Close()
				_, err = p.FirstFlight()
			case "oversized-send":
				_, err = p.Encrypt(make([]byte, MaxPairingFrame-15))
			case "oversized-receive":
				_, err = p.Decrypt(make([]byte, MaxPairingFrame+1))
			case "empty-send":
				_, err = p.Encrypt(nil)
			case "short-receive":
				_, err = p.Decrypt(make([]byte, 16))
			}
			if err == nil {
				t.Fatal("invalid operation succeeded")
			}
			if _, err := p.FirstFlight(); err == nil {
				t.Fatal("invalid operation left an exchange reusable")
			}
		})
	}
}
