"use client";

/**
 * /safety dashboard — top-level surface for the input-side injection
 * detector + output-side constitutional judge + scenarios catalog.
 *
 * Sibling to /observability. The observability page also embeds the
 * SafetyPanel as a compact tab, but this page is the destination for
 * the deep-dive — same window picker, full panels, drawer-open
 * behaviour on row click, plus a Scenarios tab that lists every
 * robustness property the system pins (the "how the app stays safe"
 * surface a reviewer browses without reading any code).
 */

import { useState } from "react";
import { IconChartBar, IconListCheck } from "@tabler/icons-react";
import { BrandHeader } from "@/app/components/BrandHeader";
import { SafetyPanel } from "./SafetyPanel";
import { ScenariosCatalog } from "./ScenariosCatalog";

type SafetyTab = "live" | "scenarios";

const WINDOWS: { hours: number; label: string }[] = [
  { hours: 1, label: "1h" },
  { hours: 24, label: "24h" },
  { hours: 168, label: "7d" },
  { hours: 720, label: "30d" },
];

function WindowPicker({
  hours,
  onChange,
}: {
  hours: number;
  onChange: (h: number) => void;
}) {
  return (
    <div
      className="flex items-center gap-0.5 rounded-md border-[0.5px] p-0.5"
      style={{ borderColor: "var(--color-border-tertiary)" }}
    >
      {WINDOWS.map((w) => (
        <button
          key={w.hours}
          onClick={() => onChange(w.hours)}
          className={
            "rounded px-2 py-0.5 text-[11px] font-medium transition-colors " +
            (hours === w.hours
              ? "bg-[var(--color-background-secondary)] text-[var(--color-text-primary)]"
              : "text-[var(--color-text-tertiary)] hover:text-[var(--color-text-primary)]")
          }
        >
          {w.label}
        </button>
      ))}
    </div>
  );
}

const TABS: { value: SafetyTab; label: string; Icon: React.ComponentType<{ size?: number }> }[] = [
  { value: "live", label: "Live signals", Icon: IconChartBar },
  { value: "scenarios", label: "Scenarios catalog", Icon: IconListCheck },
];

function TabBar({
  active,
  onChange,
}: {
  active: SafetyTab;
  onChange: (t: SafetyTab) => void;
}) {
  return (
    <div
      className="flex items-center gap-0.5 border-b-[0.5px]"
      style={{ borderColor: "var(--color-border-tertiary)" }}
    >
      {TABS.map((t) => {
        const isActive = active === t.value;
        const Icon = t.Icon;
        return (
          <button
            key={t.value}
            onClick={() => onChange(t.value)}
            className={
              "relative flex items-center gap-1.5 px-3 py-2 text-[12px] font-medium transition-colors " +
              (isActive
                ? "text-[var(--color-text-primary)]"
                : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]")
            }
          >
            <Icon size={13} />
            {t.label}
            {isActive && (
              <span
                className="absolute bottom-[-0.5px] left-0 right-0 h-[2px]"
                style={{ background: "var(--color-brand)" }}
              />
            )}
          </button>
        );
      })}
    </div>
  );
}

export default function SafetyPage() {
  const [tab, setTab] = useState<SafetyTab>("live");
  const [windowHours, setWindowHours] = useState(24);

  return (
    <div className="flex flex-col gap-3">
      <BrandHeader
        title="Safety & guardrails"
        sub="Live injection detections + constitutional judge verdicts on the left tab. The scenarios catalog on the right is every robustness property the system pins, with the test that verifies it."
        actions={
          tab === "live" ? (
            <WindowPicker hours={windowHours} onChange={setWindowHours} />
          ) : null
        }
      />
      <TabBar active={tab} onChange={setTab} />
      {tab === "live" ? (
        <SafetyPanel windowHours={windowHours} />
      ) : (
        <ScenariosCatalog />
      )}
    </div>
  );
}
