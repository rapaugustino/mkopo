"""Communication gateway. Wraps Resend for outbound; parses inbound webhooks."""

from __future__ import annotations

import hashlib
import hmac
import uuid
from typing import Any

import resend
import structlog
from pydantic import BaseModel

from mkopo.config import get_settings

logger = structlog.get_logger()


class OutboundEmail(BaseModel):
    """Payload for sending an email."""

    to: str
    subject: str
    body_text: str
    body_html: str | None = None
    in_reply_to: str | None = None  # Resend message_id of the message we're replying to
    reply_to: str | None = None
    thread_id: uuid.UUID | None = None  # mkopo internal thread


class SendResult(BaseModel):
    message_id: str
    thread_id: uuid.UUID


class InboundEmail(BaseModel):
    """Parsed inbound email from Resend webhook."""

    message_id: str
    in_reply_to: str | None
    from_address: str
    to_address: str
    subject: str
    body_text: str
    body_html: str | None
    raw_payload: dict[str, Any]


class CommsGateway:
    def __init__(self) -> None:
        settings = get_settings()
        self._settings = settings
        resend.api_key = settings.resend_api_key

    async def send(self, email: OutboundEmail) -> SendResult:
        """Send an email via Resend. Returns the provider message_id."""
        thread_id = email.thread_id or uuid.uuid4()
        headers = {}
        if email.in_reply_to:
            headers["In-Reply-To"] = email.in_reply_to

        params: dict[str, Any] = {
            "from": f"{self._settings.resend_from_name} <{self._settings.resend_from_address}>",
            "to": [email.to],
            "subject": email.subject,
            "text": email.body_text,
        }
        if email.body_html:
            params["html"] = email.body_html
        if email.reply_to:
            params["reply_to"] = email.reply_to
        if headers:
            params["headers"] = headers

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

    def verify_webhook_signature(
        self,
        *,
        body: bytes,
        signature_header: str,
    ) -> bool:
        """Verify a Resend inbound webhook signature."""
        if not self._settings.resend_webhook_secret:
            logger.warning("resend_webhook_secret_not_configured")
            return self._settings.environment == "development"

        expected = hmac.new(
            self._settings.resend_webhook_secret.encode(),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature_header)

    def parse_inbound(self, payload: dict[str, Any]) -> InboundEmail:
        """Parse a Resend inbound webhook payload into an InboundEmail."""
        # Resend's inbound payload shape (simplified — see Resend docs for full schema)
        data = payload.get("data", payload)
        return InboundEmail(
            message_id=data.get("message_id", ""),
            in_reply_to=data.get("in_reply_to"),
            from_address=data.get("from", ""),
            to_address=data.get("to", [""])[0]
            if isinstance(data.get("to"), list)
            else data.get("to", ""),
            subject=data.get("subject", ""),
            body_text=data.get("text", ""),
            body_html=data.get("html"),
            raw_payload=payload,
        )


_gateway: CommsGateway | None = None


def get_comms() -> CommsGateway:
    global _gateway
    if _gateway is None:
        _gateway = CommsGateway()
    return _gateway
