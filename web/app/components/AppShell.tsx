"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { BorrowerShell } from "./BorrowerShell";
import { CommandPalette } from "./CommandPalette";
import { GlobalNav } from "./GlobalNav";
import { UserMenu } from "./UserMenu";

interface Props {
  children: React.ReactNode;
}

/** Detect ``Cmd+K`` (Mac) / ``Ctrl+K`` (Win/Linux). Used to wire the
 *  command palette globally across the staff shell. We intentionally
 *  do NOT capture ``K`` alone — that would conflict with browser
 *  find-in-page and break native text editing. */
function isPaletteShortcut(e: KeyboardEvent): boolean {
  return (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k";
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

  // Command palette state — lives at the shell so the keybinding
  // works on every staff page regardless of which child component
  // currently has focus. Borrower / auth / self-layouted routes
  // don't get the palette (no staff context to search from).
  const [paletteOpen, setPaletteOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (isPaletteShortcut(e)) {
        e.preventDefault();
        setPaletteOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

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
    // Staff console login — distinct from the borrower /login above.
    // Centred-card layout that doesn't want the shell chrome.
    "/staff/login",
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
        className="border-b-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 py-3 sm:px-6 sm:py-4"
        style={{
          boxShadow: "inset 0 -1px 0 var(--color-brand-light)",
        }}
      >
        {/* Brand bar uses a wrap layout on narrow screens — logo +
            nav on the first row, palette trigger on the second. We
            avoid a hamburger because the nav is only five items and
            collapsing to icons-only (see GlobalNav) keeps everything
            tappable without an extra interaction. */}
        <div className="mx-auto flex max-w-[1440px] flex-wrap items-center gap-x-3 gap-y-2 sm:gap-x-6">
          <div className="flex items-center gap-2.5 sm:gap-3">
            <div
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md text-[13px] font-semibold"
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
          {/* Right-aligned Cmd+K trigger. Also makes the shortcut
              discoverable to users who don't know it exists — they
              see the chip, click it, and learn the keybinding from
              the kbd hints inside the modal. On narrow screens we
              keep just the icon-equivalent "Search…" and drop the
              kbd hint, since touch users don't need the shortcut.
              ``flex-1 sm:flex-none`` lets the search chip stretch
              when the brand bar has wrapped, so it stays tappable
              instead of squeezing to a sliver. */}
          <button
            type="button"
            onClick={() => setPaletteOpen(true)}
            className="ml-auto flex flex-1 items-center justify-between gap-2 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] px-2.5 py-1 text-[11.5px] text-[var(--color-text-secondary)] hover:bg-[var(--color-background-primary)] hover:text-[var(--color-text-primary)] sm:flex-none"
            aria-label="Open command palette"
            title="Search loans, borrowers, pages (⌘K)"
          >
            <span>Search…</span>
            <span className="hidden items-center gap-0.5 sm:inline-flex">
              <kbd className="inline-flex h-4 min-w-4 items-center justify-center rounded border border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-1 font-mono text-[10px] text-[var(--color-text-secondary)]">
                ⌘
              </kbd>
              <kbd className="inline-flex h-4 min-w-4 items-center justify-center rounded border border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-1 font-mono text-[10px] text-[var(--color-text-secondary)]">
                K
              </kbd>
            </span>
          </button>
          {/* Signed-in user pill — shows name + role, with a logout
              option in the dropdown. Renders null when there's no
              authed user (the API helper handles the redirect to
              /staff/login). */}
          <UserMenu />
        </div>
      </nav>
      {/* Main content — 1440px max instead of 1280px. Wider monitors
          stop wasting the right edge, narrower monitors still center
          gracefully. ``px-3 sm:px-6`` keeps content edge-padded on
          phones without losing the comfortable padding on desktops. */}
      <main className="mx-auto max-w-[1440px] px-3 py-4 sm:px-6 sm:py-6">
        {children}
      </main>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </>
  );
}
