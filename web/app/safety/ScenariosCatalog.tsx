"use client";

/**
 * Safety scenarios catalog — renders the static manifest from
 * GET /safety/scenarios as filterable cards.
 *
 * Each card describes one robustness property the system pins:
 * what an attacker would try, what stops them, the test that
 * verifies it. This is the "how the app stays safe" surface a
 * staff user, auditor, or prospective adopter can browse without
 * reading any code.
 *
 * Filtering: by category + severity. Known-gaps render in a
 * separate section below the protected list, intentionally
 * called out so the demo's honesty about scope shows through.
 */

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  IconAlertTriangle,
  IconLock,
  IconShieldCheck,
  IconShieldX,
} from "@tabler/icons-react";
import {
  api,
  type ScenarioCategory,
  type ScenarioRow,
  type ScenarioSeverity,
  type ScenariosResponse,
} from "@/lib/api";
import { Pill, type PillVariant } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";

const CATEGORY_LABEL: Record<ScenarioCategory, string> = {
  "preflight-gate": "Pre-flight gate",
  "rule-engine-override": "Rule engine override",
  "constitutional-judge": "Constitutional judge",
  "scope-and-role": "Scope & role boundary",
  "input-injection": "Input-layer injection",
  "storage-authz": "Storage authz",
  "stage-machine": "Stage machine",
  "stage-lock": "Stage lock",
  orchestrator: "Orchestrator",
  "loop-bound": "Loop bound",
};

function severityVariant(s: ScenarioSeverity): PillVariant {
  if (s === "critical") return "danger";
  if (s === "high") return "warn";
  if (s === "medium") return "info";
  return "neutral";
}

interface CardProps {
  scenario: ScenarioRow;
}

function ScenarioCard({ scenario }: CardProps) {
  const isGap = scenario.status === "known-gap";
  return (
    <article
      className="rounded-lg border-[0.5px] p-3.5"
      style={{
        borderColor: "var(--color-border-tertiary)",
        background: isGap
          ? "var(--color-background-secondary)"
          : "var(--color-background-primary)",
      }}
    >
      <header className="mb-2 flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="mb-1 flex flex-wrap items-center gap-1.5">
            <Pill variant="neutral">
              {CATEGORY_LABEL[scenario.category] ?? scenario.category}
            </Pill>
            <Pill variant={severityVariant(scenario.severity)}>
              {scenario.severity}
            </Pill>
            {scenario.status === "protected" ? (
              <Pill variant="success" leading={<IconShieldCheck size={10} />}>
                protected
              </Pill>
            ) : (
              <Pill variant="warn" leading={<IconAlertTriangle size={10} />}>
                known gap
              </Pill>
            )}
          </div>
          <h3 className="text-[13px] font-semibold text-[var(--color-text-primary)]">
            {scenario.title}
          </h3>
        </div>
      </header>

      <div className="space-y-2.5">
        <div>
          <p className="text-[10px] font-medium uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
            <IconShieldX size={10} className="mr-0.5 inline-block" />
            Threat
          </p>
          <p className="mt-0.5 text-[12.5px] leading-relaxed text-[var(--color-text-primary)]">
            {scenario.threat}
          </p>
        </div>
        <div>
          <p className="text-[10px] font-medium uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
            <IconLock size={10} className="mr-0.5 inline-block" />
            Defense
          </p>
          <p className="mt-0.5 text-[12.5px] leading-relaxed text-[var(--color-text-primary)]">
            {scenario.defense}
          </p>
          <p className="mt-1 text-[11px] text-[var(--color-text-tertiary)]">
            Layer:{" "}
            <code className="text-[var(--color-text-secondary)]">
              {scenario.defense_layer}
            </code>
          </p>
        </div>
        {scenario.test_id && (
          <div>
            <p className="text-[10px] font-medium uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
              Verified by
            </p>
            <code className="mt-0.5 block break-all text-[11px] text-[var(--color-text-secondary)]">
              {scenario.test_id}
            </code>
          </div>
        )}
      </div>
    </article>
  );
}

