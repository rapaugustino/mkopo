"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { IconGitCompare, IconX } from "@tabler/icons-react";
import { AnimatePresence, motion } from "motion/react";
import { api, type LLMCallDetail, type LLMCallDiff, type LLMCallRow, type ToolUseRow } from "@/lib/api";
import { titleCase } from "@/lib/humanize";
import { AnnotationPanel } from "@/app/components/AnnotationPanel";
import { IconButton } from "@/app/components/IconButton";
import { Pill, type PillVariant } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { InfoTooltip } from "@/app/components/Tooltip";

interface Props {
  callId: string | null;
  onClose: () => void;
}

/**
 * Drill-in drawer for one LLM call.
 *
 * Closes the audit's loudest gap: previously the observability table
 * showed "call failed" with no way to find out *why* — system_prompt
 * was a hash and there was no detail endpoint. Now clicking a row
 * opens this drawer, which surfaces:
 *
 *  - the short ``error_reason`` (always visible) and long
 *    ``error_detail`` (full validation pretty-print or SDK repr)
 *  - the call's full metadata (model, schema, attempts, latency,
 *    tokens, prompt-hash for cross-referencing)
 *  - the **related calls** sharing the same ``system_prompt_hash``
 *    in the last 72h so an operator can tell "this prompt always
 *    fails on this model" from "transient SDK 5xx".
 *
 * Right-side slide-in; closes on backdrop click or ``onClose``.
 */
