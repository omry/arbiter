from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass, field
from typing import NoReturn, Protocol


class FrozenEvidence(dict[str, object]):
    def _immutable(self, *_args: object, **_kwargs: object) -> NoReturn:
        raise TypeError("decision evidence is immutable")

    def __setitem__(self, _key: str, _value: object) -> NoReturn:
        self._immutable()

    def __delitem__(self, _key: str) -> NoReturn:
        self._immutable()

    def clear(self) -> NoReturn:
        self._immutable()

    def pop(self, _key: str, _default: object = None) -> NoReturn:
        self._immutable()

    def popitem(self) -> NoReturn:
        self._immutable()

    def setdefault(self, _key: str, _default: object = None) -> NoReturn:
        self._immutable()

    def update(self, *_args: object, **_kwargs: object) -> NoReturn:
        self._immutable()

    def __ior__(self, _other: object) -> NoReturn:
        self._immutable()


def _freeze_evidence_value(value: object) -> object:
    if isinstance(value, Mapping):
        return FrozenEvidence(
            {str(key): _freeze_evidence_value(item) for key, item in value.items()}
        )
    if isinstance(value, list | tuple):
        return tuple(_freeze_evidence_value(item) for item in value)
    return deepcopy(value)


def _freeze_evidence(evidence: Mapping[str, object]) -> FrozenEvidence:
    frozen = _freeze_evidence_value(evidence)
    if not isinstance(frozen, FrozenEvidence):
        raise TypeError("decision evidence must be a mapping")
    return frozen


@dataclass(frozen=True)
class DecisionResult:
    allowed: bool
    why_not: str | None = None
    evidence: Mapping[str, object] = field(default_factory=dict)
    failed_gate: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", _freeze_evidence(self.evidence))

    @classmethod
    def allow(
        cls,
        *,
        evidence: Mapping[str, object] | None = None,
    ) -> DecisionResult:
        return cls(allowed=True, evidence=evidence or {})

    @classmethod
    def deny(
        cls,
        why_not: str,
        *,
        evidence: Mapping[str, object] | None = None,
        failed_gate: str | None = None,
    ) -> DecisionResult:
        return cls(
            allowed=False,
            why_not=why_not,
            evidence=evidence or {},
            failed_gate=failed_gate,
        )

    def require_allowed(self) -> None:
        if not self.allowed:
            raise ValueError(self.why_not or "decision is not allowed")


class Decision(Protocol):
    def evaluate(self) -> DecisionResult: ...


@dataclass(frozen=True)
class AllowDecision:
    evidence: Mapping[str, object] = field(default_factory=dict)

    def evaluate(self) -> DecisionResult:
        return DecisionResult.allow(evidence=self.evidence)


@dataclass(frozen=True)
class DenyDecision:
    why_not: str
    evidence: Mapping[str, object] = field(default_factory=dict)
    failed_gate: str | None = None

    def evaluate(self) -> DecisionResult:
        return DecisionResult.deny(
            self.why_not,
            evidence=self.evidence,
            failed_gate=self.failed_gate,
        )


@dataclass(frozen=True, init=False)
class AndDecision:
    decisions: tuple[Decision, ...]

    def __init__(self, *decisions: Decision) -> None:
        object.__setattr__(self, "decisions", decisions)

    def evaluate(self) -> DecisionResult:
        evidence: dict[str, object] = {}
        for decision in self.decisions:
            result = decision.evaluate()
            evidence.update(result.evidence)
            if not result.allowed:
                return DecisionResult.deny(
                    result.why_not or "decision is not allowed",
                    evidence=evidence,
                    failed_gate=result.failed_gate,
                )
        return DecisionResult.allow(evidence=evidence)


@dataclass(frozen=True, init=False)
class OrDecision:
    decisions: tuple[Decision, ...]

    def __init__(self, *decisions: Decision) -> None:
        object.__setattr__(self, "decisions", decisions)

    def evaluate(self) -> DecisionResult:
        evidence: dict[str, object] = {}
        last_result: DecisionResult | None = None
        for decision in self.decisions:
            result = decision.evaluate()
            evidence.update(result.evidence)
            if result.allowed:
                return DecisionResult.allow(evidence=evidence)
            last_result = result
        if last_result is None:
            return DecisionResult.deny("no decision alternatives were provided")
        return DecisionResult.deny(
            last_result.why_not or "no decision alternatives allowed",
            evidence=evidence,
            failed_gate=last_result.failed_gate,
        )


@dataclass(frozen=True)
class NotDecision:
    decision: Decision
    why_not: str = "negated decision allowed"
    failed_gate: str | None = None

    def evaluate(self) -> DecisionResult:
        result = self.decision.evaluate()
        if result.allowed:
            return DecisionResult.deny(
                self.why_not,
                evidence=result.evidence,
                failed_gate=self.failed_gate,
            )
        return DecisionResult.allow(evidence=result.evidence)
