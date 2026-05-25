"""Startup sanity check.

Runs once on FastAPI lifespan startup and prints a clear report of which
integrations are wired vs. which will silently fall back. The point is
that a fresh clone deploying with a half-populated ``.env`` sees the
problem at boot, not when the first underwriter clicks *Run intake* and
gets a cryptic 500.

We log at INFO when an integration is fine, WARNING when something will
degrade gracefully (e.g. local storage instead of S3 because no AWS
creds), and ERROR when something must be set or the app cannot do its
core job (no Anthropic key → no agents).

This is intentionally NOT a hard fail. Hard-failing on missing config
is the right move for production deploys, but for dev / staging it's
useful to let the API come up so you can hit the OpenAPI docs and see
what's wired. The summary at the bottom of the log is the signal.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from mkopo.config import Settings


@dataclass
class CheckResult:
    """One integration check outcome.

    ``severity`` drives both the log level and the colourisation in the
    rendered console output. ``hint`` is the one-line explanation of
    how to fix it — read aloud and it should tell a fresh deployer
    exactly what to do.
    """

    name: str
    status: str  # "ok" | "degraded" | "missing"
    message: str
    hint: str | None = None


def _check_anthropic(settings: Settings) -> CheckResult:
    if not settings.anthropic_api_key or settings.anthropic_api_key.startswith("sk-ant-xxxxx"):
        return CheckResult(
            name="Anthropic (LLM)",
            status="missing",
            message="ANTHROPIC_API_KEY is not set",
            hint="Set ANTHROPIC_API_KEY in .env — the agents cannot run without it.",
        )
    return CheckResult(
        name="Anthropic (LLM)",
        status="ok",
        message=(
            f"using {settings.llm_default_model} (default) / "
            f"{settings.llm_heavy_model} (heavy)"
        ),
    )


def _check_openai(settings: Settings) -> CheckResult:
    if not settings.openai_api_key or settings.openai_api_key.startswith("sk-xxxxx"):
        return CheckResult(
            name="OpenAI (embeddings)",
            status="degraded",
            message="OPENAI_API_KEY not set — RAG + comparable-loans search disabled",
            hint="Set OPENAI_API_KEY in .env to enable 'Ask the file' and kNN search.",
        )
    return CheckResult(
        name="OpenAI (embeddings)",
        status="ok",
        message=f"{settings.embeddings_model} @ {settings.embeddings_dimensions} dims",
    )


def _check_resend(settings: Settings) -> CheckResult:
    if not settings.resend_api_key or settings.resend_api_key.startswith("re_xxxxx"):
        return CheckResult(
            name="Resend (email)",
            status="degraded",
            message=(
                "RESEND_API_KEY not set — outbound email (magic links, "
                "transactional notifications) will fail at send time"
            ),
            hint=(
                "Set RESEND_API_KEY in .env. Confirm the domain in "
                f"RESEND_FROM_ADDRESS ({settings.resend_from_address}) is "
                "verified at https://resend.com/domains."
            ),
        )
    return CheckResult(
        name="Resend (email)",
        status="ok",
        message=f"outbound from {settings.resend_from_address}",
    )


def _check_storage(settings: Settings) -> CheckResult:
    if settings.storage_backend == "local":
        return CheckResult(
            name="Document storage",
            status="ok" if settings.environment != "production" else "degraded",
            message=f"local filesystem ({settings.storage_root})",
            hint=(
                None
                if settings.environment != "production"
                else "STORAGE_BACKEND=local in production is a footgun; use s3."
            ),
        )
    # storage_backend == "s3"
    if not (settings.aws_access_key_id and settings.aws_secret_access_key):
        return CheckResult(
            name="Document storage",
            status="missing",
            message=(
                f"STORAGE_BACKEND=s3 (bucket {settings.s3_bucket}) but no AWS "
                "credentials configured"
            ),
            hint=(
                "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY (or use IAM "
                "roles on EC2 / ECS / App Runner) and confirm the bucket exists."
            ),
        )
    return CheckResult(
        name="Document storage",
        status="ok",
        message=f"S3 bucket {settings.s3_bucket} in {settings.aws_region}",
    )


def _check_auth(settings: Settings) -> CheckResult:
    """Staff bearer token. The dev default (`dev-token-replace-me`)
    is a placeholder, not a "no-op" — anyone who can reach the API
    with it has full admin. We always surface it as ``degraded`` (not
    ``ok``) so the deployer sees the warning on every boot, regardless
    of ``ENVIRONMENT`` value. Silent "ok" in dev was lulling people
    into shipping the placeholder.
    """
    if settings.dev_api_token == "dev-token-replace-me":
        return CheckResult(
            name="Auth (staff bearer)",
            status="degraded",
            message="DEV_API_TOKEN is the placeholder default — anyone with it has admin",
            hint=(
                "Set DEV_API_TOKEN in api/.env to a random ≥32-char value. "
                "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(32))'. "
                "Also update NEXT_PUBLIC_DEV_TOKEN in web/.env.local to match. "
                "Production deploys must change this; see task #186 (kill the dev bearer)."
            ),
        )
    if len(settings.dev_api_token) < 24:
        return CheckResult(
            name="Auth (staff bearer)",
            status="degraded",
            message=f"DEV_API_TOKEN is only {len(settings.dev_api_token)} chars",
            hint="Use a random secret of at least 24 characters.",
        )
    return CheckResult(
        name="Auth (staff bearer)",
        status="ok",
        message=f"DEV_API_TOKEN configured ({len(settings.dev_api_token)} chars)",
    )


def _check_jwt(settings: Settings) -> CheckResult:
    """Borrower-session JWT secret. The default is a placeholder
    that mints forgeable tokens — every deployment that leaves it
    in place shares the same signing key. We always report this as
    ``degraded`` (not ``ok``), regardless of ``ENVIRONMENT``, so the
    warning shows on every boot. Silent "ok" in dev was lulling
    people into shipping the placeholder.
    """
    default = "dev-jwt-secret-replace-me-min-32-chars"
    if settings.jwt_secret == default:
        return CheckResult(
            name="Auth (borrower JWT)",
            status="degraded",
            message="JWT_SECRET is the placeholder default — session tokens are forgeable",
            hint=(
                "Set JWT_SECRET in api/.env to a unique random ≥32-char value. "
                "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(48))'. "
                "Production deploys MUST change this — otherwise every deployment "
                "shares the same signing key and any borrower session can be forged."
            ),
        )
    if len(settings.jwt_secret) < 32:
        return CheckResult(
            name="Auth (borrower JWT)",
            status="degraded",
            message=f"JWT_SECRET is only {len(settings.jwt_secret)} chars",
            hint="Use a random secret of at least 32 characters (256 bits).",
        )
    return CheckResult(
        name="Auth (borrower JWT)",
        status="ok",
        message=f"JWT signing secret configured ({len(settings.jwt_secret)} chars)",
    )


def run_startup_checks(settings: Settings) -> list[CheckResult]:
    """Run every integration check and emit a structured log summary.

    Returns the results so callers (tests, ops dashboards) can render
    them anywhere — the standard FastAPI lifespan handler just logs.
    """
    logger = structlog.get_logger()
    results = [
        _check_anthropic(settings),
        _check_openai(settings),
        _check_resend(settings),
        _check_storage(settings),
        _check_auth(settings),
        _check_jwt(settings),
    ]
    ok = [r for r in results if r.status == "ok"]
    degraded = [r for r in results if r.status == "degraded"]
    missing = [r for r in results if r.status == "missing"]

    logger.info(
        "startup_checks_complete",
        environment=settings.environment,
        ok=len(ok),
        degraded=len(degraded),
        missing=len(missing),
    )
    for r in results:
        log = (
            logger.error
            if r.status == "missing"
            else (logger.warning if r.status == "degraded" else logger.info)
        )
        log(
            "integration_check",
            name=r.name,
            status=r.status,
            detail=r.message,
            **({"hint": r.hint} if r.hint else {}),
        )

    # The console-summary block is a deliberate ergonomic add — JSON
    # logs are great for grep, but the first thing a deployer sees in
    # their terminal is the printed table. Stays single colour because
    # structlog already applies ANSI to the JSON-or-console renderer.
    border = "─" * 72
    print(f"\n  {border}", flush=True)
    print(f"   Startup check · environment={settings.environment}", flush=True)
    print(f"  {border}", flush=True)
    for r in results:
        marker = {"ok": "✓", "degraded": "⚠", "missing": "✗"}[r.status]
        print(f"   {marker}  {r.name:24s}  {r.message}", flush=True)
        if r.hint:
            print(f"      → {r.hint}", flush=True)
    print(f"  {border}\n", flush=True)

    return results
