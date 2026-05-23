"""Outbound communication gateway. Thin wrapper over Resend.

What this module is for, deliberately narrow:

  - Sending magic-link emails for borrower auth (set password / sign
    in / verify email / reset password).
  - Sending transactional notifications ("there's a new message on
    your application — sign in to read it") with a secure deep-link
    back to the app.

What this module is NOT for: inbound. We don't parse replies. The
in-app messaging surface (timeline composer on the staff side, the
borrower chat on the borrower side) is the channel for two-way
communication. Email is one-way "click this link to come back" only.

If you reach for "we need to handle a borrower reply via email,"
that's an in-app messaging feature, not an email-parsing feature —
build the surface where the borrower already lives (their authed
``/apply/{id}`` page) rather than re-parse email threads.
"""

from __future__ import annotations

import uuid
from typing import Any

import resend
import structlog
from pydantic import BaseModel

from mkopo.config import get_settings

logger = structlog.get_logger()


class OutboundEmail(BaseModel):
    """Payload for sending an email.

    Kept deliberately minimal — no ``in_reply_to`` because we don't
    thread replies back into the system, no ``reply_to`` because
    replies aren't a supported flow (the outbound email body should
    say "please reply in-app" rather than open a thread).
    """

    to: str
    subject: str
    body_text: str
    body_html: str | None = None
    # Internal correlation only — kept on outbound message rows so
    # the audit trail can show "this notification went out about
    # loan X". Not surfaced to Resend.
    thread_id: uuid.UUID | None = None


class SendResult(BaseModel):
    message_id: str
    thread_id: uuid.UUID


class CommsGateway:
    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        resend.api_key = settings.resend_api_key

    async def send(self, email: OutboundEmail) -> SendResult:
        """Send an email via Resend. Returns the provider message_id.

        Failures bubble up — the magic-link signup flow specifically
        needs to know if delivery failed so the user can be told to
        contact support, rather than silently get no email.
        """
        thread_id = email.thread_id or uuid.uuid4()

        params: dict[str, Any] = {
            "from": f"{self._settings.resend_from_name} <{self._settings.resend_from_address}>",
            "to": [email.to],
            "subject": email.subject,
            "text": email.body_text,
        }
        if email.body_html:
            params["html"] = email.body_html

        result = resend.Emails.send(params)
        message_id = result.get("id", "")
        logger.info(
            "email_sent",
            to=email.to,
            subject=email.subject[:80],
            message_id=message_id,
            thread_id=str(thread_id),
        )
        return SendResult(message_id=message_id, thread_id=thread_id)


_gateway: CommsGateway | None = None


def get_comms() -> CommsGateway:
    global _gateway
    if _gateway is None:
        _gateway = CommsGateway()
    return _gateway
