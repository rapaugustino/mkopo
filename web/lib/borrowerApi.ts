/**
 * Borrower-side API client.
 *
 * Distinct from ``lib/api.ts`` (the staff/internal client) for two reasons:
 *
 *  1. **Different auth.** Borrowers authenticate via the
 *     ``mkopo_session`` httpOnly cookie set by the backend on signup
 *     / login. The bearer dev-token used by ``lib/api.ts`` is a
 *     staff credential and must not leak onto borrower fetches.
 *
 *  2. **``credentials: 'include'``** is essential here — the
 *     session cookie only ships across an origin boundary if we
 *     ask for it explicitly. Without this flag the borrower would
 *     authenticate once and then be 401'd on every subsequent
 *     fetch, which is a fun thing to debug at 2am.
 *
 * Same-site note: localhost:3000 ↔ localhost:8000 are *same-site*
 * (the registrable domain is ``localhost``) so ``SameSite=Lax``
 * cookies ride across the port boundary. In production the
 * frontend and API should share a registrable domain
 * (``app.mkopo.com`` ↔ ``api.mkopo.com``) for the same reason.
 */

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// ---- types --------------------------------------------------------------

export interface BorrowerUser {
  id: string;
  email: string;
  name: string;
  role: string;
  email_verified_at: string | null;
}

/** One row of the signed-in borrower's loan list. Powers the
 *  /account dashboard. */
export interface MyLoanRow {
  loan_id: string;
  reference: string;
  stage: string;
  loan_type: string;
  loan_class: string;
  amount: string;
  submitted_at: string;
  next_step: string;
}

export interface MagicLinkIssued {
  ok: boolean;
  /** Dev-mode convenience: the magic-link URL is included in the
   *  response so tests don't need a real mailbox. ``null`` in
   *  production — there the link only ships via email. */
  magic_link_url: string | null;
}

export interface ApiError {
  status: number;
  message: string;
}

// ---- fetch helper -------------------------------------------------------

/**
 * One small fetch wrapper for the borrower side. All requests are
 * cookie-authed (no Authorization header) and surface a typed
 * ApiError on non-2xx responses so callers can branch on status.
 */
