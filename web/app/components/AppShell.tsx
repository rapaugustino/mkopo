"use client";

import { usePathname } from "next/navigation";
import { BorrowerShell } from "./BorrowerShell";
import { GlobalNav } from "./GlobalNav";

interface Props {
  children: React.ReactNode;
}

/**
 * App shell — routes incoming requests into one of three chrome
 * styles based on the URL:
 *
 *   - **Staff routes** (the default): full underwriter chrome with
 *     brand bar + GlobalNav (Pipeline, Review queue, Eval,
 *     Observability). Anything not enumerated below.
 *
 *   - **Borrower routes** (``/account``, ``/account/*``): the
 *     :class:`BorrowerShell` chrome — same brand bar with auth
 *     chip, NO staff nav. Borrowers should never see the
 *     underwriter-side surface; that would be both confusing UX
 *     and a product-surface leak.
 *
 *   - **Routes with their own layout** (``/apply``, ``/apply/*``):
 *     AppShell stays out of the way; the route's own ``layout.tsx``
 *     handles chrome.
 *
 *   - **Standalone routes** (auth flows like ``/login``,
 *     ``/signup``, ``/forgot-password``, ``/reset-password``,
 *     ``/auth/verify``): pages render full-bleed forms with their
 *     own centred card; no shell wrapper.
 *
 * Lives as a client component because ``usePathname`` requires the
 * client runtime — being client-side has zero perf cost for the
 * shell since the markup itself is static.
 */
export function AppShell({ children }: Props) {
  const pathname = usePathname() ?? "/";

  // Routes whose own ``layout.tsx`` already adds chrome — AppShell
  // must not double-wrap. ``/apply`` is the application wizard.
  const SELF_LAYOUTED = ["/apply"];
  if (
    SELF_LAYOUTED.some((p) => pathname === p || pathname.startsWith(p + "/"))
  ) {
    return <main>{children}</main>;
  }

  // Borrower-facing routes that don't have their own layout.tsx but
  // still need the borrower brand chrome (and absolutely must NOT
  // get the staff GlobalNav).
  const BORROWER_PATHS = ["/account"];
  if (
    BORROWER_PATHS.some((p) => pathname === p || pathname.startsWith(p + "/"))
  ) {
    return (
      <main>
        <BorrowerShell>{children}</BorrowerShell>
      </main>
    );
  }

  // Standalone auth-form pages — login / signup / password recovery
  // / magic-link verify. These pages render their own centred card
  // (logo + form), so we don't want any shell chrome above them.
  const STANDALONE_PATHS = [
    "/login",
    "/signup",
    "/forgot-password",
    "/reset-password",
    "/auth",
  ];
  if (
    STANDALONE_PATHS.some(
      (p) => pathname === p || pathname.startsWith(p + "/"),
    )
  ) {
    return <main>{children}</main>;
  }

  return (
    <>
      {/* Brand bar. The 1px brand-light strip below the nav is the
          app's quietest brand cue: visible only when you look for
          it, but consistent on every screen — that's the kind of
          detail that separates a real product from a vibe. */}
      {/* Nav bar — px-6 (instead of 4) and py-4 (instead of 3) so the
          logo has visual breathing room from the viewport edges. The
          brand-light bottom-edge stroke stays as the quiet brand cue. */}
      <nav
        className="border-b-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-6 py-4"
        style={{
          boxShadow: "inset 0 -1px 0 var(--color-brand-light)",
        }}
      >
        <div className="mx-auto flex max-w-[1440px] items-center gap-6">
          <div className="flex items-center gap-3">
            <div
              className="flex h-8 w-8 items-center justify-center rounded-md text-[13px] font-semibold"
              style={{
                background: "var(--color-brand)",
                color: "var(--color-brand-light)",
                letterSpacing: "-0.04em",
              }}
            >
              MK
            </div>
            <div className="flex items-baseline gap-2">
              <span className="brand-wordmark text-[15px] font-medium">
                Mkopo Lens
              </span>
            </div>
          </div>
          <GlobalNav />
        </div>
      </nav>
      {/* Main content — 1440px max instead of 1280px. Wider monitors
          stop wasting the right edge, narrower monitors still center
          gracefully. ``px-6`` matches the nav for visual alignment. */}
      <main className="mx-auto max-w-[1440px] px-6 py-6">{children}</main>
    </>
  );
}
