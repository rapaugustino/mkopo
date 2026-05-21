"""HTTP routers.

`auth` lives in this package because it provides router-level dependencies
(`CurrentUser`, `require_user`), but it does NOT export a mountable `router` —
import it directly via `from mkopo.routers.auth import ...`.
"""

from mkopo.routers import agents, documents, evals, loans, parties, review, webhooks

__all__ = ["agents", "documents", "evals", "loans", "parties", "review", "webhooks"]
