"use client";

/**
 * Refusal / abstain rate trend — input-detector block-rate canary.
 *
 * Backs ``mkopo/services/refusal.py``. A sudden change in the
 * injection-detector block rate is the canonical leading indicator
 * for:
 *
 *  - A new attack family slipping past the regex catalog (Haiku
 *    catches what regex doesn't → blocks rise).
 *  - Prompt drift on borrower inputs (real users producing
 *    suspicious-looking but legitimate text → spurious blocks).
 *  - Detector regression (a tightening change blocks legitimate
 *    traffic).
 *
 * The card shows the current 7-day block rate vs the prior 28-day
 * baseline, with a z-score derived from the binomial-proportion
 * test. |z| ≥ 2 flips the flag to ``spike`` — a 95%-confidence
 * deviation that's worth operator attention.
 *
 * NOT a CI gate. The dashboard surfaces the trend; the operator
 * decides what to do about it. Blocking attackers is success;
 * blocking everyone is the problem.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { IconRefresh, IconShieldOff } from "@tabler/icons-react";
import { toast } from "sonner";
import {
  api,
  type RefusalDetails,
  type TaskDetail,
} from "@/lib/api";
import { Pill, type PillVariant } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { Tooltip } from "@/app/components/Tooltip";
import { NISTBadge } from "./NISTBadge";

const PCT = (v: number | null | undefined, digits = 1) =>
  v == null ? "—" : `${(v * 100).toFixed(digits)}%`;

const FLAG_META: Record<
  string,
  { variant: PillVariant; label: string }
> = {
  stable: { variant: "success", label: "Stable" },
  spike: { variant: "danger", label: "Spike" },
  insufficient_data: { variant: "neutral", label: "No data" },
};

export function RefusalCard() {
  const queryClient = useQueryClient();
  const query = useQuery<TaskDetail, Error>({
    queryKey: ["eval-task-detail", "refusal.block_rate"],
    queryFn: () => api.getTaskDetail("refusal.block_rate"),
    refetchInterval: 60_000,
  });

  const refresh = useMutation({
    mutationFn: () => api.refreshRefusal(),
    onSuccess: async (result) => {
      await queryClient.invalidateQueries({
        queryKey: ["eval-task-detail", "refusal.block_rate"],
      });
      if (result.flag === "insufficient_data") {
        toast.info("Refusal monitor ran — insufficient data", {
          description: `Need ≥ 20 detections in the last 7d AND ≥ 30 in the prior 28d. Got ${result.n_current} / ${result.n_baseline}.`,
        });
      } else {
        toast.success("Refusal monitor ran", {
          description: `Block rate: ${PCT(result.current_rate)} (baseline ${PCT(result.baseline_rate)}, z=${result.z_score?.toFixed(2) ?? "—"}).`,
        });
      }
    },
    onError: (e) =>
      toast.error("Refusal monitor failed", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  if (query.isLoading) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <Skeleton className="mb-2 h-3 w-40" />
        <Skeleton className="h-[120px]" />
      </div>
    );
  }

  if (!query.data?.found || !query.data.details) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <div className="mb-2 flex items-center justify-between gap-2">
          <SectionLabel Icon={IconShieldOff} dense>
            Refusal-rate trend
          </SectionLabel>
          <RefreshButton
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
          />
        </div>
        <p className="text-[12px] text-[var(--color-text-tertiary)]">
          No refusal-rate data yet. Need ≥ 20 detections in the
          current 7-day window AND ≥ 30 in the prior 28-day baseline
          before the z-score is statistically meaningful. Document
          uploads + chat messages populate this; the monitor itself
          runs at 3:52 UTC nightly.
        </p>
        <p className="mt-2 text-[10.5px] text-[var(--color-text-tertiary)]">
          z = (p_cur − p_base) / √(p_base(1−p_base)/n_cur). Spike
          threshold: |z| ≥ 2 (binomial-proportion 2σ).
        </p>
      </div>
    );
  }

  const d = query.data.details as unknown as RefusalDetails;
  const ranAt = query.data.ran_at
    ? new Date(query.data.ran_at).toLocaleString()
    : "—";
  const flagMeta = FLAG_META[d.flag] ?? FLAG_META.insufficient_data;
  const delta = d.current_rate - d.baseline_rate;

  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <SectionLabel Icon={IconShieldOff} dense>
          Refusal-rate trend
        </SectionLabel>
        <span className="flex items-center gap-2 text-[11px] text-[var(--color-text-tertiary)]">
          <Tooltip
            content={
              <>
                <strong>Block rate over last 7 days</strong> from the
                input-layer injection detector. Compared against the
                prior 28-day baseline via the binomial-proportion
                test. A spike is the canonical early warning for a
                new attack family OR a regression in the detector
                itself.
              </>
            }
            underline
            maxWidth={320}
          >
            block rate
          </Tooltip>
          <Pill variant={flagMeta.variant}>
            {PCT(d.current_rate)} · {flagMeta.label}
          </Pill>
          <RefreshButton
            disabled={refresh.isPending}
            onClick={() => refresh.mutate()}
          />
        </span>
      </div>

      <div className="grid grid-cols-3 gap-3 text-[11.5px]">
        <Metric
          label="Current 7d"
          value={PCT(d.current_rate)}
          sub={`${d.n_current_blocked} / ${d.n_current}`}
        />
        <Metric
          label="Baseline 28d"
          value={PCT(d.baseline_rate)}
          sub={`${d.n_baseline_blocked} / ${d.n_baseline}`}
        />
        <Metric
          label="Δ vs baseline"
          value={`${delta >= 0 ? "+" : ""}${(delta * 100).toFixed(1)}pp`}
          sub={
            d.z_score != null
              ? `z=${d.z_score >= 0 ? "+" : ""}${d.z_score.toFixed(2)}`
              : "—"
          }
          tone={d.flag === "spike" ? "danger" : "default"}
        />
      </div>

      <p className="mt-3 flex flex-wrap items-center gap-1.5 text-[10.5px] text-[var(--color-text-tertiary)]">
        <span>
          Spike when |z| ≥ {d.spike_threshold_sigma}. Last run:{" "}
          {ranAt}. Production trend; not a CI gate.
        </span>
        <NISTBadge category="info_security" />
      </p>
    </div>
  );
}

function Metric({
  label,
  value,
  sub,
  tone,
}: {
  label: string;
  value: string;
  sub: string;
  tone?: "default" | "danger";
}) {
  const colour =
    tone === "danger"
      ? "var(--color-text-danger)"
      : "var(--color-text-primary)";
  return (
    <div className="rounded-md bg-[var(--color-background-secondary)] px-3 py-2">
      <p className="text-[10px] uppercase tracking-wider text-[var(--color-text-secondary)]">
        {label}
      </p>
      <p
        className="font-editorial mt-0.5 text-[18px] leading-none tabular-nums"
        style={{ color: colour }}
      >
        {value}
      </p>
      <p className="mt-1 text-[10.5px] text-[var(--color-text-tertiary)] tabular-nums">
        {sub}
      </p>
    </div>
  );
}

function RefreshButton({
  disabled,
  onClick,
}: {
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      title="Re-run the refusal-rate monitor now"
      className="inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10.5px] text-[var(--color-text-secondary)] hover:bg-[var(--color-background-secondary)] disabled:opacity-50"
    >
      <IconRefresh size={11} />
      {disabled ? "Running…" : "Refresh"}
    </button>
  );
}
