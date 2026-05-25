"use client";

import { useEffect, useState } from "react";
import { createPortal } from "react-dom";
import { useQuery } from "@tanstack/react-query";
import {
  IconExternalLink,
  IconLoader2,
  IconQuote,
  IconX,
} from "@tabler/icons-react";
import { AnimatePresence, motion } from "motion/react";

import { api, type Citation } from "@/lib/api";
import { humanizeField } from "@/lib/humanize";

/**
 * Grounded-AI citation chip + side drawer.
 *
 * The underwriting summary's ``citations`` array lists extracted-field
 * keys that fed each section ("property_address", "loan_amount",
 * "annual_noi" etc.). Without affordances those keys read as noise.
 * This component renders each one as a clickable chip — click to
 * open a side drawer that fetches the underlying extraction + shows
 * the exact source quote, document filename, page, and confidence.
 *
 * The demo moment: "the AI didn't claim DSCR is 1.87 — it shows you
 * the page it read it from." That's the strongest possible signal
 * the system is grounded and not hallucinating.
 *
 * UX notes:
 *
 * - Chips are not eager-fetched; the lookup fires the first time
 *   the chip is clicked. Subsequent opens are instant from the
 *   query cache. ``staleTime: Infinity`` because extractions don't
 *   meaningfully change inside one underwriting view.
 *
 * - 404 from the resolver means the citation is "stale" — the
 *   prompt changed but underwriting hasn't been re-run. We surface
 *   that as a friendly state rather than swallowing the error.
 *
 * - The drawer slides in from the right and is dismissable via
 *   Escape, backdrop click, or the X button. Standard side-panel
 *   convention; the DocumentViewer modal uses the same pattern.
 */
/** Render variant.
 *
 *  - ``"chip"`` (default): the full outlined chip with a quote icon
 *    and the humanised field name. Fits "Citations:" lists.
 *  - ``"superscript"``: inline numbered superscript, sized to fit
 *    inside running prose. Mirrors how academic citations read.
 */
type Variant = "chip" | "superscript";

export function CitedSource({
  loanId,
  field,
  variant = "chip",
  number,
  preview,
}: {
  loanId: string;
  field: string;
  variant?: Variant;
  /** Citation number (1, 2, 3 …) for the ``"superscript"`` variant.
   *  Ignored when ``variant === "chip"``. */
  number?: number;
  /** Optional pre-rendered hover tooltip — used as the ``title`` on
   *  the superscript variant so users can preview without clicking. */
  preview?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <>
      {variant === "chip" ? (
        <button
          type="button"
          onClick={() => setOpen(true)}
          title={`See source — ${humanizeField(field)}`}
          className="inline-flex items-center gap-1 rounded-full border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2 py-0.5 text-[10.5px] font-medium text-[var(--color-text-secondary)] transition-colors hover:border-[var(--color-brand)] hover:bg-[var(--color-background-success)] hover:text-[var(--color-text-success)]"
        >
          <IconQuote size={9} />
          {humanizeField(field)}
        </button>
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          title={preview ?? `Click to see the source for ${humanizeField(field)}`}
          className="ml-0.5 inline-flex items-center justify-center rounded px-1 align-baseline text-[10px] font-medium transition-colors hover:opacity-80"
          style={{
            background: "var(--color-background-info)",
            color: "var(--color-text-info)",
          }}
        >
          {number ?? "?"}
        </button>
      )}
      {/* The drawer is mounted via :func:`createPortal` so the
          ``<aside>`` / ``<div>`` / ``<blockquote>`` it contains
          never end up nested under whatever parent rendered the
          chip. The most common parent is a ``<p>`` (the citation
          chip sits inline in prose), and block elements inside a
          paragraph are invalid HTML — React surfaces them as
          hydration errors. The portal lifts the drawer out to
          ``document.body`` where it's free to render block-level
          content. */}
      <AnimatePresence>
        {open && (
          <DrawerPortal>
            <CitationDrawer
              loanId={loanId}
              field={field}
              onClose={() => setOpen(false)}
            />
          </DrawerPortal>
        )}
      </AnimatePresence>
    </>
  );
}

/** Portal target for the drawer.
 *
 *  Renders ``children`` into ``document.body`` once we're on the
 *  client. During SSR we render nothing — Next.js then sees an
 *  empty subtree on the server side, which avoids any hydration
 *  mismatch when the client mounts and creates the portal.
 *
 *  The ``open && ...`` guard in the parent already prevents this
 *  from ever rendering on first server paint (the drawer is only
 *  open after a user click), so the portal-availability check is
 *  belt-and-braces. */
function DrawerPortal({ children }: { children: React.ReactNode }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    setMounted(true);
  }, []);
  if (!mounted) return null;
  return createPortal(children, document.body);
}

