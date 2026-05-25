"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import {
  IconActivity,
  IconChartLine,
  IconChevronRight,
  IconFileSearch,
  IconLayoutGrid,
  IconLoader2,
  IconPencil,
  IconUser,
  IconUsers,
} from "@tabler/icons-react";
import { motion, AnimatePresence } from "motion/react";

import { api, type SearchHit } from "@/lib/api";

/**
 * Command palette — the staff-side keyboard surface.
 *
 * Bound to ``Cmd+K`` / ``Ctrl+K`` on every page via AppShell. Opens
 * a centered modal with:
 *
 *   - **Actions** (always visible) — jump to Pipeline / Review queue /
 *     Eval / Observability / Prompts. Visible even with empty query
 *     because the palette doubles as nav for keyboard-first users.
 *   - **Results** (when typing ≥ 2 chars) — loans + parties, each
 *     section ranked server-side and capped at 8.
 *
 * Selection model:
 *   - ``↑/↓`` move the highlighted row
 *   - ``Enter`` navigates to the highlighted row's href
 *   - ``Esc`` closes
 *   - Clicking a row also navigates
 *
 * The list of selectable rows is flat — we render section headers
 * for visual grouping, but ``selectedIndex`` indexes into the
 * concatenated array so up/down stays predictable across sections.
 *
 * Debounce on search input is 120ms — short enough to feel
 * instant, long enough to swallow rapid keystrokes. Loading state
 * lights a tiny spinner so the user knows we're thinking even
 * when the round-trip is fast.
 */

type SelectableRow =
  | { kind: "action"; id: string; label: string; sublabel?: string; href: string; Icon: React.ComponentType<{ size?: number }> }
  | { kind: "loan"; id: string; label: string; sublabel: string | null; href: string }
  | { kind: "party"; id: string; label: string; sublabel: string | null; href: string };

const ACTIONS: Extract<SelectableRow, { kind: "action" }>[] = [
  {
    kind: "action",
    id: "pipeline",
    label: "Pipeline",
    sublabel: "All active loans",
    href: "/",
    Icon: IconLayoutGrid,
  },
  {
    kind: "action",
    id: "review",
    label: "Review queue",
    sublabel: "Extractions waiting on a human",
    href: "/review-queue",
    Icon: IconFileSearch,
  },
  {
    kind: "action",
    id: "eval",
    label: "Eval",
    sublabel: "Drift, calibration, reliability",
    href: "/eval",
    Icon: IconChartLine,
  },
  {
    kind: "action",
    id: "observability",
    label: "Observability",
    sublabel: "LLM calls, agent runs, errors",
    href: "/observability",
    Icon: IconActivity,
  },
  {
    kind: "action",
    id: "prompts",
    label: "Prompts",
    sublabel: "Versioned system prompts",
    href: "/prompts",
    Icon: IconPencil,
  },
];

interface CommandPaletteProps {
  open: boolean;
  onClose: () => void;
}