export function LLMCallDrawer({ callId, onClose }: Props) {
  const detailQuery = useQuery<LLMCallDetail, Error>({
    queryKey: ["llm-call-detail", callId],
    queryFn: () => api.getLLMCallDetail(callId!),
    enabled: callId != null,
  });

  return (
    <AnimatePresence>
      {callId && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.14 }}
          className="fixed inset-0 z-40"
          style={{ background: "var(--color-overlay-light)" }}
          onClick={onClose}
          role="dialog"
          aria-modal="true"
          aria-label="LLM call details"
        >
          <motion.aside
            initial={{ x: 24, opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: 24, opacity: 0 }}
            transition={{ duration: 0.2, ease: "easeOut" }}
            onClick={(e) => e.stopPropagation()}
            className="absolute right-0 top-0 flex h-full w-full max-w-[640px] flex-col overflow-hidden bg-[var(--color-background-primary)] shadow-2xl"
          >
            <header className="flex items-start justify-between gap-3 border-b-[0.5px] border-[var(--color-border-tertiary)] px-5 py-4">
              <div>
                <p className="text-[11px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
                  LLM call
                </p>
                <p className="mt-0.5 truncate font-mono text-[12px] text-[var(--color-text-primary)]">
                  {callId}
                </p>
              </div>
              <IconButton label="Close" Icon={IconX} onClick={onClose} />
            </header>

            <div className="flex-1 overflow-y-auto px-5 py-4">
              {detailQuery.isPending && (
                <p className="text-[12px] text-[var(--color-text-tertiary)]">
                  Loading call detail…
                </p>
              )}
              {detailQuery.error && (
                <p className="text-[12px] text-[var(--color-text-danger)]">
                  Couldn&apos;t load detail: {detailQuery.error.message}
                </p>
              )}
              {detailQuery.data && <CallBody detail={detailQuery.data} />}
            </div>
          </motion.aside>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function statusVariant(status: string): PillVariant {
  if (status === "ok") return "success";
  if (status === "schema_failed" || status === "error") return "danger";
  return "warn";
}

function CallBody({ detail }: { detail: LLMCallDetail }) {
  return (
    <div className="flex flex-col gap-5">
      {/* Human verdict on this call. Sits at the top so an operator
          who clicked through from /eval's "recent failures" can
          mark it in one motion. */}
      <AnnotationPanel targetKind="llm_call" targetId={detail.id} />

      {/* Headline metadata grid. The numbers an operator wants to
          see first: when, status, latency, attempt count. Tokens
          live below as supporting evidence. */}
      <section>
        <div className="grid grid-cols-2 gap-3">
          <Meta label="Status">
            <Pill variant={statusVariant(detail.status)} size="sm">
              {titleCase(detail.status)}
            </Pill>
          </Meta>
          <Meta label="When">
            <time
              className="text-[12.5px]"
              title={new Date(detail.created_at).toISOString()}
            >
              {new Date(detail.created_at).toLocaleString(undefined, {
                dateStyle: "medium",
                timeStyle: "medium",
              })}
            </time>
          </Meta>
          <Meta label="Model">
            <span className="font-medium">{detail.model}</span>
          </Meta>
          <Meta label="Schema">
            <span>{detail.schema_name ?? "—"}</span>
          </Meta>
          <Meta label="Latency">
            <span className="tabular-nums">
              {detail.elapsed_seconds.toFixed(2)}s
            </span>
          </Meta>
          <Meta
            label="Attempt"
            hint="0 = first call. Positive values mean the gateway retried after a schema-fail or transient error; each retry is its own llm_calls row."
          >
            {/* ``attempt`` is stored 0-indexed on the row (0 = first
                call). Rendering the raw "0" reads as broken; convert
                to a 1-based ordinal so an operator scanning the
                drawer sees "1 of 1" mental-model'd as "first try". */}
            <span className="tabular-nums">
              {detail.attempt === 0
                ? "First call"
                : `Retry #${detail.attempt}`}
            </span>
          </Meta>
          <Meta label="Tokens">
            <span className="tabular-nums">
              {detail.input_tokens != null && detail.output_tokens != null
                ? `${detail.input_tokens} → ${detail.output_tokens}`
                : "—"}
            </span>
          </Meta>
          <Meta
            label="Cost"
            hint="Per-call USD = input × per-million-input + output × per-million-output, priced at the model's published rate at write time. — = no pricing for this model in the gateway."
          >
            <span className="tabular-nums">
              {detail.cost_input_usd != null || detail.cost_output_usd != null
                ? `$${(
                    (detail.cost_input_usd ?? 0) +
                    (detail.cost_output_usd ?? 0)
                  ).toFixed(4)}`
                : "—"}
            </span>
          </Meta>
          <Meta label="Prompt hash" hint="sha256(system_prompt) — same hash = same prompt text. Use it to group related failures.">
            <code className="text-[11px] text-[var(--color-text-secondary)]">
              {detail.system_prompt_hash.slice(0, 16)}…
            </code>
          </Meta>
        </div>
      </section>

      {/* Failure forensics. Only renders for failure rows; the reason
          stays visible and the long detail tucks behind nothing (we
          already paid the drawer-open cost). */}
      {detail.error_reason && (
        <section>
          <SectionLabel>Failure</SectionLabel>
          <div className="mt-2 rounded-md bg-[var(--color-background-danger)] px-3 py-2.5">
            <p className="text-[12.5px] font-medium text-[var(--color-text-danger)]">
              {detail.error_reason}
            </p>
            {detail.error_detail && (
              <pre className="mt-2 max-h-72 overflow-auto whitespace-pre-wrap break-words text-[11px] leading-snug text-[var(--color-text-danger)] opacity-90">
                {detail.error_detail}
              </pre>
            )}
          </div>
        </section>
      )}

      {/* Tool trajectory. Populated when the LLM call asked for one
          or more tools — renders the full sequence the agent issued,
          its inputs, what came back, and (for failures) why. This
          is the Langsmith-replacement insight: you can see exactly
          what the agent did without crawling structured logs. */}
      {detail.tool_uses.length > 0 && (
        <section>
          <SectionLabel>
            Tool trajectory
            <span className="ml-1 font-normal text-[var(--color-text-tertiary)]">
              · {detail.tool_uses.length}
            </span>
          </SectionLabel>
          <ol className="mt-2 flex flex-col gap-2">
            {detail.tool_uses.map((tu) => (
              <ToolUseStep key={tu.id} tu={tu} />
            ))}
          </ol>
        </section>
      )}

      {/* Same-prompt neighbours. The single most useful thing for
          deciding "is this a pattern or a blip?" — if 12 of the last
          14 calls with this prompt hash failed the same way, the
          prompt is broken; if it's 1 of 50 the SDK had a moment. */}
      <section>
        <SectionLabel>
          Same-prompt calls (last 72h)
          <span className="ml-1 font-normal text-[var(--color-text-tertiary)]">
            · {detail.related.length}
          </span>
        </SectionLabel>
        {detail.related.length === 0 ? (
          <p className="mt-2 text-[12px] text-[var(--color-text-tertiary)]">
            No other recent calls with this prompt hash.
          </p>
        ) : (
          <table className="mt-2 w-full text-[12px]">
            <thead>
              <tr className="border-b-[0.5px] border-[var(--color-border-tertiary)]">
                {["Time", "Status", "Model", "Latency"].map((h, i) => (
                  <th
                    key={h}
                    className={
                      "py-1.5 text-[10px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)] " +
                      (i === 0 ? "pl-1 text-left" : i === 3 ? "pr-1 text-right" : "px-1 text-left")
                    }
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {detail.related.map((r) => (
                <tr
                  key={r.id}
                  className="border-b-[0.5px] border-[var(--color-border-tertiary)] last:border-b-0"
                >
                  <td
                    className="py-1.5 pl-1 text-[var(--color-text-secondary)]"
                    title={new Date(r.created_at).toISOString()}
                  >
                    {relativeShort(r.created_at)}
                  </td>
                  <td className="px-1 py-1.5">
                    <Pill variant={statusVariant(r.status)} size="xs">
                      {titleCase(r.status)}
                    </Pill>
                  </td>
                  <td className="px-1 py-1.5 text-[var(--color-text-secondary)]">
                    {r.model}
                  </td>
                  <td className="pr-1 text-right tabular-nums">
                    {r.elapsed_seconds.toFixed(2)}s
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {/* Regression diff. Lets the operator pick a same-prompt
          neighbour and see a per-field side-by-side: latency, tokens,
          cost, status, attempts. Metadata-only — we don't store the
          user/response text, so prompt diffing is out of scope. The
          point is to catch "model creep" or "this prompt got slower"
          regressions, not to do source-level diffing. */}
      {detail.related.length > 0 && (
        <DiffSection thisCall={detail} candidates={detail.related} />
      )}
    </div>
  );
}


// ---- diff section ---------------------------------------------------------


function DiffSection({
  thisCall,
  candidates,
}: {
  thisCall: LLMCallDetail;
  candidates: LLMCallRow[];
}) {
  const [otherId, setOtherId] = useState<string>("");
  const diffQuery = useQuery<LLMCallDiff, Error>({
    queryKey: ["llm-call-diff", thisCall.id, otherId],
    queryFn: () => api.diffLLMCalls(otherId, thisCall.id),
    enabled: otherId !== "",
  });
  return (
    <section>
      <SectionLabel Icon={IconGitCompare}>Compare to…</SectionLabel>
      <p className="mt-1 text-[11.5px] text-[var(--color-text-secondary)]">
        Pick a same-prompt neighbour to see per-field deltas. <strong>A</strong>{" "}
        is the picked call; <strong>B</strong> is this one.
      </p>
      <select
        value={otherId}
        onChange={(e) => setOtherId(e.target.value)}
        className="form-input mt-2 w-full text-[12px]"
      >
        <option value="">— pick a call —</option>
        {candidates.map((r) => (
          // Same field order + casing as the Same-prompt calls table
          // above so an operator's eye doesn't have to retrain when
          // jumping between the two views.
          <option key={r.id} value={r.id}>
            {relativeShort(r.created_at)} · {r.model} ·{" "}
            {titleCase(r.status)} · {r.elapsed_seconds.toFixed(2)}s
          </option>
        ))}
      </select>
      {otherId !== "" && diffQuery.isPending && (
        <p className="mt-2 text-[11.5px] text-[var(--color-text-tertiary)]">
          Computing diff…
        </p>
      )}
      {diffQuery.data && <DiffTable diff={diffQuery.data} />}
    </section>
  );
}


function DiffTable({ diff }: { diff: LLMCallDiff }) {
  return (
    <div className="mt-3 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
      <div className="border-b-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] px-3 py-2">
        <p className="text-[12px] font-medium">{diff.summary}</p>
      </div>
      <table className="w-full text-[12px]">
        <thead>
          <tr className="border-b-[0.5px] border-[var(--color-border-tertiary)]">
            {["Field", "A", "B", "Δ"].map((h, i) => (
              <th
                key={h}
                className={
                  "py-1.5 text-[10px] font-medium uppercase tracking-[0.03em] text-[var(--color-text-secondary)] " +
                  (i === 0 ? "pl-2 text-left" : i === 3 ? "pr-2 text-right" : "px-2 text-left")
                }
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {diff.fields.map((f) => (
            <tr
              key={f.label}
              className="border-b-[0.5px] border-[var(--color-border-tertiary)] last:border-b-0"
            >
              <td className="py-1.5 pl-2 font-medium">{f.label}</td>
              <td className="px-2 py-1.5 text-[var(--color-text-secondary)] tabular-nums">
                {f.a}
              </td>
              <td className="px-2 py-1.5 tabular-nums">{f.b}</td>
              <td
                className="pr-2 py-1.5 text-right text-[11.5px] tabular-nums"
                style={{ color: diffFlagColour(f.flag) }}
              >
                {f.delta}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function diffFlagColour(flag: string): string {
  if (flag === "regression") return "var(--color-text-danger)";
  if (flag === "improvement") return "var(--color-text-success)";
  if (flag === "match") return "var(--color-text-tertiary)";
  return "var(--color-text-secondary)";
}

function Meta({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-0.5">
      {/* Label row. When ``hint`` is provided we surface an inline
         ⓘ glyph next to the label that opens our custom Tooltip on
         hover (vs the previous native ``title=`` which was browser-
         themed and didn't read as a learn-more affordance). */}
      <span className="inline-flex items-center text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
        {label}
        {hint && <InfoTooltip content={hint} maxWidth={260} />}
      </span>
      <span className="text-[var(--color-text-primary)]">{children}</span>
    </div>
  );
}

function relativeShort(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h`;
  return `${Math.floor(sec / 86400)}d`;
}

/** One step in the tool trajectory. Expandable card showing the
 *  tool name, status, timing, and (on expand) the input + output
 *  JSON for full forensic transparency. Failures surface the error
 *  message in red so the operator can see exactly what went wrong. */
function ToolUseStep({ tu }: { tu: ToolUseRow }) {
  const variant: PillVariant =
    tu.status === "ok"
      ? "success"
      : tu.status === "cancelled"
        ? "neutral"
        : "danger";
  return (
    <li className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 py-2">
      <div className="flex items-center justify-between gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[10px] font-semibold"
            style={{
              background: "var(--color-background-secondary)",
              color: "var(--color-text-secondary)",
            }}
          >
            {tu.sequence_num + 1}
          </span>
          <code className="truncate font-mono text-[12.5px] text-[var(--color-text-primary)]">
            {tu.tool_name}
          </code>
          <Pill variant={variant} size="xs">
            {titleCase(tu.status)}
          </Pill>
        </div>
        {tu.elapsed_ms != null && (
          <span className="shrink-0 tabular-nums text-[11px] text-[var(--color-text-tertiary)]">
            {tu.elapsed_ms}ms
          </span>
        )}
      </div>
      {tu.error_message && (
        <p className="mt-1.5 rounded bg-[var(--color-background-danger)] px-2 py-1.5 text-[11.5px] text-[var(--color-text-danger)]">
          {tu.error_message}
        </p>
      )}
      <details className="mt-1.5 group">
        <summary className="cursor-pointer text-[11px] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]">
          <span className="group-open:hidden">Show inputs &amp; output</span>
          <span className="hidden group-open:inline">Hide inputs &amp; output</span>
        </summary>
        <div className="mt-2 grid grid-cols-2 gap-2">
          <div>
            <p className="mb-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
              Input
            </p>
            <pre className="max-h-48 overflow-auto rounded bg-[var(--color-background-secondary)] p-2 font-mono text-[11px] leading-snug">
              {JSON.stringify(tu.input, null, 2)}
            </pre>
          </div>
          <div>
            <p className="mb-0.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
              Output
            </p>
            <pre className="max-h-48 overflow-auto rounded bg-[var(--color-background-secondary)] p-2 font-mono text-[11px] leading-snug">
              {tu.output ? JSON.stringify(tu.output, null, 2) : "—"}
            </pre>
          </div>
        </div>
      </details>
    </li>
  );
}
