from __future__ import annotations

from arbiter_server.decisions import (
    AllowDecision,
    AndDecision,
    Decision,
    DecisionResult,
    DenyDecision,
)

from .config import SMTPRecipientPolicyConfig, SMTPServicePolicyConfig


class _SMTPRuntimePolicyMixin:
    def _policy_now(self) -> float:
        raise NotImplementedError

    def _rate_limit_attempt_timestamps(self) -> dict[str, list[float]]:
        raise NotImplementedError

    def _recipient_matches_list(
        self,
        recipient: str,
        configured_recipients: list[str],
    ) -> bool:
        raise NotImplementedError

    def _domain_matches_any_pattern(self, domain: str, patterns: list[str]) -> bool:
        raise NotImplementedError

    def _decision_check_result(
        self,
        operation_id: str,
        decision: DecisionResult,
    ) -> dict[str, object]:
        result: dict[str, object] = {
            "operation": operation_id,
            "allowed": decision.allowed,
            "evidence": decision.evidence,
        }
        if not decision.allowed:
            result["why_not"] = decision.why_not or "operation is not allowed"
            if decision.failed_gate is not None:
                result["failed_gate"] = decision.failed_gate
        return result

    def _send_policy_decision(
        self,
        *,
        account_name: str,
        smtp_policy: SMTPServicePolicyConfig,
        recipients: list[str],
    ) -> Decision:
        return AndDecision(
            self._max_recipients_decision(
                account_name=account_name,
                smtp_policy=smtp_policy,
                recipients=recipients,
            ),
            *(
                self._recipient_policy_decision(
                    recipient=recipient,
                    recipient_policy=smtp_policy.recipient_policy,
                )
                for recipient in recipients
            ),
        )

    def _max_recipients_decision(
        self,
        *,
        account_name: str,
        smtp_policy: SMTPServicePolicyConfig,
        recipients: list[str],
    ) -> Decision:
        max_recipients = smtp_policy.limits.max_recipients_per_message
        evidence = {
            "account": account_name,
            "recipient_count": len(recipients),
            "max_recipients_per_message": max_recipients,
        }
        if max_recipients is None or len(recipients) <= max_recipients:
            return AllowDecision(evidence)
        return DenyDecision(
            f"send_email exceeds max_recipients_per_message for account: {account_name}",
            evidence,
            failed_gate="max_recipients_per_message",
        )

    def _recipient_policy_decision(
        self,
        *,
        recipient: str,
        recipient_policy: SMTPRecipientPolicyConfig,
    ) -> Decision:
        normalized_recipient = recipient.strip().lower()
        _, _, domain = normalized_recipient.partition("@")
        evidence = {"recipient": recipient, "domain": domain}
        if self._recipient_matches_list(
            normalized_recipient, recipient_policy.blocked_recipients
        ):
            return DenyDecision(
                f"send_email recipient is blocked by exact address policy: {recipient}",
                evidence,
                failed_gate="blocked_recipient",
            )
        if self._domain_matches_any_pattern(
            domain, recipient_policy.blocked_domain_patterns
        ):
            return DenyDecision(
                f"send_email recipient is blocked by domain policy: {recipient}",
                evidence,
                failed_gate="blocked_domain",
            )

        has_allowlist = bool(
            recipient_policy.allowed_recipients
            or recipient_policy.allowed_domain_patterns
        )
        if has_allowlist and not (
            self._recipient_matches_list(
                normalized_recipient, recipient_policy.allowed_recipients
            )
            or self._domain_matches_any_pattern(
                domain, recipient_policy.allowed_domain_patterns
            )
        ):
            return DenyDecision(
                f"send_email recipient is not allowed by policy: {recipient}",
                evidence,
                failed_gate="allowed_recipients",
            )
        return AllowDecision(evidence)

    def _rate_limit_decision(
        self,
        account_name: str,
        smtp_policy: SMTPServicePolicyConfig,
    ) -> Decision:
        max_messages = smtp_policy.limits.max_messages_per_minute
        evidence: dict[str, object] = {
            "account": account_name,
            "max_messages_per_minute": max_messages,
        }
        if max_messages is None:
            return AllowDecision(evidence)

        now = self._policy_now()
        window_start = now - 60.0
        attempt_timestamps = self._rate_limit_attempt_timestamps()
        active_attempts = [
            timestamp
            for timestamp in attempt_timestamps.get(account_name, [])
            if timestamp > window_start
        ]
        evidence["active_attempt_count"] = len(active_attempts)
        if len(active_attempts) < max_messages:
            return AllowDecision(evidence)
        return DenyDecision(
            f"send_email exceeds max_messages_per_minute for account: {account_name}",
            evidence,
            failed_gate="max_messages_per_minute",
        )

    def _sent_copy_preflight_decision(
        self,
        *,
        smtp_policy: SMTPServicePolicyConfig,
        result: dict[str, object] | None,
    ) -> Decision:
        if smtp_policy.sent_copy.on_failure.value != "fail":
            return AllowDecision({"sent_copy": result or {"status": "not_required"}})
        if result is None:
            return AllowDecision({"sent_copy": {"status": "resolved"}})
        if result.get("status") in {"saved", "disabled", "resolved"}:
            return AllowDecision({"sent_copy": result})
        reason = result.get("reason")
        return DenyDecision(
            f"send_email sent-copy preflight failed: {reason}",
            {"sent_copy": result},
            failed_gate="sent_copy",
        )

    def _check_send_decision(
        self,
        *,
        operation_id: str,
        account: str,
        smtp_policy: SMTPServicePolicyConfig,
        recipients: list[str],
        sent_copy_result: dict[str, object] | None,
    ) -> dict[str, object]:
        decision = AndDecision(
            self._send_policy_decision(
                account_name=account,
                smtp_policy=smtp_policy,
                recipients=recipients,
            ),
            self._sent_copy_preflight_decision(
                smtp_policy=smtp_policy,
                result=sent_copy_result,
            ),
            self._rate_limit_decision(account, smtp_policy),
        ).evaluate()
        return self._decision_check_result(operation_id, decision)
