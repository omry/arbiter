// Isolated interoperability probe, not a production enrollment service.
// stdin is the trusted test-control channel, NOT the simulated network.
package main

import (
	"bufio"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os"

	"github.com/flynn/noise"
)

type command struct {
	Op        string `json:"op"`
	Data      []byte `json:"data"`
	PSK       []byte `json:"psk"`
	Prologue  []byte `json:"prologue"`
	Initiator bool   `json:"initiator"`
}

type result struct {
	OK       bool   `json:"ok"`
	Data     []byte `json:"data,omitempty"`
	Hash     []byte `json:"hash,omitempty"`
	SendHash string `json:"send_key_hash,omitempty"`
	RecvHash string `json:"recv_key_hash,omitempty"`
	Error    string `json:"error,omitempty"`
}

type peer struct {
	hs        *noise.HandshakeState
	send      *noise.CipherState
	recv      *noise.CipherState
	initiator bool
}

func fingerprint(k [32]byte) string {
	h := sha256.Sum256(k[:])
	return hex.EncodeToString(h[:])
}

func (p *peer) split(c1, c2 *noise.CipherState) {
	if c1 == nil {
		return
	}
	// Noise Split returns initiator->responder, responder->initiator.
	if p.initiator {
		p.send, p.recv = c1, c2
	} else {
		p.send, p.recv = c2, c1
	}
}

func (p *peer) handle(c command) (r result, err error) {
	r.OK = true
	if c.Op == "init" {
		// Missing PSK must fail closed. flynn/noise otherwise selects plain NN.
		if len(c.PSK) != 32 {
			return r, errors.New("PSK must contain exactly 32 bytes")
		}
		p.initiator = c.Initiator
		p.send, p.recv = nil, nil
		p.hs, err = noise.NewHandshakeState(noise.Config{
			CipherSuite: noise.NewCipherSuite(noise.DH25519, noise.CipherChaChaPoly, noise.HashSHA256),
			Pattern:     noise.HandshakeNN, Initiator: c.Initiator,
			PresharedKey: c.PSK, PresharedKeyPlacement: 0, Prologue: c.Prologue,
		})
		return r, err
	}
	if p.hs == nil {
		return r, errors.New("not initialized")
	}
	var c1, c2 *noise.CipherState
	switch c.Op {
	case "write":
		r.Data, c1, c2, err = p.hs.WriteMessage(nil, c.Data)
		if err == nil {
			p.split(c1, c2)
		}
	case "read":
		r.Data, c1, c2, err = p.hs.ReadMessage(nil, c.Data)
		if err == nil {
			p.split(c1, c2)
		}
	case "encrypt":
		if p.send == nil {
			return r, errors.New("handshake incomplete")
		}
		r.Data, err = p.send.Encrypt(nil, nil, c.Data)
	case "decrypt":
		if p.recv == nil {
			return r, errors.New("handshake incomplete")
		}
		r.Data, err = p.recv.Decrypt(nil, nil, c.Data)
	case "state":
		if p.send == nil {
			return r, errors.New("handshake incomplete")
		}
		r.Hash = p.hs.ChannelBinding()
		// Test-only key fingerprints, never export actual keys.
		r.SendHash, r.RecvHash = fingerprint(p.send.UnsafeKey()), fingerprint(p.recv.UnsafeKey())
	default:
		err = errors.New("unknown operation")
	}
	return r, err
}

func main() {
	var p peer
	s := bufio.NewScanner(os.Stdin)
	s.Buffer(make([]byte, 4096), 256*1024)
	out := json.NewEncoder(os.Stdout)
	for s.Scan() {
		var c command
		var r result
		err := json.Unmarshal(s.Bytes(), &c)
		if err == nil {
			r, err = p.handle(c)
		}
		if err != nil {
			r = result{Error: err.Error()}
		}
		if err := out.Encode(r); err != nil {
			fmt.Fprintln(os.Stderr, err)
			os.Exit(1)
		}
	}
	if err := s.Err(); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}
