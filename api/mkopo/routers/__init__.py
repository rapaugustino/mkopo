"""HTTP routers.

`auth` lives in this package because it provides router-level dependencies
(`CurrentUser`, `require_user`), but it does NOT export a mountable `router` —
import it directly via `from mkopo.routers.auth import ...`.
"""

from mkopo.routers import (
    agents,
    borrower_auth,
    borrower_chat,
    borrower_portal,
    documents,
    evals,
    loans,
    observability,
    parties,
    review,
    staff_chat,
)

__all__ = [
    "agents",
    "borrower_auth",
    "borrower_chat",
    "borrower_portal",
    "documents",
    "evals",
    "loans",
    "observability",
    "parties",
    "review",
    "staff_chat",
]