export function CommandPalette({ open, onClose }: CommandPaletteProps) {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [debouncedQuery, setDebouncedQuery] = useState("");
  const [hits, setHits] = useState<{ loans: SearchHit[]; parties: SearchHit[] } | null>(null);
  const [loading, setLoading] = useState(false);
  const [selectedIndex, setSelectedIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Reset on open. Without this, re-opening the palette would
  // show the previous query and stale results — a small detail
  // but very noticeable when you're using it frequently.
  useEffect(() => {
    if (open) {
      setQuery("");
      setDebouncedQuery("");
      setHits(null);
      setSelectedIndex(0);
      // Defer focus to next tick so the modal is in the DOM.
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  // Debounce the query so we don't fire a request per keystroke.
  // 120ms is short enough to feel instant; 250ms+ feels laggy.
  useEffect(() => {
    if (!open) return;
    const t = setTimeout(() => setDebouncedQuery(query), 120);
    return () => clearTimeout(t);
  }, [query, open]);

  // Fetch on debounced query change. Cancellation flag handles the
  // case where the user keeps typing and a stale request resolves
  // after a newer one — we drop the stale results.
  useEffect(() => {
    if (!open) return;
    if (debouncedQuery.trim().length < 2) {
      setHits(null);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    api
      .search(debouncedQuery)
      .then((res) => {
        if (cancelled) return;
        setHits(res);
        setSelectedIndex(0);
      })
      .catch(() => {
        if (cancelled) return;
        setHits({ loans: [], parties: [] });
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [debouncedQuery, open]);

  // Flat list of selectable rows, in the same order the UI
  // renders them. Actions section is dropped when the user has
  // typed something — they're clearly searching, not navigating.
  const rows: SelectableRow[] = useMemo(() => {
    const out: SelectableRow[] = [];
    const trimmed = debouncedQuery.trim();
    if (trimmed.length < 2) {
      // No query → show actions only.
      out.push(...ACTIONS);
    } else if (hits) {
      // Query → show hits. Actions hide so Enter from the search
      // box always goes to the first matching loan / party.
      for (const h of hits.loans) {
        out.push({
          kind: "loan",
          id: h.id,
          label: h.label,
          sublabel: h.sublabel,
          href: h.href,
        });
      }
      for (const h of hits.parties) {
        out.push({
          kind: "party",
          id: h.id,
          label: h.label,
          sublabel: h.sublabel,
          href: h.href,
        });
      }
    }
    return out;
  }, [hits, debouncedQuery]);

  // Clamp selected index when rows shrink (e.g. delete chars).
  useEffect(() => {
    if (selectedIndex >= rows.length) setSelectedIndex(Math.max(0, rows.length - 1));
  }, [rows, selectedIndex]);

  const handleNavigate = useCallback(
    (row: SelectableRow) => {
      onClose();
      router.push(row.href);
    },
    [onClose, router],
  );

  // Global keybindings while the modal is open. Up/down move the
  // cursor; Enter activates; Esc closes. We handle these on the
  // window so the input's caret-movement defaults don't fight us.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setSelectedIndex((i) => Math.min(rows.length - 1, i + 1));
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        setSelectedIndex((i) => Math.max(0, i - 1));
        return;
      }
      if (e.key === "Enter") {
        e.preventDefault();
        const row = rows[selectedIndex];
        if (row) handleNavigate(row);
        return;
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, rows, selectedIndex, handleNavigate, onClose]);

  if (!open) return null;

  const showingResults = debouncedQuery.trim().length >= 2;
  const hasAnyHits =
    !!hits && (hits.loans.length > 0 || hits.parties.length > 0);

  // Compute the running flat index per row so we can highlight the
  // right one. We render in two passes (actions OR loans + parties);
  // the running counter keeps the selection model honest.
  let flatIdx = -1;
  const nextIdx = () => {
    flatIdx += 1;
    return flatIdx;
  };

  return (
    <AnimatePresence>
      <div
        className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[18vh]"
        style={{ background: "rgba(0,0,0,0.45)" }}
        onClick={onClose}
      >
        <motion.div
          initial={{ opacity: 0, scale: 0.98, y: -8 }}
          animate={{ opacity: 1, scale: 1, y: 0 }}
          exit={{ opacity: 0, scale: 0.98, y: -8 }}
          transition={{ duration: 0.12 }}
          className="relative flex w-full max-w-xl flex-col overflow-hidden rounded-xl border border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] shadow-2xl"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Input row */}
          <div className="flex items-center gap-2.5 border-b border-[var(--color-border-tertiary)] px-4 py-3">
            <input
              ref={inputRef}
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search loans, borrowers, guarantors… or jump to a page"
              className="flex-1 bg-transparent text-[13.5px] text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus:outline-none"
              autoComplete="off"
              spellCheck={false}
            />
            {loading ? (
              <IconLoader2
                size={14}
                className="animate-spin text-[var(--color-text-tertiary)]"
              />
            ) : (
              <span className="rounded border border-[var(--color-border-tertiary)] px-1.5 py-0.5 text-[10px] font-medium text-[var(--color-text-tertiary)]">
                Esc
              </span>
            )}
          </div>

          {/* Results body */}
          <div className="max-h-[60vh] overflow-y-auto py-1.5">
            {!showingResults && (
              <>
                <SectionHeader>Jump to</SectionHeader>
                {ACTIONS.map((a) => {
                  const idx = nextIdx();
                  return (
                    <Row
                      key={a.id}
                      selected={idx === selectedIndex}
                      onHover={() => setSelectedIndex(idx)}
                      onClick={() => handleNavigate(a)}
                      Icon={a.Icon}
                      label={a.label}
                      sublabel={a.sublabel}
                    />
                  );
                })}
              </>
            )}

            {showingResults && hits && (
              <>
                {hits.loans.length > 0 && (
                  <>
                    <SectionHeader>Loans</SectionHeader>
                    {hits.loans.map((h) => {
                      const idx = nextIdx();
                      return (
                        <Row
                          key={`loan-${h.id}`}
                          selected={idx === selectedIndex}
                          onHover={() => setSelectedIndex(idx)}
                          onClick={() =>
                            handleNavigate({
                              kind: "loan",
                              id: h.id,
                              label: h.label,
                              sublabel: h.sublabel,
                              href: h.href,
                            })
                          }
                          Icon={IconLayoutGrid}
                          label={h.label}
                          sublabel={h.sublabel ?? undefined}
                        />
                      );
                    })}
                  </>
                )}
                {hits.parties.length > 0 && (
                  <>
                    <SectionHeader>Borrowers &amp; guarantors</SectionHeader>
                    {hits.parties.map((h) => {
                      const idx = nextIdx();
                      const Icon =
                        (h.sublabel ?? "").toLowerCase() === "entity"
                          ? IconUsers
                          : IconUser;
                      return (
                        <Row
                          key={`party-${h.id}`}
                          selected={idx === selectedIndex}
                          onHover={() => setSelectedIndex(idx)}
                          onClick={() =>
                            handleNavigate({
                              kind: "party",
                              id: h.id,
                              label: h.label,
                              sublabel: h.sublabel,
                              href: h.href,
                            })
                          }
                          Icon={Icon}
                          label={h.label}
                          sublabel={h.sublabel ?? undefined}
                        />
                      );
                    })}
                  </>
                )}
                {!hasAnyHits && !loading && (
                  <div className="px-4 py-6 text-center text-[12.5px] text-[var(--color-text-tertiary)]">
                    Nothing matches “{debouncedQuery}”.
                  </div>
                )}
              </>
            )}
          </div>

          {/* Footer hint row */}
          <div className="flex items-center justify-between gap-3 border-t border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] px-3 py-2 text-[10.5px] text-[var(--color-text-tertiary)]">
            <span>
              <Kbd>↑</Kbd> <Kbd>↓</Kbd> to move · <Kbd>↵</Kbd> to open
            </span>
            <span>
              <Kbd>⌘</Kbd>
              <Kbd>K</Kbd> from anywhere
            </span>
          </div>
        </motion.div>
      </div>
    </AnimatePresence>
  );
}

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <p className="px-4 pt-2.5 pb-1 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
      {children}
    </p>
  );
}

function Row({
  selected,
  onHover,
  onClick,
  Icon,
  label,
  sublabel,
}: {
  selected: boolean;
  onHover: () => void;
  onClick: () => void;
  Icon: React.ComponentType<{ size?: number }>;
  label: string;
  sublabel?: string;
}) {
  return (
    <button
      type="button"
      onMouseEnter={onHover}
      onClick={onClick}
      className="flex w-full items-center gap-3 px-4 py-2 text-left"
      style={{
        background: selected ? "var(--color-background-secondary)" : "transparent",
      }}
    >
      <span
        className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-md"
        style={{
          background: selected
            ? "var(--color-background-primary)"
            : "var(--color-background-secondary)",
          color: selected
            ? "var(--color-text-primary)"
            : "var(--color-text-secondary)",
        }}
      >
        <Icon size={13} />
      </span>
      <span className="flex min-w-0 flex-1 items-baseline gap-2">
        <span className="truncate text-[12.5px] font-medium text-[var(--color-text-primary)]">
          {label}
        </span>
        {sublabel && (
          <span className="truncate text-[11.5px] text-[var(--color-text-tertiary)]">
            {sublabel}
          </span>
        )}
      </span>
      {selected && (
        <IconChevronRight
          size={12}
          className="shrink-0 text-[var(--color-text-tertiary)]"
        />
      )}
    </button>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return (
    <kbd className="inline-flex h-4 min-w-4 items-center justify-center rounded border border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-1 font-mono text-[10px] text-[var(--color-text-secondary)]">
      {children}
    </kbd>
  );
}
