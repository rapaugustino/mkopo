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

Failure semantics: ``send`` raises on Resend errors so the caller
can decide. The magic-link helper :func:`send_magic_link_email`
catches and returns ``None`` instead — borrower auth uses it through
a ``BackgroundTask`` and we don't want a transient Resend hiccup to
crash a sign-in attempt that already succeeded on our side.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

import resend
import structlog
from pydantic import BaseModel

from mkopo.config import get_settings

logger = structlog.get_logger()


# Mirror of ``mkopo.services.auth_service.MagicLinkPurpose`` so this
# module doesn't import from auth (auth_service already imports the
# config; we want comms to be importable from anywhere, including
# auth_service if it ever wants to). Stay in sync if the auth set
# changes — the test file pins both lists.
MagicLinkPurposeLiteral = Literal[
    "login", "set_password", "password_reset", "email_verify"
]


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
        # ``resend`` is a module-level singleton — assigning api_key
        # is global. That's fine because we deploy one tenant; do not
        # try to multiplex multiple API keys here.
        resend.api_key = settings.resend_api_key

    @property
    def is_configured(self) -> bool:
        """Whether Resend can actually send mail. False in dev when
        ``RESEND_API_KEY`` isn't set. Callers should check this before
        deciding whether a missing email is "broken" or "intentional"."""
        return bool(self._settings.resend_api_key)

    async def send(self, email: OutboundEmail) -> SendResult:
        """Send an email via Resend. Returns the provider message_id.

        Failures bubble up — the caller decides whether to surface
        them. For magic links specifically use
        :func:`send_magic_link_email`, which catches Resend errors so
        a transient hiccup doesn't fail the upstream sign-in.
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

        # The resend SDK exposes ``send_async`` (proper coroutine) and
        # ``send`` (blocking httpx call). Use async so we don't pin a
        # thread on the event loop.
        result = await resend.Emails.send_async(params)
        message_id = result.get("id", "") if isinstance(result, dict) else getattr(result, "id", "")
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


# ---- magic-link email composer ----------------------------------------


def _purpose_copy(
    purpose: MagicLinkPurposeLiteral, *, expires_minutes: int, brand: str
) -> tuple[str, str, str]:
    """Subject + intro line + button label for one magic-link purpose.

    Pulled out so :func:`compose_magic_link_email` can build subject +
    body deterministically — and so the unit test can pin exact
    copy per purpose without re-rendering the whole template.
    """
    match purpose:
        case "login":
            return (
                f"Sign in to {brand}",
                "Click the button below to sign in to your account.",
                "Sign in",
            )
        case "password_reset":
            return (
                f"Reset your {brand} password",
                (
                    "Click below to set a new password. If you didn't "
                    "request this, you can safely ignore this email."
                ),
                "Reset password",
            )
        case "set_password":
            return (
                f"Set up your {brand} password",
                (
                    f"Welcome to {brand}. Click below to set your "
                    "password and finish creating your account."
                ),
                "Set password",
            )
        case "email_verify":
            return (
                f"Confirm your email at {brand}",
                "Click below to confirm your email address.",
                "Confirm email",
            )
        case _:  # pragma: no cover — Literal exhaustiveness handled by mypy
            raise ValueError(f"unknown magic-link purpose: {purpose}")


def compose_magic_link_email(
    *,
    to: str,
    url: str,
    purpose: MagicLinkPurposeLiteral,
    expires_minutes: int,
    recipient_name: str | None = None,
    brand: str = "Mkopo",
) -> OutboundEmail:
    """Pure function: build the :class:`OutboundEmail` for a magic
    link. No side effects, no network — testable on its own.

    The text body is the canonical form (most clients render text
    fine on small screens, and corporate filters trust text more than
    HTML). The HTML body is a clean, single-column layout suitable for
    every modern client without webfonts, images, or media queries —
    Resend recommends inline styles for maximum compatibility.
    """
    subject, intro, button_label = _purpose_copy(
        purpose, expires_minutes=expires_minutes, brand=brand
    )
    greeting = (
        f"Hi {recipient_name.strip()},"
        if recipient_name and recipient_name.strip()
        else "Hi there,"
    )
    body_text = (
        f"{greeting}\n\n"
        f"{intro}\n\n"
        f"{url}\n\n"
        f"This link expires in {expires_minutes} minutes and can only be "
        "used once. If you didn't ask for this, you can ignore the email — "
        "nothing has changed.\n\n"
        f"— {brand}\n"
        "You're receiving this because someone (hopefully you) used your "
        "email to sign in or recover access. Replies to this address "
        "aren't monitored; reach us through your account instead."
    )
    # Style constants. Kept here (instead of inlined further down)
    # so the long-line ruff lint doesn't trip on each row of the
    # template — email clients ignore whitespace inside tags, so
    # splitting attributes across lines is safe.
    body_style = (
        "margin:0;padding:0;background:#f5f5f5;"
        "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
        "color:#111;"
    )
    card_style = (
        "background:#ffffff;border-radius:12px;"
        "border:1px solid #e5e5e5;padding:32px;"
    )
    brand_style = (
        "font-size:14px;color:#666;letter-spacing:0.05em;"
        "text-transform:uppercase;padding-bottom:24px;"
    )
    button_style = (
        "display:inline-block;background:#111;color:#fff;"
        "text-decoration:none;font-weight:500;font-size:15px;"
        "padding:12px 24px;border-radius:8px;"
    )
    footer_style = (
        "font-size:13px;color:#888;padding-top:24px;"
        "border-top:1px solid #eee;line-height:1.6;"
    )
    body_html = f"""<!doctype html>
