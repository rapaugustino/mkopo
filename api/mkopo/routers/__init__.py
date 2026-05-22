"""HTTP routers.

`auth` lives in this package because it provides router-level dependencies
(`CurrentUser`, `require_user`), but it does NOT export a mountable `router` —
import it directly via `from mkopo.routers.auth import ...`.
"""

from mkopo.routers import (
    agents,
    borrower_portal,
    documents,
    evals,
    loans,
    observability,
    parties,
    review,
    webhooks,
)

__all__ = [
    "agents",
    "borrower_portal",
    "documents",
    "evals",
    "loans",
    "observability",
    "parties",
    "review",
    "webhooks",
]
