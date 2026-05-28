from __future__ import annotations

from .imap import ImapServicePlugin
from .smtp import SmtpServicePlugin
from ..services import ServicePlugin


def default_service_plugins() -> list[ServicePlugin]:
    return [
        SmtpServicePlugin(),
        ImapServicePlugin(),
    ]
