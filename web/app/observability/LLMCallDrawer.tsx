"use client";

import { useQuery } from "@tanstack/react-query";
import { IconX } from "@tabler/icons-react";
import { AnimatePresence, motion } from "motion/react";
import { api, type LLMCallDetail } from "@/lib/api";
import { titleCase } from "@/lib/humanize";
import { IconButton } from "@/app/components/IconButton";
import { Pill, type PillVariant } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";

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
          className="fixed inset-0 z-40 bg-black/30"
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
          <Meta label="Attempt">
            <span className="tabular-nums">{detail.attempt}</span>
          </Meta>
          <Meta label="Tokens">
            <span className="tabular-nums">
              {detail.input_tokens != null && detail.output_tokens != null
                ? `${detail.input_tokens} → ${detail.output_tokens}`
                : "—"}
            </span>
          </Meta>
          <Meta label="Prompt hash" hint="sha256(system_prompt) — for grouping">
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
    </div>
  );
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
      <span
        className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]"
        title={hint}
      >
        {label}
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
