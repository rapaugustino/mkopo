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
    user_id: str
    workspace_id: str
    is_admin: bool = False


async def require_user(creds: BearerCredsDep) -> CurrentUser:
    """Resolve the current user from the bearer token.

    Dev mode: accepts the configured DEV_API_TOKEN and returns a fixed identity.
    Production: replace this with real auth (JWT validation, session cookie, etc.).
    """
    settings = get_settings()
    if not creds or not creds.credentials:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Missing token")

    if creds.credentials != settings.dev_api_token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token")

    return CurrentUser(user_id="dev-user", workspace_id="dev-workspace", is_admin=True)