async function bfetch<T>(
  path: string,
  init?: RequestInit,
): Promise<T> {
  const res = await fetch(`${API_URL}/api/v1${path}`, {
    ...init,
    credentials: "include",
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!res.ok) {
    let message = res.statusText;
    try {
      const body = (await res.json()) as { detail?: string };
      if (body?.detail) message = body.detail;
    } catch {
      // Body wasn't JSON; keep the statusText fallback.
    }
    const err: ApiError = { status: res.status, message };
    throw err;
  }
  // 204 No Content has no body — return null cast as T so callers
  // can treat all responses uniformly.
  if (res.status === 204) return null as T;
  return (await res.json()) as T;
}

// ---- auth endpoints -----------------------------------------------------

export const borrowerAuthApi = {
  /**
   * Create a borrower account. Returns the new user; the session
   * cookie is set by the backend on the response, so subsequent
   * calls are authenticated automatically.
   */
  signup: (input: { email: string; password: string; name?: string }) =>
    bfetch<BorrowerUser>(`/borrower-auth/signup`, {
      method: "POST",
      body: JSON.stringify(input),
    }),

  /**
   * Email + password login. Throws ApiError(401) on bad creds —
   * the message is generic to avoid email-enumeration leaks.
   */
  login: (input: { email: string; password: string }) =>
    bfetch<BorrowerUser>(`/borrower-auth/login`, {
      method: "POST",
      body: JSON.stringify(input),
    }),

  /**
   * Send a magic-link email. The backend always returns ``ok: true``
   * regardless of whether the email is on file (anti-enumeration).
   * In dev, ``magic_link_url`` is non-null so the test can follow it.
   */
  requestMagicLink: (email: string) =>
    bfetch<MagicLinkIssued>(`/borrower-auth/magic-link/request`, {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  /** Consume a magic-link token (login or email-verify purpose). */
  consumeMagicLink: (token: string) =>
    bfetch<BorrowerUser>(`/borrower-auth/magic-link/consume`, {
      method: "POST",
      body: JSON.stringify({ token }),
    }),

  /** Send a password-reset link. */
  requestPasswordReset: (email: string) =>
    bfetch<MagicLinkIssued>(`/borrower-auth/password-reset/request`, {
      method: "POST",
      body: JSON.stringify({ email }),
    }),

  /** Finalise a password reset. New password takes effect immediately;
   *  the user is signed in via session cookie on success. */
  confirmPasswordReset: (input: { token: string; new_password: string }) =>
    bfetch<BorrowerUser>(`/borrower-auth/password-reset/confirm`, {
      method: "POST",
      body: JSON.stringify(input),
    }),

  /** Clear the session cookie. Always succeeds. */
  logout: () =>
    bfetch<null>(`/borrower-auth/logout`, {
      method: "POST",
    }),

  /** Return the signed-in borrower, or throw ApiError(401) if not. */
  me: () => bfetch<BorrowerUser>(`/borrower-auth/me`),

  /** List my loans — the borrower's launchpad after login. */
  myLoans: () => bfetch<MyLoanRow[]>(`/borrower-auth/me/loans`),

  /** Update typo-class contact fields (name only for now). Anything
   *  that affects underwriting goes through {@link updateLoanFields}. */
  updateContact: (input: { name?: string }) =>
    bfetch<BorrowerUser>(`/borrower-auth/me/contact`, {
      method: "PATCH",
      body: JSON.stringify(input),
    }),

  /** Mint a fresh-auth challenge token. Verifies the current
   *  password and returns a one-shot token the UI must echo back on
   *  the next sensitive request (withdraw / erasure). 5-minute TTL.
   *
   *  Required because a stolen session cookie shouldn't be enough to
   *  trigger an irreversible action — see #169. */
  mintChallenge: (password: string) =>
    bfetch<{ token: string; expires_in_seconds: number }>(
      `/borrower-auth/me/challenge`,
      { method: "POST", body: JSON.stringify({ password }) },
    ),

  /** Withdraw an in-flight application. Terminal — no undo.
   *  Requires a fresh-auth challenge token; obtain via mintChallenge. */
  withdrawLoan: (loanId: string, reason: string, challengeToken: string) =>
    bfetch<MyLoanRow>(`/borrower-auth/me/loans/${loanId}/withdraw`, {
      method: "POST",
      body: JSON.stringify({ reason, challenge_token: challengeToken }),
    }),

  /** Edit borrower-supplied loan fields (income, employer, etc.).
   *  Whitelisted server-side; post-decision edits drift the
   *  materials hash and force a re-underwriting. */
  updateLoanFields: (
    loanId: string,
    patch: {
      annual_income?: number | null;
      monthly_debt_payments?: number | null;
      employer?: string | null;
      credit_score?: number | null;
      years_employment?: number | null;
      purpose?: string | null;
    },
  ) =>
    bfetch<{ changed: string[]; diff?: Record<string, unknown>; message?: string }>(
      `/borrower-auth/me/loans/${loanId}/fields`,
      { method: "PATCH", body: JSON.stringify(patch) },
    ),

  /** Mint a short-lived presigned download URL for one of the
   *  borrower's documents. The returned URL fetches the raw bytes
   *  directly from object storage and expires after a few minutes.
   *  An audit ``document_accessed`` event is recorded server-side. */
  getDocumentDownloadUrl: (loanId: string, documentId: string) =>
    bfetch<{
      url: string;
      filename: string;
      content_type: string;
      expires_in_seconds: number;
    }>(
      `/borrower-auth/me/loans/${loanId}/documents/${documentId}/download-url`,
    ),

  /** Download a DSAR-style JSON dump of everything we hold. */
  exportMyData: () =>
    bfetch<Record<string, unknown>>(`/borrower-auth/me/data/export`),

  /** Soft-delete account + all loans. Retention window applies; the
   *  user is signed out immediately. */
  requestErasure: (input: {
    reason: string;
    confirm: boolean;
    challenge_token: string;
  }) =>
    bfetch<{
      ok: boolean;
      loans_affected: number;
      retention_until_max: string | null;
      message: string;
    }>(`/borrower-auth/me/erasure`, {
      method: "POST",
      body: JSON.stringify(input),
    }),
};