<html lang="en">
<body style="{body_style}">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0"
         border="0" style="background:#f5f5f5;padding:32px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="520" cellpadding="0"
               cellspacing="0" border="0" style="{card_style}">
          <tr>
            <td style="{brand_style}">{brand}</td>
          </tr>
          <tr>
            <td style="font-size:18px;font-weight:600;padding-bottom:16px;">
              {subject}
            </td>
          </tr>
          <tr>
            <td style="font-size:15px;line-height:1.6;color:#333;padding-bottom:24px;">
              {greeting}<br><br>{intro}
            </td>
          </tr>
          <tr>
            <td align="center" style="padding-bottom:24px;">
              <a href="{url}" style="{button_style}">{button_label}</a>
            </td>
          </tr>
          <tr>
            <td style="font-size:13px;color:#666;line-height:1.6;padding-bottom:8px;">
              Or paste this link into your browser:<br>
              <a href="{url}" style="color:#0066cc;word-break:break-all;">{url}</a>
            </td>
          </tr>
          <tr>
            <td style="{footer_style}">
              This link expires in {expires_minutes} minutes and can only be used once.
              If you didn't ask for this, you can ignore the email — nothing has changed.
            </td>
          </tr>
          <tr>
            <td style="font-size:12px;color:#aaa;padding-top:16px;line-height:1.5;">
              Replies to this address aren't monitored. Reach us through your account.
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
    return OutboundEmail(
        to=to,
        subject=subject,
        body_text=body_text,
        body_html=body_html,
    )


async def send_magic_link_email(
    *,
    to: str,
    url: str,
    purpose: MagicLinkPurposeLiteral,
    expires_minutes: int,
    recipient_name: str | None = None,
) -> SendResult | None:
    """Compose + dispatch a magic-link email.

    Returns ``None`` and logs a warning when:
      - ``RESEND_API_KEY`` isn't configured (dev mode without inbox);
      - the Resend call itself errors (transient network / quota /
        domain misconfig).

    Crucially: this never raises. The borrower-auth endpoints schedule
    this via ``BackgroundTasks`` after the response has already been
    sent, so an exception here couldn't be reported back to the user
    anyway — turning it into a log line lets observability catch it.
    """
    gateway = get_comms()
    email = compose_magic_link_email(
        to=to,
        url=url,
        purpose=purpose,
        expires_minutes=expires_minutes,
        recipient_name=recipient_name,
        brand=gateway._settings.resend_from_name,
    )

    if not gateway.is_configured:
        # Dev path. The URL was already returned in the response body
        # (dev only) so the test flow is unaffected. Log so the deployer
        # sees that real delivery is off.
        logger.info(
            "magic_link_email_skipped_no_resend_key",
            to=to,
            purpose=purpose,
        )
        return None

    try:
        return await gateway.send(email)
    except Exception as e:
        # Log + swallow. The borrower flow has already returned 200;
        # we can't undo that. Anti-enumeration also wants us not to
        # propagate the failure as a 5xx.
        logger.error(
            "magic_link_email_send_failed",
            to=to,
            purpose=purpose,
            error=str(e)[:200],
            error_type=type(e).__name__,
        )
        return None
