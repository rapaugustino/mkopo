"""Auth. Dev token for the portfolio project. Replace with Clerk/Auth0/custom in production."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from mkopo.config import get_settings

bearer_scheme = HTTPBearer(auto_error=False)

BearerCredsDep = Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)]


@dataclass
class CurrentUser:
    """Identity of the staff caller behind the request.

    ``role`` is the canonical RBAC value the tool registry (and any
    other gate that asks "can this user do X?") consumes. Today the
    staff side uses a single dev bearer token so the role is fixed,
    but the dataclass shape is what production-grade auth (Clerk, a
    real ``users`` table lookup, etc.) should return — the consumers
    don't change.

    ``is_admin`` is kept for backwards compatibility with older
    callers that switch on it; new code should branch on ``role``.
    """

    user_id: str
    workspace_id: str
    role: str = "underwriter"
    is_admin: bool = False


async def require_user(creds: BearerCredsDep) -> CurrentUser:
    """Resolve the current user from the bearer token.

    Dev mode: accepts the configured DEV_API_TOKEN and returns a fixed
    identity with ``role="admin"`` so the dev bearer can exercise
    every tool — production should swap this for a real lookup that
    fans out per-user roles. The role is canonical for both the
    staff-chat tool-registry filter (`mkopo.agents.tools.staff`) and
    any other RBAC check downstream.
    """
    settings = get_settings()
    if not creds or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing token")

    if creds.credentials != settings.dev_api_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    return CurrentUser(
        user_id="dev-user",
        workspace_id="dev-workspace",
        role="admin",
        is_admin=True,
    )
