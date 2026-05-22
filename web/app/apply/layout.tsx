import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "Apply — Mkopo Lens",
  description: "Apply for a commercial loan.",
};

/**
 * Layout for the borrower-facing portal.
 *
 * Deliberately separate from the internal app shell — no underwriter
 * nav, no pipeline link, no review queue. The borrower sees a clean
 * application surface that reads as "talk to your lender's system",
 * not "you're inside the lender's tools."
 *
 * The brand bar is intentionally lighter than the internal one: a
 * mark + a "powered by Mkopo Lens" line, nothing else. We don't want
 * the borrower confused about what they're using; we DO want them to
 * trust that the underwriter is reading the same data on the other
 * side.
 */
export default function BorrowerPortalLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <>
      {/* Override the root layout's max-w-7xl container so the
          borrower form can centre itself in a narrower column —
          forms read better at 640-720px max. */}
      <div className="mx-auto -mt-6 max-w-3xl px-4 pb-12 pt-6">
        <header className="mb-6 flex items-center justify-between">
          <div className="flex items-center gap-2.5">
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
            <div className="flex flex-col leading-tight">
              <span className="brand-wordmark text-[14px] font-medium">
                Mkopo Lens
              </span>
              <span className="text-[10.5px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                Borrower portal
              </span>
            </div>
          </div>
          <a
            href="https://mkopo.io"
            className="text-[11px] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]"
          >
            What is this?
          </a>
        </header>
        {children}
      </div>
    </>
  );
}
