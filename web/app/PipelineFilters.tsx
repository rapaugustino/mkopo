"use client";

import { useEffect, useRef, useState } from "react";
import {
  IconCheck,
  IconFilter,
  IconSearch,
  IconX,
} from "@tabler/icons-react";
import { AnimatePresence, motion } from "motion/react";
import type { LoanStage, Owner, RiskBand } from "@/lib/api";
import { humanizeStage } from "@/lib/humanize";
import { SecondaryButton } from "@/app/components/SecondaryButton";

const STAGE_OPTIONS: LoanStage[] = [
  "intake",
  "underwriting",
  "decision",
  "conditions",
  "closing",
  "approved",
  "servicing",
  "declined",
];

const RISK_OPTIONS: { value: RiskBand; label: string }[] = [
  { value: "low", label: "Low" },
  { value: "med", label: "Med" },
  { value: "high", label: "High" },
];

export interface PipelineFilterState {
  search: string;
  stages: Set<LoanStage>;
  risks: Set<RiskBand>;
  ownerIds: Set<string>;
}

/** Empty filter state — every collection is empty, no search query. */
export const EMPTY_FILTERS: PipelineFilterState = {
  search: "",
  stages: new Set(),
  risks: new Set(),
  ownerIds: new Set(),
};

interface Props {
  /** Distinct owners across the loaded loan set — feeds the owner dropdown. */
  owners: Owner[];
  value: PipelineFilterState;
  onChange: (next: PipelineFilterState) => void;
}

/**
 * Search input + Filter popover for the pipeline table.
 *
 * Why a single popover for all four facets rather than four chip
 * dropdowns: the chip pattern looks cluttered when most days no
 * filter is set, and clicking through three dropdowns to scope a
 * triage view feels slow. The popover collapses the controls when
 * idle and feels like one decision when open.
 *
 * The whole filter state lives in the parent (the pipeline page),
 * which keeps the filter logic and the table render in the same
 * place. Query-param syncing would be a separate, optional pass.
 */
