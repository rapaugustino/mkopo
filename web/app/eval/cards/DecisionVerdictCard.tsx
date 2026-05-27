"use client";

/**
 * Decision-verdict eval card — renders the confusion matrix + per-
 * class precision/recall/F1 + macro-F1.
 *
 * Visualizes the output of ``evals/tasks/decision_verdict.py``.
 * SR 11-7 §VI outcome analysis requires direction-of-error
 * breakdown for any classification model — that's what this
 * confusion matrix is. The diagonal is correct predictions; every
 * off-diagonal cell is a specific failure mode (e.g. cell
 * [expected=decline, predicted=approve] is the "we missed a hard
 * decline" cell — the worst possible direction for a lender).
 *
 * Cell color intensity is normalized per row (the dominant
 * prediction for a given expected class hits 100% saturation). This
 * makes the visual read regardless of class imbalance — a 20-example
 * golden set with 5/5/10 distribution still shows useful contrast.
 */

import { useQuery } from "@tanstack/react-query";
import { api, type DecisionVerdictDetails, type TaskDetail } from "@/lib/api";
import { Pill } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import { Tooltip } from "@/app/components/Tooltip";

const PCT = (v: number, digits = 0) => `${(v * 100).toFixed(digits)}%`;

interface CellProps {
  count: number;
  rowMax: number;
  isDiagonal: boolean;
}

function MatrixCell({ count, rowMax, isDiagonal }: CellProps) {
  // Normalize against the row max so colour reads regardless of
  // class size. Diagonal cells get the brand green; off-diagonal
  // (errors) get a softer warning red so the directionality is
  // visually obvious.
  const intensity = rowMax > 0 ? count / rowMax : 0;
  const baseColor = isDiagonal
    ? "16, 152, 111" // brand green RGB
    : "200, 80, 80"; // soft warn red RGB
  return (
    <td
      className="border-[0.5px] text-center tabular-value"
      style={{
        borderColor: "var(--color-border-tertiary)",
        background: `rgba(${baseColor}, ${intensity * 0.55})`,
        // Slight text contrast bump for high-intensity cells.
        color:
          intensity > 0.5
            ? "var(--color-text-primary)"
            : "var(--color-text-secondary)",
        minWidth: "60px",
        height: "44px",
      }}
    >
      <span className="text-[14px] font-semibold">{count}</span>
    </td>
  );
}

export function DecisionVerdictCard() {
  const query = useQuery<TaskDetail, Error>({
    queryKey: ["eval-task-detail", "decision_verdict"],
    queryFn: () => api.getTaskDetail("decision_verdict"),
    refetchInterval: 60_000,
  });

  if (query.isLoading) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <Skeleton className="mb-2 h-3 w-32" />
        <Skeleton className="h-[200px]" />
      </div>
    );
  }
  if (!query.data?.found || !query.data.details) {
    return (
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel>Decision verdict</SectionLabel>
        <p className="mt-2 text-[12px] text-[var(--color-text-tertiary)]">
          No run yet. Run{" "}
          <code>cd api && uv run python -m evals.runner</code> or wait
          for the 4 AM UTC sweep.
        </p>
      </div>
    );
  }

  const d = query.data.details as unknown as DecisionVerdictDetails;
  const ranAt = query.data.ran_at
    ? new Date(query.data.ran_at).toLocaleString()
    : "—";

  // Row maxes for cell colour normalization. One per expected class.
  const rowMax = Object.fromEntries(
    d.classes.map((c) => [
      c,
      Math.max(...d.classes.map((c2) => d.confusion_matrix[c]?.[c2] ?? 0)),
    ]),
  );

  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <SectionLabel>Decision verdict confusion matrix</SectionLabel>
        <span className="flex items-center gap-2 text-[11px] text-[var(--color-text-tertiary)]">
          <Tooltip
            content="Macro-F1 = unweighted mean of per-class F1. The strict-equal weighting means a regression on the rarest class (declines) hits the headline number just as hard as one on the most common."
            underline
          >
            macro-F1
          </Tooltip>
          <span className="tabular-value font-medium text-[var(--color-text-primary)]">
            {PCT(d.macro_f1, 1)}
          </span>
          <span>·</span>
          <span>n={query.data.n}</span>
          <span>·</span>
          <span>{ranAt}</span>
        </span>
      </div>

      <div className="flex flex-col gap-3 lg:flex-row">
        {/* Confusion matrix table. Rows = expected class, cols =
            predicted. Diagonal cells are correct predictions; off-
            diagonal is the failure mode. */}
        <div className="overflow-x-auto">
          <table className="border-collapse text-[11px]">
            <thead>
              <tr>
                <th className="px-2 py-1 text-left text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
                  Expected ↓ / Predicted →
                </th>
                {d.classes.map((c) => (
                  <th
                    key={c}
                    className="px-2 py-1 text-center text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]"
                  >
                    {c}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {d.classes.map((expected) => (
                <tr key={expected}>
                  <th className="border-[0.5px] border-[var(--color-border-tertiary)] px-2 py-1 text-left text-[11px] font-medium text-[var(--color-text-secondary)]">
                    {expected}
                  </th>
                  {d.classes.map((predicted) => (
                    <MatrixCell
                      key={`${expected}-${predicted}`}
                      count={d.confusion_matrix[expected]?.[predicted] ?? 0}
                      rowMax={rowMax[expected] ?? 0}
                      isDiagonal={expected === predicted}
                    />
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Per-class stats. precision / recall / F1 / support per row. */}
        <div className="flex-1 space-y-1.5 text-[11.5px]">
          <p className="text-[10px] font-medium uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
            Per-class metrics
          </p>
          {d.classes.map((c) => {
            const stats = d.per_class[c];
            if (!stats) return null;
            return (
              <div
                key={c}
                className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] px-2.5 py-1.5"
              >
                <div className="flex items-center justify-between">
                  <span className="font-medium text-[var(--color-text-primary)]">
                    {c}
                  </span>
                  <span className="text-[10px] text-[var(--color-text-tertiary)]">
                    support={stats.n}
                  </span>
                </div>
                <div className="mt-1 flex flex-wrap gap-2">
                  <Tooltip content="Precision = TP / (TP + FP). Of the loans we predicted this class, what fraction was actually this class?">
                    <Pill variant="info">
                      P {PCT(stats.precision)}
                    </Pill>
                  </Tooltip>
                  <Tooltip content="Recall = TP / (TP + FN). Of the loans that should have been this class, what fraction did we catch?">
                    <Pill variant="info">
                      R {PCT(stats.recall)}
                    </Pill>
                  </Tooltip>
                  <Tooltip content="F1 = harmonic mean of precision and recall. The single number that captures both halves of the trade-off.">
                    <Pill variant={stats.f1 >= 0.8 ? "success" : "warn"}>
                      F1 {PCT(stats.f1)}
                    </Pill>
                  </Tooltip>
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
}
