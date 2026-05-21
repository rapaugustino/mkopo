"""Webhook receivers. Resend inbound for borrower replies."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import select

from mkopo.db import get_session
from mkopo.models import Message, MessageDirection
from mkopo.services.audit import Actor, record
from mkopo.tools.comms import get_comms

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post("/resend/inbound", status_code=status.HTTP_200_OK)
async def resend_inbound(
    request: Request,
    x_resend_signature: str | None = Header(None),
) -> dict[str, str]:
    """Handle inbound emails from Resend.

    Threading: we look up the original outbound message by Resend message_id
    via In-Reply-To header, then attach the inbound reply to the same loan thread.
    """
    body = await request.body()
    comms = get_comms()

    if not comms.verify_webhook_signature(
        body=body,
        signature_header=x_resend_signature or "",
    ):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")

    payload = await request.json()
    inbound = comms.parse_inbound(payload)

    async with get_session() as session:
        # Find the outbound message this is replying to (if any)
        loan_id: uuid.UUID | None = None
        thread_id: uuid.UUID | None = None

        if inbound.in_reply_to:
            stmt = select(Message).where(
                Message.resend_metadata["message_id"].astext == inbound.in_reply_to
            )
            origin = (await session.execute(stmt)).scalar_one_or_none()
            if origin:
                loan_id = origin.loan_id
                thread_id = origin.thread_id

        if loan_id is None:
            # Unknown reply — log it and move on
            return {"status": "ignored", "reason": "no_matching_thread"}

        message = Message(
            loan_id=loan_id,
            thread_id=thread_id,  # type: ignore[arg-type]
            direction=MessageDirection.INBOUND,
            sender=inbound.from_address,
            recipient=inbound.to_address,
            subject=inbound.subject,
            body=inbound.body_text,
            resend_metadata={"message_id": inbound.message_id, "in_reply_to": inbound.in_reply_to},
        )
        session.add(message)
        await record(
            session,
            loan_id=loan_id,
            actor=Actor.system(),
            action="inbound_email",
            payload={
                "from": inbound.from_address,
                "subject": inbound.subject[:120],
                "body_text": inbound.body_text[:4000],
                "message_id": inbound.message_id,
            },
        )

    return {"status": "received", "loan_id": str(loan_id)}