export function PipelineFilters({ owners, value, onChange }: Props) {
  const [open, setOpen] = useState(false);
  const popoverRef = useRef<HTMLDivElement | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  // Close on outside click — the popover is non-modal so we don't
  // want to trap focus, just hide cleanly.
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      const t = e.target as Node;
      if (popoverRef.current?.contains(t) || triggerRef.current?.contains(t)) {
        return;
      }
      setOpen(false);
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open]);

  const activeCount =
    (value.search ? 1 : 0) +
    value.stages.size +
    value.risks.size +
    value.ownerIds.size;

  // Toggle helpers — immutable updates so React notices.
  const toggleStage = (s: LoanStage) => {
    const next = new Set(value.stages);
    next.has(s) ? next.delete(s) : next.add(s);
    onChange({ ...value, stages: next });
  };
  const toggleRisk = (r: RiskBand) => {
    const next = new Set(value.risks);
    next.has(r) ? next.delete(r) : next.add(r);
    onChange({ ...value, risks: next });
  };
  const toggleOwner = (id: string) => {
    const next = new Set(value.ownerIds);
    next.has(id) ? next.delete(id) : next.add(id);
    onChange({ ...value, ownerIds: next });
  };

  const clearAll = () => onChange(EMPTY_FILTERS);

  return (
    <div className="flex items-center gap-2">
      <SearchInput
        value={value.search}
        onChange={(s) => onChange({ ...value, search: s })}
      />

      <div className="relative">
        <button
          ref={triggerRef}
          onClick={() => setOpen((v) => !v)}
          className="flex items-center gap-1.5 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2.5 py-1.5 text-[12px] text-[var(--color-text-primary)] hover:bg-[var(--color-background-secondary)]"
        >
          <IconFilter size={13} />
          Filter
          {activeCount > 0 && (
            <span
              className="rounded-full px-1.5 text-[10px] font-medium leading-4"
              style={{
                background: "var(--color-brand)",
                color: "var(--color-brand-light)",
              }}
            >
              {activeCount}
            </span>
          )}
        </button>

        <AnimatePresence>
          {open && (
            <motion.div
              ref={popoverRef}
              role="dialog"
              aria-label="Filter loans"
              initial={{ opacity: 0, y: -4, scale: 0.99 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={{ opacity: 0, y: -4, scale: 0.99 }}
              transition={{ duration: 0.14, ease: "easeOut" }}
              className="absolute right-0 z-30 mt-1 w-[300px] rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] shadow-lg"
              style={{
                boxShadow:
                  "0 8px 24px -8px rgba(60, 60, 56, 0.18), 0 1px 0 rgba(60, 60, 56, 0.04)",
              }}
            >
              <header className="flex items-center justify-between border-b-[0.5px] border-[var(--color-border-tertiary)] px-3 py-2">
                <p className="text-[12px] font-medium">Filter loans</p>
                <button
                  onClick={clearAll}
                  disabled={activeCount === 0}
                  className="text-[11px] text-[var(--color-text-info)] disabled:text-[var(--color-text-tertiary)]"
                >
                  Clear all
                </button>
              </header>

              <div className="flex flex-col gap-3 px-3 py-3">
                <FilterGroup label="Stage">
                  <div className="flex flex-wrap gap-1">
                    {STAGE_OPTIONS.map((s) => (
                      <ChipToggle
                        key={s}
                        active={value.stages.has(s)}
                        onClick={() => toggleStage(s)}
                      >
                        {humanizeStage(s)}
                      </ChipToggle>
                    ))}
                  </div>
                </FilterGroup>

                <FilterGroup label="Risk band">
                  <div className="flex gap-1">
                    {RISK_OPTIONS.map((r) => (
                      <ChipToggle
                        key={r.value}
                        active={value.risks.has(r.value)}
                        onClick={() => toggleRisk(r.value)}
                      >
                        {r.label}
                      </ChipToggle>
                    ))}
                  </div>
                </FilterGroup>

                {owners.length > 0 && (
                  <FilterGroup label="Owner">
                    <div className="flex flex-col gap-0.5">
                      {owners.map((o) => {
                        const checked = value.ownerIds.has(o.id);
                        return (
                          <button
                            key={o.id}
                            onClick={() => toggleOwner(o.id)}
                            className="flex items-center justify-between rounded-md px-2 py-1.5 text-[12px] hover:bg-[var(--color-background-secondary)]"
                          >
                            <span className="flex items-center gap-2">
                              <span className="inline-flex h-5 w-5 items-center justify-center rounded-full bg-[var(--color-background-secondary)] text-[10px] font-medium text-[var(--color-text-secondary)]">
                                {o.initials}
                              </span>
                              {o.name}
                            </span>
                            {checked && (
                              <IconCheck
                                size={13}
                                style={{ color: "var(--color-brand)" }}
                              />
                            )}
                          </button>
                        );
                      })}
                    </div>
                  </FilterGroup>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>

      {/* The "active filters" strip is rendered by the parent. Keeping
          this control narrow lets the parent decide whether to render
          chips below the table header, in the filter bar, or both. */}
    </div>
  );
}

// ---- Sub-components -------------------------------------------------------

function SearchInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="flex items-center gap-1.5 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2 py-1.5 focus-within:border-[var(--color-brand)]">
      <IconSearch size={13} className="text-[var(--color-text-tertiary)]" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder="Search loan, borrower, address…"
        className="w-44 bg-transparent text-[12px] outline-none placeholder:text-[var(--color-text-tertiary)]"
      />
      {value && (
        <button
          onClick={() => onChange("")}
          aria-label="Clear search"
          className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]"
        >
          <IconX size={12} />
        </button>
      )}
    </div>
  );
}

function FilterGroup({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <p className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
        {label}
      </p>
      {children}
    </div>
  );
}

function ChipToggle({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className="rounded-md px-2 py-1 text-[11px] font-medium transition-colors"
      style={{
        background: active
          ? "var(--color-background-success)"
          : "var(--color-background-secondary)",
        color: active ? "var(--color-brand)" : "var(--color-text-secondary)",
        border: active
          ? "0.5px solid var(--color-brand)"
          : "0.5px solid transparent",
      }}
    >
      {children}
    </button>
  );
}

// Suppress unused SecondaryButton (only used in storybook variants of this
// component); keeping the import makes adding a "Reset" button later trivial.
void SecondaryButton;