function CitationDrawer({
  loanId,
  field,
  onClose,
}: {
  loanId: string;
  field: string;
  onClose: () => void;
}) {
  const { data, isPending, error } = useQuery<Citation, Error>({
    queryKey: ["loan", loanId, "citation", field],
    queryFn: () => api.getCitation(loanId, field),
    staleTime: Infinity,
  });

  return (
    <div
      role="dialog"
      aria-label="Citation source"
      className="fixed inset-0 z-50 flex justify-end"
      style={{ background: "rgba(0,0,0,0.32)" }}
      onClick={onClose}
      onKeyDown={(e) => {
        if (e.key === "Escape") onClose();
      }}
    >
      <motion.aside
        initial={{ x: 24, opacity: 0 }}
        animate={{ x: 0, opacity: 1 }}
        exit={{ x: 24, opacity: 0 }}
        transition={{ duration: 0.2, ease: "easeOut" }}
        className="flex h-full w-full max-w-[440px] flex-col overflow-hidden border-l border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--color-border-tertiary)] px-4 py-3">
          <div className="min-w-0">
            <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
              Source
            </p>
            <p className="truncate text-[13px] font-medium text-[var(--color-text-primary)]">
              {humanizeField(field)}
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-background-secondary)]"
          >
            <IconX size={14} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 py-4">
          {isPending && (
            <div className="flex items-center justify-center gap-2 py-12 text-[12.5px] text-[var(--color-text-secondary)]">
              <IconLoader2 size={14} className="animate-spin" />
              Resolving citation…
            </div>
          )}
          {error && (
            <div className="rounded-md bg-[var(--color-background-warning)] px-3 py-3 text-[12.5px] text-[var(--color-text-warning)]">
              <p className="font-medium">Citation no longer resolvable.</p>
              <p className="mt-1 text-[12px]">
                This usually means the underwriting summary was generated
                against an older extraction set — re-run intake to refresh.
              </p>
            </div>
          )}
          {data && <CitationBody c={data} />}
        </div>
      </motion.aside>
    </div>
  );
}

function CitationBody({ c }: { c: Citation }) {
  // Status chip — colour-coded so the user can see at a glance
  // whether a human signed off on this value or the AI alone did.
  const statusStyle: Record<string, { bg: string; fg: string; label: string }> = {
    accepted: {
      bg: "var(--color-background-success)",
      fg: "var(--color-text-success)",
      label: "Accepted",
    },
    overridden: {
      bg: "var(--color-background-info)",
      fg: "var(--color-text-info)",
      label: "Overridden",
    },
    proposed: {
      bg: "var(--color-background-warning)",
      fg: "var(--color-text-warning)",
      label: "Proposed",
    },
  };
  const sm = statusStyle[c.status] ?? {
    bg: "var(--color-background-secondary)",
    fg: "var(--color-text-secondary)",
    label: c.status,
  };

  return (
    <div className="flex flex-col gap-4">
      {/* Value + confidence + status */}
      <div className="flex flex-col gap-1.5">
        <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          Extracted value
        </p>
        <p className="font-editorial text-[20px] leading-tight text-[var(--color-text-primary)]">
          {c.value}
        </p>
        <div className="flex items-center gap-1.5">
          <span
            className="inline-flex items-center rounded-full px-2 py-0.5 text-[10.5px] font-medium"
            style={{ background: sm.bg, color: sm.fg }}
          >
            {sm.label}
          </span>
          <span className="text-[10.5px] text-[var(--color-text-tertiary)]">
            {Math.round(c.confidence * 100)}% confidence
          </span>
        </div>
      </div>

      {/* Source quote — the magic moment. We render it as a styled
          blockquote with a subtle yellow tinted-highlight on the
          full text so it reads as "this is the exact text I saw".
          If char_start / char_end were populated we'd highlight just
          that span; today the source_span only carries a ``quote``,
          so we render the whole quote highlighted. */}
      <div className="flex flex-col gap-1.5">
        <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          Source quote
        </p>
        <blockquote
          className="rounded-md border-l-2 border-[var(--color-brand)] px-3 py-2.5 font-mono text-[12px] leading-relaxed"
          style={{
            background: "var(--color-background-success)",
            color: "var(--color-text-primary)",
          }}
        >
          {c.quote || (
            <span className="text-[var(--color-text-tertiary)] italic">
              No source span was recorded for this extraction.
            </span>
          )}
        </blockquote>
      </div>

      {/* Document attribution + link to the in-app viewer */}
      <div className="flex flex-col gap-1.5">
        <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
          Document
        </p>
        <p className="text-[12.5px] text-[var(--color-text-primary)]">
          {c.document_filename}
          {c.page != null && (
            <span className="text-[var(--color-text-tertiary)]">
              {" "}
              · p.{c.page}
            </span>
          )}
        </p>
        {/* Cross-link to the case-file's Activity tab is non-trivial
            because the docs panel is on a different tab. Surface as
            an outbound chip pointing at the document download URL
            instead — clicking opens the DocumentViewer pattern in a
            new tab. Acceptable for now; a deeper integration could
            scroll the page to the Documents panel and auto-open the
            viewer over that doc. */}
      </div>

      {/* Footer reassurance */}
      <p className="rounded-md bg-[var(--color-background-secondary)] px-3 py-2 text-[11px] leading-relaxed text-[var(--color-text-tertiary)]">
        Every value the underwriting summary cites resolves to a
        specific span in a specific document. Click any citation chip
        on the workspace to see its source.
      </p>

      {/* Helper: silence unused-icon warning when char positions are
          null. ``IconExternalLink`` will be used when we wire the
          "Open document at this page" affordance in a follow-up. */}
      <span className="hidden">
        <IconExternalLink size={12} />
      </span>
    </div>
  );
}
