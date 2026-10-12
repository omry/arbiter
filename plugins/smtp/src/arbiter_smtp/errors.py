"""SMTP's mapping of protocol exceptions to the public operation contract."""

from __future__ import annotations

from collections.abc import Iterable
import logging
import smtplib
import ssl

from arbiter_server.errors import OperationError, log_unexpected


class SMTPPreSubmissionError(OSError):
    """A failure before any message submission was attempted."""

    def __init__(self, cause: Exception) -> None:
        super().__init__(str(cause))
        self.cause = cause


def smtp_operation_error(
    exc: Exception,
    *,
    recipient_count: int,
    attempted_recipients: Iterable[str] | None = None,
) -> OperationError:
    pre_submission = isinstance(exc, SMTPPreSubmissionError)
    if isinstance(exc, SMTPPreSubmissionError):
        exc = exc.cause
    if pre_submission and not isinstance(exc, (OSError, UnicodeError)):
        log_unexpected(logging.getLogger(__name__), exc)
        return OperationError(
            "internal_error",
            "Internal Arbiter server error.",
            plugin="smtp",
            replay="safe",
        )
    uncertain_replay = "safe" if pre_submission else "unknown"
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return OperationError(
            "upstream_auth_failed",
            "SMTP authentication failed.",
            plugin="smtp",
            domain_code="authentication_failed",
            transient="no",
            replay="safe",
        )
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        refused_recipients = {recipient.strip().lower() for recipient in exc.recipients}
        if attempted_recipients is None:
            attempted = recipient_count
            refused = len(refused_recipients)
            accepted = attempted - refused
        else:
            attempted_list = [
                recipient.strip().lower() for recipient in attempted_recipients
            ]
            attempted_set = set(attempted_list)
            refused_set = attempted_set & refused_recipients
            attempted = len(attempted_set)
            refused = len(refused_set)
            accepted = attempted - refused
            if refused_set and len(attempted_list) != len(attempted_set):
                # SMTP's refusal dictionary collapses repeated RCPT commands.
                # It cannot prove that every occurrence was rejected.
                return OperationError(
                    "partial_failure",
                    "SMTP recipient rejection left the delivery outcome uncertain.",
                    plugin="smtp",
                    domain_code="recipients_partially_rejected",
                    details={
                        "rejected_count": refused,
                        "steps": {"smtp_submission": "unknown"},
                    },
                    replay="unsafe",
                )
        partial = refused < attempted
        return OperationError(
            "partial_failure" if partial else "upstream_rejected",
            (
                "SMTP accepted some recipients but rejected others."
                if partial
                else "SMTP rejected the recipients."
            ),
            plugin="smtp",
            domain_code=(
                "recipients_partially_rejected" if partial else "recipients_rejected"
            ),
            details={
                "accepted_count": accepted,
                "rejected_count": refused,
                "steps": {
                    "smtp_submission": "partially_succeeded" if partial else "failed"
                },
            },
            replay="unsafe" if partial else "safe",
        )
    if isinstance(exc, smtplib.SMTPResponseException):
        return OperationError(
            "upstream_rejected",
            "SMTP rejected the operation.",
            plugin="smtp",
            domain_code="submission_rejected",
            details={"smtp_code": exc.smtp_code},
            transient="yes" if 400 <= exc.smtp_code < 500 else "no",
            replay="safe",
        )
    if isinstance(exc, smtplib.SMTPNotSupportedError):
        return OperationError(
            "plugin_error",
            "SMTP required capability is not supported.",
            plugin="smtp",
            domain_code="unsupported_capability",
            transient="no",
            replay="safe",
        )
    if isinstance(exc, TimeoutError):
        return OperationError(
            "upstream_timeout",
            "SMTP operation timed out.",
            plugin="smtp",
            domain_code="timeout",
            transient="yes",
            replay=uncertain_replay,
        )
    if isinstance(exc, ssl.SSLError):
        return OperationError(
            "plugin_error",
            "SMTP TLS connection failed.",
            plugin="smtp",
            domain_code="tls_failed",
            replay=uncertain_replay,
        )
    if isinstance(exc, smtplib.SMTPServerDisconnected) or (
        isinstance(exc, OSError) and not isinstance(exc, smtplib.SMTPException)
    ):
        return OperationError(
            "upstream_unavailable",
            "SMTP service is unavailable.",
            plugin="smtp",
            domain_code="connection_failed",
            transient="yes",
            replay=uncertain_replay,
        )
    return OperationError(
        "plugin_error",
        "SMTP operation failed.",
        plugin="smtp",
        domain_code="protocol_failed",
        replay=uncertain_replay,
    )
