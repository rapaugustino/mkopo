"use client";

import { usePathname } from "next/navigation";
import { GlobalNav } from "./GlobalNav";

interface Props {
  children: React.ReactNode;
}

/**
 * App shell — the brand bar + global nav + content wrapper for the
 * *internal* application (pipeline, loan detail, eval, observability).
 *
 * Hidden on borrower-facing routes (``/apply/*``) so the borrower
 * sees a clean self-service surface without underwriter navigation.
 * The borrower portal renders its own header in
 * ``app/apply/layout.tsx``.
 *
 * Lives as a client component because ``usePathname`` requires the
 * client runtime — and being client-side has zero perf cost for the
 * shell since the underlying brand markup is static.
 */
export function AppShell({ children }: Props) {
  const pathname = usePathname() ?? "/";
  const isBorrowerPortal = pathname.startsWith("/apply");

  if (isBorrowerPortal) {
    // Borrower portal renders its own layout. Just emit the main
    // content slot — no internal-nav chrome.
    return <main>{children}</main>;
  }

  return (
    <>
      {/* Brand bar. The 1px brand-light strip below the nav is the
          app's quietest brand cue: visible only when you look for
          it, but consistent on every screen — that's the kind of
          detail that separates a real product from a vibe. */}
      <nav
        className="border-b-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3"
        style={{
          boxShadow: "inset 0 -1px 0 var(--color-brand-light)",
        }}
      >
        <div className="mx-auto flex max-w-7xl items-center gap-6">
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
              <span className="text-[11px] text-[var(--color-text-tertiary)]">
                AI-first origination
              </span>
            </div>
          </div>
          <GlobalNav />
        </div>
      </nav>
      <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
    </>
  );
}
