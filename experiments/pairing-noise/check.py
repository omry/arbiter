"""Run the isolated Python/Go Noise probe. No network listener or real credentials."""

import base64
from contextlib import contextmanager
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
import platform
from queue import Empty, Queue
import secrets
import subprocess
import sys
from threading import Thread
import unittest

from cryptography.exceptions import InvalidTag
from noise.connection import NoiseConnection
from noise.exceptions import NoiseInvalidMessage, NoisePSKError, NoiseValueError

ROOT = Path(__file__).resolve().parent
OUTPUT = Path(os.environ.get("PAIRING_POC_OUTPUT", ROOT / "results"))
PEER = ROOT / "build" / ("peer.exe" if os.name == "nt" else "peer")
PROFILE = b"Noise_NNpsk0_25519_ChaChaPoly_SHA256"


def b64(data):
    return base64.b64encode(data).decode("ascii")


class Rejected(Exception):
    pass


class GoPeer:
    def __init__(self, initiator, psk, prologue):
        self.p = subprocess.Popen(
            [str(PEER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        # Windows select() cannot poll subprocess pipes. A reader thread gives
        # both platforms the same bounded response wait and EOF handling.
        self.responses = Queue()
        self.reader = Thread(target=self._read_responses, daemon=True)
        self.reader.start()
        try:
            self.call("init", initiator=initiator, psk=psk, prologue=prologue)
        except BaseException:
            self.close()
            raise

    def _read_responses(self):
        try:
            for line in self.p.stdout:
                self.responses.put(line)
        finally:
            self.responses.put(None)

    def call(self, op, **kwargs):
        req = {
            "op": op,
            **{k: b64(v) if isinstance(v, bytes) else v for k, v in kwargs.items()},
        }
        self.p.stdin.write(json.dumps(req) + "\n")
        self.p.stdin.flush()
        try:
            line = self.responses.get(timeout=5)
        except Empty:
            raise RuntimeError("Go peer response timed out") from None
        if not line:
            raise RuntimeError("Go peer exited unexpectedly")
        response = json.loads(line)
        if not response["ok"]:
            raise Rejected(response.get("error"))
        return response

    def write(self, data=b""):
        return base64.b64decode(self.call("write", data=data).get("data", ""))

    def read(self, data):
        return base64.b64decode(self.call("read", data=data).get("data", ""))

    def encrypt(self, data):
        return base64.b64decode(self.call("encrypt", data=data).get("data", ""))

    def decrypt(self, data):
        return base64.b64decode(self.call("decrypt", data=data).get("data", ""))

    def state(self):
        r = self.call("state")
        return base64.b64decode(r["hash"]), r["send_key_hash"], r["recv_key_hash"]

    def close(self):
        try:
            self.p.stdin.close()
        except BrokenPipeError:
            pass
        if self.p.poll() is None:
            try:
                self.p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.p.kill()
                self.p.wait()
        self.reader.join(timeout=5)
        self.p.stdout.close()
        self.p.stderr.close()


class PythonPeer:
    def __init__(self, initiator, psk, prologue):
        # Explicit validation prevents any missing-PSK downgrade in wrappers.
        if len(psk) != 32:
            raise Rejected("PSK must contain exactly 32 bytes")
        self.n = NoiseConnection.from_name(PROFILE)
        if initiator:
            self.n.set_as_initiator()
        else:
            self.n.set_as_responder()
        self.n.set_psks(psk=psk)
        self.n.set_prologue(prologue)
        self.n.start_handshake()

    def write(self, data=b""):
        return bytes(self.n.write_message(data))

    def read(self, data):
        return bytes(self.n.read_message(data))

    def encrypt(self, data):
        return self.n.encrypt(data)

    def decrypt(self, data):
        return self.n.decrypt(data)

    def state(self):
        p = self.n.noise_protocol
        # Test-only inspection of the candidate's internal keys. No key is logged.
        return (
            self.n.get_handshake_hash(),
            hashlib.sha256(p.cipher_state_encrypt.k).hexdigest(),
            hashlib.sha256(p.cipher_state_decrypt.k).hexdigest(),
        )


def context():
    # Synthetic application binding only; not an adopted Arbiter wire schema.
    return (
        b"arbiter-pairing-poc-v1\0" + secrets.token_bytes(16) + secrets.token_bytes(32)
    )


@contextmanager
def pair(go_initiator, *, wrong_psk=False, wrong_context=False):
    psk, prologue = secrets.token_bytes(32), context()
    g = GoPeer(
        go_initiator,
        secrets.token_bytes(32) if wrong_psk else psk,
        prologue + b"changed" if wrong_context else prologue,
    )
    p = PythonPeer(not go_initiator, psk, prologue)
    try:
        yield (g, p, psk) if go_initiator else (p, g, psk)
    finally:
        g.close()


def handshake(i, r):
    # Synthetic durable public-key/identity material is carried in authenticated
    # payloads. Real identity certificate profiles/PoP remain separate design work.
    client = b"client-public-key:" + secrets.token_bytes(32)
    server = b"server-identity:" + secrets.token_bytes(32)
    first = i.write(client)
    if r.read(first) != client:
        raise AssertionError("client payload differs")
    second = r.write(server)
    if i.read(second) != server:
        raise AssertionError("server payload differs")
    return first, second


def flip(data, index=-1):
    value = bytearray(data)
    value[index] ^= 1
    return bytes(value)


REJECTION = (
    Rejected,
    InvalidTag,
    NoiseInvalidMessage,
    NoisePSKError,
    NoiseValueError,
    ValueError,
)


class Compatibility(unittest.TestCase):
    def test_correct_secret_keys_and_mutual_confirmation(self):
        for role in (False, True):
            with self.subTest(go_initiator=role), pair(role) as (i, r, psk):
                wire = list(handshake(i, r))
                ih, isend, irecv = i.state()
                rh, rsend, rrecv = r.state()
                self.assertEqual(ih, rh)
                self.assertEqual(isend, rrecv)
                self.assertEqual(irecv, rsend)
                self.assertNotEqual(isend, irecv)
                for sender, recipient, label in (
                    (i, r, b"client-confirm:"),
                    (r, i, b"server-confirm:"),
                ):
                    proof = label + ih
                    ciphertext = sender.encrypt(proof)
                    wire.append(ciphertext)
                    self.assertEqual(recipient.decrypt(ciphertext), proof)
                for message in wire:
                    self.assertNotIn(psk, message)
                    self.assertNotIn(
                        base64.urlsafe_b64encode(psk).rstrip(b"="), message
                    )

    def test_wrong_secret(self):
        for role in (False, True):
            with (
                self.subTest(go_initiator=role),
                pair(role, wrong_psk=True) as (i, r, _),
            ):
                with self.assertRaises(REJECTION):
                    r.read(i.write(b"client"))

    def test_wrong_request_context(self):
        for role in (False, True):
            with (
                self.subTest(go_initiator=role),
                pair(role, wrong_context=True) as (i, r, _),
            ):
                with self.assertRaises(REJECTION):
                    r.read(i.write(b"client"))

    def test_first_message_tampering(self):
        for role in (False, True):
            for index in (0, -1):
                with (
                    self.subTest(go_initiator=role, index=index),
                    pair(role) as (i, r, _),
                ):
                    with self.assertRaises(REJECTION):
                        r.read(flip(i.write(b"client-key"), index))

    def test_second_message_tampering(self):
        for role in (False, True):
            for index in (0, -1):
                with (
                    self.subTest(go_initiator=role, index=index),
                    pair(role) as (i, r, _),
                ):
                    r.read(i.write(b"client-key"))
                    with self.assertRaises(REJECTION):
                        i.read(flip(r.write(b"server-identity"), index))

    def test_malformed_first_message(self):
        for role in (False, True):
            for data in (b"", b"x", b"x" * 31, b"x" * 32, b"x" * 47, b"\0" * 48):
                with (
                    self.subTest(go_initiator=role, length=len(data)),
                    pair(role) as (_, r, _),
                ):
                    with self.assertRaises(REJECTION):
                        r.read(data)

    def test_malformed_second_message(self):
        for role in (False, True):
            for data in (b"", b"x", b"x" * 31, b"x" * 32, b"x" * 47, b"\0" * 48):
                with (
                    self.subTest(go_initiator=role, length=len(data)),
                    pair(role) as (i, r, _),
                ):
                    r.read(i.write())
                    with self.assertRaises(REJECTION):
                        i.read(data)

    def test_transport_tampering_both_directions(self):
        for role in (False, True):
            with self.subTest(go_initiator=role), pair(role) as (i, r, _):
                handshake(i, r)
                for sender, recipient in ((i, r), (r, i)):
                    with self.assertRaises(REJECTION):
                        recipient.decrypt(
                            flip(sender.encrypt(b"confirmed-key-material"))
                        )

    def test_transport_replay_both_directions(self):
        for role in (False, True):
            with self.subTest(go_initiator=role), pair(role) as (i, r, _):
                handshake(i, r)
                for sender, recipient in ((i, r), (r, i)):
                    ciphertext = sender.encrypt(b"confirm")
                    self.assertEqual(recipient.decrypt(ciphertext), b"confirm")
                    with self.assertRaises(REJECTION):
                        recipient.decrypt(ciphertext)

    def test_truncated_transport_both_directions(self):
        for role in (False, True):
            with self.subTest(go_initiator=role), pair(role) as (i, r, _):
                handshake(i, r)
                for sender, recipient in ((i, r), (r, i)):
                    ciphertext = sender.encrypt(b"confirm")
                    with self.assertRaises(REJECTION):
                        recipient.decrypt(ciphertext[:15])

    def test_missing_or_wrong_length_psk_fails_closed(self):
        for constructor in (GoPeer, PythonPeer):
            for size in (0, 8, 31, 33):
                with self.subTest(peer=constructor.__name__, size=size):
                    with self.assertRaises(REJECTION):
                        constructor(True, secrets.token_bytes(size), context())

    def test_new_sessions_are_distinct(self):
        for role in (False, True):
            # Same PSK and prologue still produce fresh ephemeral/session keys.
            psk, prologue = secrets.token_bytes(32), context()
            hashes = []
            for _ in range(2):
                g = GoPeer(role, psk, prologue)
                try:
                    p = PythonPeer(not role, psk, prologue)
                    i, r = (g, p) if role else (p, g)
                    handshake(i, r)
                    hashes.append(i.state())
                finally:
                    g.close()
            self.assertNotEqual(hashes[0], hashes[1])

    def test_replayed_first_message_cannot_confirm_new_session(self):
        for role in (False, True):
            psk, prologue = secrets.token_bytes(32), context()
            g = GoPeer(role, psk, prologue)
            second_g = None
            try:
                p = PythonPeer(not role, psk, prologue)
                i, r = (g, p) if role else (p, g)
                first, _ = handshake(i, r)
                old_confirmation = i.encrypt(b"client-confirm:" + i.state()[0])
                if role:
                    fresh_r = PythonPeer(False, psk, prologue)
                else:
                    second_g = GoPeer(False, psk, prologue)
                    fresh_r = second_g
                # Noise does NOT reject a replayed first message by itself.
                # Real pending-request state/attempt limits must enforce that.
                fresh_r.read(first)
                fresh_r.write(b"fresh-server-identity")
                with self.assertRaises(REJECTION):
                    fresh_r.decrypt(old_confirmation)
            finally:
                g.close()
                if second_g:
                    second_g.close()


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(Compatibility)
    log = io.StringIO()
    result = unittest.TextTestRunner(verbosity=2, stream=log).run(suite)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "test-output.txt").write_text(log.getvalue(), encoding="utf-8")
    sys.stderr.write(log.getvalue())
    evidence = {
        "profile": PROFILE.decode(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "dependencies": {
            name: importlib.metadata.version(name)
            for name in ("noiseprotocol", "cryptography", "cffi", "pycparser")
        },
        "test_methods": result.testsRun,
        "failures": len(result.failures),
        "errors": len(result.errors),
        "successful": result.wasSuccessful(),
    }
    (OUTPUT / "test-results.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
    )
    sys.exit(not result.wasSuccessful())
