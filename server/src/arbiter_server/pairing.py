"""Bounded Noise channel foundation; this module cannot activate an enrollment.

The prologue joins the fixed prefix, ASCII selector (22 bytes), exact request
SHA-256 (32 bytes), and access/portable/recipient DER SPKIs (91/91/44 bytes).
The first payload on each side binds its role and SHA-256 of that prologue.
Signing-key proofs run inside this channel; durable trust and activation remain
later protocol steps.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import hmac
import threading
import time
from typing import Any, TypeVar

from noise.connection import NoiseConnection

from .blocking import BlockingOperationPool
from .credentials import SigningCredential
from .enrollment import Advertisement, ApprovedRequest, EnrollmentRequests
from .errors import OperationError
from .limits import NativeTransportLimitsConfig
from .pairing_proofs import server_proof, verify_client_proof

PROFILE = b"Noise_NNpsk0_25519_ChaChaPoly_SHA256"
MAX_ATTEMPTS = 5
Result = TypeVar("Result")


def prologue(advertisement: Advertisement) -> bytes:
    # All encodings have fixed validated lengths; the digest binds the exact
    # accepted advertisement bytes, independently of JSON implementation.
    return (
        b"arbiter-enrollment-v1\0client\0server\0"
        + advertisement.selector.encode("ascii")
        + bytes.fromhex(advertisement.digest)
        + advertisement.access_spki
        + advertisement.portable_spki
        + advertisement.recipient_spki
    )


def first_payload(advertisement: Advertisement, *, server: bool = False) -> bytes:
    role = b"server" if server else b"client"
    return (
        b"arbiter-enrollment-v1\0"
        + role
        + b"\0"
        + hashlib.sha256(prologue(advertisement)).digest()
    )


def _failed() -> ValueError:
    return ValueError("Pairing exchange is unavailable or invalid; start again.")


def _busy() -> OperationError:
    return OperationError(
        "resource_exhausted",
        "Pairing capacity is full; try again later.",
        transient="yes",
        replay="safe",
    )


@dataclass(frozen=True)
class ReplayedHandshake:
    response: bytes


class PairingLease:
    def __init__(self, owner: PairingResponder) -> None:
        self._owner = owner
        self.deadline = owner._clock() + owner._limits.handshake_timeout_seconds
        self._closed = False
        self._busy = False

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.deadline - self._owner._clock())

    def close(self) -> None:
        self._owner._close_lease(self)


@dataclass(repr=False)
class PairingSession:
    lease: PairingLease
    approval: ApprovedRequest
    noise: Any
    first_digest: bytes
    response: bytes
    handshake_hash: bytes
    received: int
    sent: int
    proof_context: bytes = b""
    proof_complete: bool = False
    ready_complete: bool = False


@dataclass(repr=False)
class _Attempts:
    approval: ApprovedRequest
    digests: set[bytes] = field(default_factory=set)
    active: PairingSession | None = None
    verifying: bool = False
    prepared: bytes = b""


class PairingResponder:
    def __init__(
        self,
        requests: EnrollmentRequests,
        limits: NativeTransportLimitsConfig | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._requests = requests
        self._limits = replace(limits or NativeTransportLimitsConfig())
        self._clock = clock
        self._lock = threading.RLock()
        self._pool = BlockingOperationPool(self._limits.max_handshakes)
        self._leases: set[PairingLease] = set()
        self._attempts: dict[str, _Attempts] = {}
        self._closed = False

    def reserve(self) -> PairingLease:
        """Reserve before reading/parsing a carrier; there is no admission queue."""
        with self._lock:
            if self._closed or len(self._leases) >= self._limits.max_connections:
                raise _busy()
            lease = PairingLease(self)
            self._leases.add(lease)
            return lease

    def _check(self, lease: PairingLease) -> None:
        if (
            self._closed
            or lease._owner is not self
            or lease not in self._leases
            or lease._closed
            or self._clock() >= lease.deadline
        ):
            raise _failed()

    def _release(self, lease: PairingLease) -> None:
        if lease._closed and not lease._busy:
            for state in tuple(self._attempts.values()):
                if state.active is not None and state.active.lease is lease:
                    state.active = None
                    if len(state.digests) == MAX_ATTEMPTS:
                        self._exhaust(state)
            self._leases.discard(lease)

    def _exhaust(self, state: _Attempts) -> None:
        self._requests.exhaust_approval(state.approval)
        selector = state.approval.request.advertisement.selector
        if self._attempts.get(selector) is state:
            del self._attempts[selector]

    def _close_lease(self, lease: PairingLease) -> None:
        with self._lock:
            lease._closed = True
            self._release(lease)

    def _finished(self, lease: PairingLease) -> None:
        with self._lock:
            lease._busy = False
            self._release(lease)

    async def _run(
        self, lease: PairingLease, operation: Callable[[], Result]
    ) -> Result:
        with self._lock:
            try:
                self._check(lease)
            except ValueError:
                if lease._owner is self:
                    lease.close()
                raise
            if lease._busy:
                raise _busy()
            lease._busy = True

        def work() -> Result:
            with self._lock:
                self._check(lease)
            result = operation()
            with self._lock:
                self._check(lease)
            return result

        submitted = False

        async def dispatch() -> Result:
            nonlocal submitted
            submitted = True
            return await self._pool.run(work, on_finished=lambda: self._finished(lease))

        try:
            return await asyncio.wait_for(
                dispatch(),
                timeout=max(0.0, lease.deadline - self._clock()),
            )
        except BaseException:
            lease.close()
            if not submitted:
                self._finished(lease)
            raise

    def _state(self, selector: str) -> _Attempts:
        # Reap before inserting a new state. Rejected/reset/expired contexts
        # cannot accumulate outside the existing pending-request capacity.
        for key, state in tuple(self._attempts.items()):
            if (
                state.active is not None
                and self._clock() >= state.active.lease.deadline
            ):
                state.active.lease.close()
            if not self._requests.approval_is_current(state.approval):
                if state.active is not None:
                    state.active.lease.close()
                self._attempts.pop(key, None)
        approved = self._requests.approved(selector)
        if approved is None:
            raise _failed()
        if selector not in self._attempts:
            self._attempts[selector] = _Attempts(approved)
        return self._attempts[selector]

    def _live(self, session: PairingSession) -> None:
        self._check(session.lease)
        state = self._state(session.approval.request.advertisement.selector)
        if state.active is not session or state.approval is not session.approval:
            raise _failed()

    async def start(
        self, lease: PairingLease, selector: str, first: bytes
    ) -> PairingSession | ReplayedHandshake:
        def authenticate() -> PairingSession | ReplayedHandshake:
            if type(first) is not bytes or not 1 <= len(first) <= min(
                self._limits.max_noise_frame_bytes, self._limits.max_handshake_bytes
            ):
                raise _failed()
            digest = hashlib.sha256(first).digest()
            with self._lock:
                state = self._state(selector)
                if digest in state.digests:
                    if state.active is not None and state.active.first_digest == digest:
                        self._check(state.active.lease)
                        return ReplayedHandshake(state.active.response)
                    raise _failed()
                if state.active is not None or state.verifying:
                    raise _busy()
                if len(state.digests) >= MAX_ATTEMPTS:
                    raise _failed()
                state.verifying = True
            try:
                approval = state.approval
                if type(approval.secret) is not bytes or len(approval.secret) != 32:
                    raise _failed()
                noise = NoiseConnection.from_name(PROFILE)
                noise.set_as_responder()
                noise.set_psks(psk=approval.secret)
                noise.set_prologue(prologue(approval.request.advertisement))
                noise.start_handshake()
                payload = bytes(noise.read_message(first))
                if not hmac.compare_digest(
                    payload, first_payload(approval.request.advertisement)
                ):
                    raise _failed()
                with self._lock:
                    self._check(lease)
                    if not self._requests.approval_is_current(approval):
                        raise _failed()
                    state.digests.add(digest)
                # No registry/ownership lock spans cryptographic work. The
                # verifying marker excludes another mutable handshake.
                response = bytes(
                    noise.write_message(
                        first_payload(approval.request.advertisement, server=True)
                    )
                )
                if len(response) > min(
                    self._limits.max_noise_frame_bytes,
                    self._limits.max_handshake_bytes,
                ):
                    raise _failed()
                with self._lock:
                    self._check(lease)
                    if not self._requests.approval_is_current(approval):
                        raise _failed()
                    session = PairingSession(
                        lease,
                        approval,
                        noise,
                        digest,
                        response,
                        noise.get_handshake_hash(),
                        len(first),
                        len(response),
                    )
                    state.active = session
                    return session
            except OperationError:
                raise
            except Exception:
                raise _failed() from None
            finally:
                with self._lock:
                    state.verifying = False
                    if state.active is None and len(state.digests) == MAX_ATTEMPTS:
                        self._exhaust(state)

        return await self._run(lease, authenticate)

    async def message(
        self, session: PairingSession, data: bytes, *, encrypt: bool = False
    ) -> bytes:
        return await self._run(
            session.lease, lambda: self._message(session, data, encrypt=encrypt)
        )

    def _message(self, session: PairingSession, data: bytes, *, encrypt: bool) -> bytes:
        with self._lock:
            self._live(session)
            if type(data) is not bytes:
                raise _failed()
            limit = self._limits.max_noise_frame_bytes - (16 if encrypt else 0)
            size = len(data) + (16 if encrypt else 0)
            used = session.sent if encrypt else session.received
            if (
                not 1 <= len(data) <= limit
                or used + size > self._limits.max_handshake_bytes
            ):
                raise _failed()
        try:
            result = (
                session.noise.encrypt(data) if encrypt else session.noise.decrypt(data)
            )
        except Exception:
            raise _failed() from None
        with self._lock:
            self._live(session)
            if encrypt:
                session.sent += len(result)
            else:
                session.received += len(data)
        return result

    async def prove_server_from_authority(
        self, session: PairingSession, root: Path, now: datetime
    ) -> bytes:
        from .pairing_identity import load_pairing_identity

        material = await self._run(session.lease, lambda: load_pairing_identity(root))
        if material is None:
            raise _failed()
        return await self.prove_server(session, *material, now)

    async def prove_server(
        self,
        session: PairingSession,
        credential: SigningCredential,
        authority_der: bytes,
        now: datetime,
    ) -> bytes:
        def prove() -> bytes:
            with self._lock:
                self._live(session)
                if session.proof_context or session.proof_complete:
                    raise _failed()
            try:
                proof, context = server_proof(
                    session.handshake_hash,
                    prologue(session.approval.request.advertisement),
                    credential,
                    authority_der,
                    now,
                )
            except ValueError:
                raise _failed() from None
            wire = self._message(session, proof, encrypt=True)
            session.proof_context = context
            return wire

        return await self._run(session.lease, prove)

    async def prove_client(self, session: PairingSession, wire: bytes) -> None:
        def verify() -> None:
            with self._lock:
                self._live(session)
                if not session.proof_context or session.proof_complete:
                    raise _failed()
            proof = self._message(session, wire, encrypt=False)
            try:
                verify_client_proof(
                    proof, session.proof_context, session.approval.request.advertisement
                )
            except ValueError:
                raise _failed() from None
            with self._lock:
                self._live(session)
                session.proof_complete = True

        await self._run(session.lease, verify)

    async def prepare(self, session: PairingSession, root: Path) -> bytes:
        from .prepared import prepare

        def build() -> bytes:
            with self._lock:
                self._live(session)
                if not session.proof_complete:
                    raise _failed()
                state = self._state(session.approval.request.advertisement.selector)
                existing = state.prepared
                # Sample wall time first, so elapsed work cannot extend expiry.
                wall_now = datetime.now(timezone.utc).timestamp()
                remaining = session.approval.request.deadline - self._requests._clock()
                expires_at = int(wall_now + remaining)
            result = existing or prepare(root, session.approval, expires_at)
            with self._lock:
                self._live(session)
                state.prepared = result
            return result

        return await self._run(session.lease, build)

    async def ready(self, session: PairingSession, data: bytes) -> None:
        from .prepared import ready_context, verify_ready

        def verify() -> None:
            with self._lock:
                self._live(session)
                result = self._state(
                    session.approval.request.advertisement.selector
                ).prepared
                if not result or not session.proof_complete or session.ready_complete:
                    raise _failed()
            verify_ready(
                data,
                result,
                ready_context(
                    session.handshake_hash,
                    prologue(session.approval.request.advertisement),
                    result,
                ),
                session.approval,
            )
            with self._lock:
                self._live(session)
                session.ready_complete = True
            # No identity/access/grant write or activation is reachable here.

        await self._run(session.lease, verify)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            for lease in tuple(self._leases):
                lease.close()
            self._attempts.clear()
        self._pool.close()
