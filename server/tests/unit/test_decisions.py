from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from typing import cast

import pytest

from arbiter_server.decisions import (
    AllowDecision,
    AndDecision,
    DecisionResult,
    DenyDecision,
    NotDecision,
    OrDecision,
)


@dataclass
class RecordingDecision:
    result: DecisionResult
    calls: list[str]
    name: str

    def evaluate(self) -> DecisionResult:
        self.calls.append(self.name)
        return self.result


def test_decision_result_require_allowed_raises_denial_reason() -> None:
    DecisionResult.allow().require_allowed()

    with pytest.raises(ValueError, match="blocked by policy"):
        DecisionResult.deny("blocked by policy").require_allowed()


def test_decision_result_copies_and_freezes_evidence_mapping() -> None:
    evidence = {"folder": "INBOX"}
    result = DecisionResult.allow(evidence=evidence)

    evidence["folder"] = "Trash"

    assert result.evidence == {"folder": "INBOX"}
    with pytest.raises(TypeError):
        result.evidence["folder"] = "Trash"  # type: ignore[index]


def test_decision_result_deep_copies_and_freezes_nested_evidence() -> None:
    evidence: dict[str, object] = {
        "folder": {"name": "INBOX"},
        "rules": ["allow_glob"],
    }
    result = DecisionResult.allow(evidence=evidence)

    cast(dict[str, object], evidence["folder"])["name"] = "Trash"
    cast(list[str], evidence["rules"]).append("deny_exact")

    assert result.evidence == {
        "folder": {"name": "INBOX"},
        "rules": ("allow_glob",),
    }
    with pytest.raises(TypeError):
        cast(dict[str, object], result.evidence["folder"])["name"] = "Trash"
    with pytest.raises(AttributeError):
        cast(list[str], result.evidence["rules"]).append("deny_exact")


def test_decision_result_evidence_remains_json_and_asdict_friendly() -> None:
    result = DecisionResult.deny(
        "blocked",
        evidence={"folder": {"name": "INBOX"}, "rules": ["allow_glob"]},
        failed_gate="folder_access",
    )

    assert json.loads(json.dumps(result.evidence)) == {
        "folder": {"name": "INBOX"},
        "rules": ["allow_glob"],
    }
    assert asdict(result) == {
        "allowed": False,
        "why_not": "blocked",
        "evidence": {
            "folder": {"name": "INBOX"},
            "rules": ("allow_glob",),
        },
        "failed_gate": "folder_access",
    }


def test_and_decision_allows_when_all_children_allow() -> None:
    result = AndDecision(
        AllowDecision({"source": "ok"}),
        AllowDecision({"destination": "ok"}),
    ).evaluate()

    assert result == DecisionResult.allow(
        evidence={"source": "ok", "destination": "ok"}
    )


def test_and_decision_returns_first_denial_and_short_circuits() -> None:
    calls: list[str] = []

    result = AndDecision(
        RecordingDecision(DecisionResult.allow(), calls, "source"),
        RecordingDecision(
            DecisionResult.deny(
                "destination denied",
                evidence={"destination": "denied"},
                failed_gate="destination_access",
            ),
            calls,
            "destination",
        ),
        RecordingDecision(DecisionResult.allow(), calls, "move"),
    ).evaluate()

    assert result == DecisionResult.deny(
        "destination denied",
        evidence={"destination": "denied"},
        failed_gate="destination_access",
    )
    assert calls == ["source", "destination"]


def test_or_decision_allows_first_allowed_child_and_short_circuits() -> None:
    calls: list[str] = []

    result = OrDecision(
        RecordingDecision(
            DecisionResult.deny(
                "delete denied",
                evidence={"delete": "denied"},
                failed_gate="delete",
            ),
            calls,
            "delete",
        ),
        RecordingDecision(
            DecisionResult.allow(evidence={"move": "allowed"}),
            calls,
            "move",
        ),
        RecordingDecision(DecisionResult.deny("unused"), calls, "unused"),
    ).evaluate()

    assert result == DecisionResult.allow(
        evidence={"delete": "denied", "move": "allowed"}
    )
    assert calls == ["delete", "move"]


def test_or_decision_returns_last_denial_when_no_child_allows() -> None:
    result = OrDecision(
        DenyDecision("delete denied", {"delete": "denied"}, failed_gate="delete"),
        DenyDecision(
            "move denied",
            {"move": "denied"},
            failed_gate="destination_selector",
        ),
    ).evaluate()

    assert result == DecisionResult.deny(
        "move denied",
        evidence={"delete": "denied", "move": "denied"},
        failed_gate="destination_selector",
    )


def test_not_decision_inverts_child_result() -> None:
    assert NotDecision(DenyDecision("blocked")).evaluate() == DecisionResult.allow()

    assert NotDecision(
        AllowDecision({"state": "open"}),
        why_not="must be closed",
        failed_gate="closed",
    ).evaluate() == DecisionResult.deny(
        "must be closed",
        evidence={"state": "open"},
        failed_gate="closed",
    )