const CATEGORY_OPTIONS: { value: ScenarioCategory | "all"; label: string }[] = [
  { value: "all", label: "All categories" },
  { value: "preflight-gate", label: "Pre-flight gate" },
  { value: "rule-engine-override", label: "Rule engine override" },
  { value: "constitutional-judge", label: "Constitutional judge" },
  { value: "scope-and-role", label: "Scope & role" },
  { value: "input-injection", label: "Input injection" },
  { value: "storage-authz", label: "Storage authz" },
  { value: "stage-machine", label: "Stage machine" },
  { value: "stage-lock", label: "Stage lock" },
  { value: "orchestrator", label: "Orchestrator" },
  { value: "loop-bound", label: "Loop bound" },
];

const SEVERITY_OPTIONS: {
  value: ScenarioSeverity | "all";
  label: string;
}[] = [
  { value: "all", label: "All severities" },
  { value: "critical", label: "Critical" },
  { value: "high", label: "High" },
  { value: "medium", label: "Medium" },
  { value: "low", label: "Low" },
];

export function ScenariosCatalog() {
  const [category, setCategory] = useState<ScenarioCategory | "all">("all");
  const [severity, setSeverity] = useState<ScenarioSeverity | "all">("all");

  const query = useQuery<ScenariosResponse, Error>({
    queryKey: ["safety", "scenarios"],
    queryFn: () => api.getSafetyScenarios(),
    // Static catalog — refetch on focus is enough; no polling.
    staleTime: 10 * 60_000,
  });

  if (query.isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-[120px]" />
        <Skeleton className="h-[120px]" />
        <Skeleton className="h-[120px]" />
      </div>
    );
  }
  if (!query.data) return null;

  const filter = (rows: ScenarioRow[]) =>
    rows.filter((r) => {
      if (category !== "all" && r.category !== category) return false;
      if (severity !== "all" && r.severity !== severity) return false;
      return true;
    });

  const protectedRows = filter(query.data.protected);
  const gapRows = filter(query.data.known_gaps);

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-2">
        <label className="flex items-center gap-1.5 text-[11px]">
          <span className="text-[var(--color-text-tertiary)]">Category</span>
          <select
            value={category}
            onChange={(e) =>
              setCategory(e.target.value as ScenarioCategory | "all")
            }
            className="form-input-on-card"
          >
            {CATEGORY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <label className="flex items-center gap-1.5 text-[11px]">
          <span className="text-[var(--color-text-tertiary)]">Severity</span>
          <select
            value={severity}
            onChange={(e) =>
              setSeverity(e.target.value as ScenarioSeverity | "all")
            }
            className="form-input-on-card"
          >
            {SEVERITY_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
        <p className="ml-auto text-[11px] text-[var(--color-text-tertiary)]">
          {protectedRows.length} protected · {gapRows.length} known gap
          {gapRows.length === 1 ? "" : "s"}
        </p>
      </div>

      {/* Protected scenarios */}
      <div>
        <SectionLabel>Protected by tests</SectionLabel>
        <p className="mb-3 text-[12px] text-[var(--color-text-tertiary)]">
          Each card describes a way the system can be attacked, the
          defense layer that catches it, and the test that pins it.
          A test failure flips the corresponding card to a regression
          banner.
        </p>
        {protectedRows.length === 0 ? (
          <p className="text-[12px] text-[var(--color-text-tertiary)]">
            No scenarios match the filters.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {protectedRows.map((s) => (
              <ScenarioCard key={s.id} scenario={s} />
            ))}
          </div>
        )}
      </div>

      {/* Known gaps */}
      {gapRows.length > 0 && (
        <div>
          <SectionLabel>Honestly documented gaps</SectionLabel>
          <p className="mb-3 text-[12px] text-[var(--color-text-tertiary)]">
            Things the demo doesn&apos;t do — surfaced here on purpose
            so a reviewer knows what would need to be built before a
            real lender deployment.
          </p>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {gapRows.map((s) => (
              <ScenarioCard key={s.id} scenario={s} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
