"""IMAP's mapping of protocol exceptions to the public operation contract."""

from __future__ import annotations

import imaplib
import ssl

from arbiter_server.errors import OperationError, Replay


class IMAPAuthenticationError(imaplib.IMAP4.error):
    """A failure specifically during the login exchange."""


def imap_operation_error(exc: Exception, *, replay: Replay) -> OperationError:
    if isinstance(exc, IMAPAuthenticationError):
        return OperationError(
            "upstream_auth_failed",
            "IMAP authentication failed.",
            plugin="imap",
            domain_code="authentication_failed",
            transient="no",
            replay="safe",
        )
    if isinstance(exc, TimeoutError):
        return OperationError(
            "upstream_timeout",
            "IMAP operation timed out.",
            plugin="imap",
            domain_code="timeout",
            transient="yes",
            replay=replay,
        )
    if isinstance(exc, ssl.SSLError):
        return OperationError(
            "plugin_error",
            "IMAP TLS connection failed.",
            plugin="imap",
            domain_code="tls_failed",
            replay=replay,
        )
    if isinstance(exc, (OSError, imaplib.IMAP4.abort)):
        return OperationError(
            "upstream_unavailable",
            "IMAP service is unavailable.",
            plugin="imap",
            domain_code="connection_failed",
            transient="yes",
            replay=replay,
        )
    return OperationError(
        "upstream_rejected",
        "IMAP rejected the operation.",
        plugin="imap",
        domain_code="command_rejected",
        replay=replay,
    )
