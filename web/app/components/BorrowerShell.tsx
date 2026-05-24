import { BorrowerHeaderChip } from "@/app/apply/BorrowerHeaderChip";

/**
 * Borrower-facing chrome — the brand bar, a single source of truth
 * for the auth chip, and a narrow centred content column.
 *
 * Used by:
 *   - ``/apply/layout.tsx`` — borrower application wizard + status pages
 *   - ``AppShell`` — wraps every other borrower route (``/account`` and
 *     its subpaths) since those don't have their own layout file
 *
 * Deliberately separate from the staff ``AppShell``: borrowers
 * should not see the underwriter-side navigation (Pipeline / Review
 * Queue / Eval / Observability). Surfacing that to a borrower would
 * be both confusing UX and a product-surface leak.
 *
 * Width: 3xl (768px) by default — borrower content is form-flavoured
 * (applications, dashboards with a list of loans, privacy controls)
 * and reads best at a centred narrow column.
 */
export function BorrowerShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="mx-auto max-w-3xl px-4 pt-10 pb-12">
      <header className="mb-6 flex items-center justify-between gap-3">
        <div className="flex items-center gap-2.5">
          <div
            className="flex h-9 w-9 items-center justify-center rounded-md text-[13px] font-semibold"
            style={{
              background: "var(--color-brand)",
              color: "var(--color-brand-light)",
              letterSpacing: "-0.04em",
            }}
          >
            MK
          </div>
          <div className="flex flex-col leading-tight">
            <span className="brand-wordmark text-[14.5px] font-medium">
              Mkopo Lens
            </span>
            <span className="text-[10.5px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
              Borrower portal
            </span>
          </div>
        </div>
        <BorrowerHeaderChip />
      </header>
      {children}
    </div>
  );
}
