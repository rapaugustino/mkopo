"""Shared FastAPI dependencies.

Define each dependency once as an `Annotated[T, Depends(...)]` alias and
import it everywhere — keeps routers tidy and means a dependency's wiring
changes in exactly one place.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from mkopo.db import get_db
from mkopo.routers.auth import CurrentUser, require_user

# A logged-in caller (dev: bearer-token auth; production: swap require_user).
CurrentUserDep = Annotated[CurrentUser, Depends(require_user)]

# A scoped, request-bound async SQLAlchemy session.
DbSessionDep = Annotated[AsyncSession, Depends(get_db)]
