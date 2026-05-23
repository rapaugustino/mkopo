"""Unit tests for the magic-link email composer.

We pin the per-purpose subject and the bits of body copy that the
audit + UX care about, but we deliberately don't snapshot the full
HTML — that's noisy to maintain and the visible text content is what
actually reaches the borrower's eyes anyway.

Network is never touched: ``compose_magic_link_email`` is pure.
"""

from __future__ import annotations

from typing import Any

import pytest

from mkopo.tools.comms import (
    compose_magic_link_email,
    send_magic_link_email,
)

URL = "https://app.example.com/auth/verify?purpose=login&token=abc"


def test_login_subject_and_button():
    email = compose_magic_link_email(
        to="user@example.com", url=URL, purpose="login", expires_minutes=15
    )
    assert email.to == "user@example.com"
    assert "Sign in to Mkopo" == email.subject
    assert "sign in" in email.body_text.lower()
    assert URL in email.body_text
    assert URL in (email.body_html or "")
    assert "Sign in" in (email.body_html or "")


def test_password_reset_subject_and_intro():
    email = compose_magic_link_email(
        to="user@example.com",
        url=URL,
        purpose="password_reset",
        expires_minutes=15,
    )
    assert "Reset your Mkopo password" == email.subject
    assert "ignore this email" in email.body_text.lower()
    assert "Reset password" in (email.body_html or "")


def test_email_verify_subject():
    email = compose_magic_link_email(
        to="user@example.com",
        url=URL,
        purpose="email_verify",
        expires_minutes=15,
    )
    assert "Confirm your email at Mkopo" == email.subject
    assert "confirm" in email.body_text.lower()


def test_set_password_welcomes_new_user():
    email = compose_magic_link_email(
        to="user@example.com",
        url=URL,
        purpose="set_password",
        expires_minutes=15,
    )
    assert "Set up your Mkopo password" == email.subject
    assert "welcome" in email.body_text.lower()


def test_named_recipient_personalises_greeting():
    email = compose_magic_link_email(
        to="user@example.com",
        url=URL,
        purpose="login",
        expires_minutes=15,
        recipient_name="Asha",
    )
    assert "Hi Asha," in email.body_text
    assert "Hi Asha," in (email.body_html or "")


def test_anonymous_recipient_uses_generic_greeting():
    email = compose_magic_link_email(
        to="user@example.com",
        url=URL,
        purpose="login",
        expires_minutes=15,
    )
    assert "Hi there," in email.body_text


def test_blank_name_still_uses_generic_greeting():
    email = compose_magic_link_email(
        to="user@example.com",
        url=URL,
        purpose="login",
        expires_minutes=15,
        recipient_name="   ",
    )
    assert "Hi there," in email.body_text


def test_expiry_is_called_out_in_body():
    email = compose_magic_link_email(
        to="user@example.com", url=URL, purpose="login", expires_minutes=42
    )
    assert "42 minutes" in email.body_text
    assert "42 minutes" in (email.body_html or "")


def test_replies_warning_in_text_body():
    """Every magic-link email tells the recipient replies aren't
    monitored — Mkopo doesn't process inbound email."""
    email = compose_magic_link_email(
        to="user@example.com", url=URL, purpose="login", expires_minutes=15
    )
    body_lower = email.body_text.lower()
    assert "aren't monitored" in body_lower or "not monitored" in body_lower


def test_unknown_purpose_raises():
    with pytest.raises(ValueError, match="unknown magic-link purpose"):
        compose_magic_link_email(
            to="user@example.com",
            url=URL,
            purpose="bogus",  # type: ignore[arg-type]
            expires_minutes=15,
        )


def test_custom_brand_applied():
    email = compose_magic_link_email(
        to="user@example.com",
        url=URL,
        purpose="login",
        expires_minutes=15,
        brand="LenderCo",
    )
    assert "Sign in to LenderCo" == email.subject
    assert "LenderCo" in (email.body_html or "")


# ---- send_magic_link_email (best-effort wrapper) ------------------------


@pytest.mark.asyncio
async def test_send_returns_none_when_resend_key_unset(monkeypatch):
    """In dev (no API key), the function logs + returns None rather
    than blowing up. Borrower auth's BackgroundTask depends on this."""
    from mkopo.tools import comms as comms_mod

    class _UnconfiguredGateway:
        is_configured = False
        _settings = type("S", (), {"resend_from_name": "Mkopo"})()

        async def send(self, _email: Any) -> Any:  # pragma: no cover
            raise AssertionError("send must not be called when unconfigured")

    monkeypatch.setattr(comms_mod, "get_comms", lambda: _UnconfiguredGateway())

    result = await send_magic_link_email(
        to="user@example.com",
        url=URL,
        purpose="login",
        expires_minutes=15,
    )
    assert result is None


@pytest.mark.asyncio
async def test_send_swallows_resend_errors(monkeypatch):
    """Resend network errors are logged + return None — never raise.
    Borrower auth fires this from a BackgroundTask after the response
    is already sent, so an exception would just become an unhandled
    background error with no UX hook."""
    from mkopo.tools import comms as comms_mod

    # Force "configured" path so we exercise the try/except wrapper.
    class _FakeGateway:
        is_configured = True
        _settings = type("S", (), {"resend_from_name": "Mkopo"})()

        async def send(self, _email: Any) -> Any:
            raise RuntimeError("simulated network failure")

    monkeypatch.setattr(comms_mod, "get_comms", lambda: _FakeGateway())

    result = await send_magic_link_email(
        to="user@example.com",
        url=URL,
        purpose="login",
        expires_minutes=15,
    )
    assert result is None
