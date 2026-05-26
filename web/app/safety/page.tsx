"use client";

/**
 * /safety dashboard — top-level surface for the input-side injection
 * detector + output-side constitutional judge.
 *
 * Sibling to /observability. The observability page also embeds the
 * SafetyPanel as a compact tab, but this page is the destination for
 * the deep-dive — same window picker, full panels, drawer-open
 * behaviour on row click.
 *
 * Auto-refreshes every 30s; cheap because the SafetyPanel uses
 * useQuery internally.
 */

import { useState } from "react";
import { BrandHeader } from "@/app/components/BrandHeader";
import { SafetyPanel } from "./SafetyPanel";

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

export default function SafetyPage() {
  const [windowHours, setWindowHours] = useState(24);

  return (
    <div className="flex flex-col gap-3">
      <BrandHeader
        title="Safety & guardrails"
        sub="Input-side injection detections (hybrid pattern + Haiku) and output-side constitutional judge verdicts. Refreshes every 30s."
        actions={<WindowPicker hours={windowHours} onChange={setWindowHours} />}
      />
      <SafetyPanel windowHours={windowHours} />
    </div>
  );
}
