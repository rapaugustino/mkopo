"use client";

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  IconCheck,
  IconEdit,
  IconFileText,
  IconX,
} from "@tabler/icons-react";
import { toast } from "sonner";
import { api, type ExtractionSource } from "@/lib/api";
import { humanizeExtractionStatus, humanizeField } from "@/lib/humanize";
import { IconButton } from "./IconButton";
import { Pill } from "./Pill";
import { PrimaryButton } from "./PrimaryButton";
import { SecondaryButton } from "./SecondaryButton";

interface Props {
  taskId: string;
  onClose: () => void;
}

/** Render `text` with the substring `quote` wrapped in a yellow <mark>.
 *
 * We don't have real char_start/char_end positions stored in the seed —
 * the extractor only persists a verbatim quote. So we locate the quote
 * by string-search and highlight the first match. Falls back to plain
 * text if the quote isn't in the document.
 */
function HighlightedText({
  text,
  quote,
}: {
  text: string;
  quote?: string;
}) {
  const blocks = useMemo(() => {
    if (!quote) return [{ text, highlight: false }];
    const idx = text.indexOf(quote);
    if (idx === -1) return [{ text, highlight: false }];
    return [
      { text: text.slice(0, idx), highlight: false },
      { text: quote, highlight: true },
      { text: text.slice(idx + quote.length), highlight: false },
    ];
  }, [text, quote]);

  return (
    <div className="whitespace-pre-wrap text-[12.5px] leading-relaxed text-[var(--color-text-primary)]">
      {blocks.map((b, i) =>
        b.highlight ? (
          <mark
            key={i}
            className="rounded px-0.5"
            style={{
              background: "var(--color-background-warning)",
              color: "var(--color-text-warning)",
              fontWeight: 500,
            }}
          >
            {b.text}
          </mark>
        ) : (
          <span key={i}>{b.text}</span>
        ),
      )}
    </div>
  );
}

function ConfidenceBadge({ pct }: { pct: number }) {
  const rounded = Math.round(pct);
  const color =
    pct >= 0.9
      ? "var(--color-text-success)"
      : pct >= 0.75
        ? "var(--color-text-warning)"
        : "var(--color-text-danger)";
  return (
    <span className="font-medium" style={{ color }}>
      {rounded}%
    </span>
  );
}

export function DocSourceViewer({ taskId, onClose }: Props) {
  const queryClient = useQueryClient();
  const [overrideValue, setOverrideValue] = useState<string | null>(null);

  const sourceQuery = useQuery<ExtractionSource, Error>({
    queryKey: ["review-task", taskId, "source"],
    queryFn: () => api.getReviewTaskSource(taskId),
  });

  const accept = useMutation({
    mutationFn: () => api.acceptReviewTask(taskId),
    onSuccess: async (task) => {
      await queryClient.invalidateQueries({ queryKey: ["review-tasks"] });
      toast.success("Extraction accepted", {
        description: `${task.loan.reference} · ${humanizeField(task.extraction.field_name)} confirmed.`,
      });
      onClose();
    },
    onError: (e) =>
      toast.error("Accept failed", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  const override = useMutation({
    mutationFn: (value: string) => api.overrideReviewTask(taskId, value),
    onSuccess: async (task) => {
      await queryClient.invalidateQueries({ queryKey: ["review-tasks"] });
      toast.success("Extraction overridden", {
        description: `${task.loan.reference} · new value saved. Counts as ground truth for the next eval cycle.`,
      });
      onClose();
    },
    onError: (e) =>
      toast.error("Override failed", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  const source = sourceQuery.data;
  const ext = source?.extraction;
  const quote = ext?.source_span?.quote;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      role="dialog"
      aria-modal="true"
      onClick={onClose}
    >
      <div
        className="flex w-full max-w-5xl flex-col gap-2.5"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Top bar */}
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-[var(--color-background-secondary)] px-3.5 py-2 text-[12px] text-[var(--color-text-secondary)]">
          <div className="flex items-center gap-2">
            <IconFileText size={14} />
            <span>
              {source?.document.filename ?? "—"}
              {source && (
                <span className="ml-2 text-[var(--color-text-tertiary)]">
                  · {source.loan.reference}
                </span>
              )}
            </span>
          </div>
          {/* Single Close affordance. The decorative prev/next/"Full
              doc" trio that used to live here was permanently disabled
              against the synthetic corpus — dead UI flagged in the
              audit, removed. When we ship real multi-page documents
              they'll come back as IconButtons. */}
          <IconButton label="Close" Icon={IconX} onClick={onClose} />
        </div>

        {/* Split: doc on left, meta on right */}
        <div className="grid grid-cols-[1.55fr_1fr] gap-3">
          <div className="max-h-[70vh] overflow-auto rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] p-4">
            {sourceQuery.isPending && (
              <p className="text-xs text-[var(--color-text-tertiary)]">Loading document…</p>
            )}
            {sourceQuery.error && (
              <p className="text-xs text-[var(--color-text-danger)]">
                {sourceQuery.error.message}
              </p>
            )}
            {source && (
              <HighlightedText
                text={source.document_text || "(document has no extractable text)"}
                quote={quote}
              />
            )}
          </div>

          <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] p-3.5">
            {ext ? (
              <>
                <p className="text-[11px] text-[var(--color-text-secondary)]">
                  Extracted value
                </p>
                <p className="text-[18px] font-medium">
                  {overrideValue ?? ext.value}
                </p>
                <p className="mb-3 text-[12px] text-[var(--color-text-secondary)]">
                  {humanizeField(ext.field_name)}
                </p>

                {quote && (
                  <p
                    className="mb-3 rounded-md bg-[var(--color-background-secondary)] px-3 py-2 text-[12px] italic leading-relaxed text-[var(--color-text-primary)]"
                    style={{ fontFamily: "var(--font-serif)" }}
                  >
                    &ldquo;{quote}&rdquo;
                  </p>
                )}

                <div className="flex flex-col gap-1.5">
                  <Row
                    k="Confidence"
                    v={<ConfidenceBadge pct={ext.confidence} />}
                  />
                  <Row
                    k="Status"
                    v={
                      <Pill variant={ext.status === "accepted" ? "success" : "warn"}>
                        {humanizeExtractionStatus(ext.status)}
                      </Pill>
                    }
                  />
                  <Row k="Method" v="LLM extraction" />
                </div>

                {overrideValue !== null && (
                  <div className="mt-3">
                    <p className="mb-1 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
                      Override
                    </p>
                    <input
                      type="text"
                      value={overrideValue}
                      onChange={(e) => setOverrideValue(e.target.value)}
                      autoFocus
                      className="form-input-on-card"
                    />
                  </div>
                )}

                {override.error && (
                  <p className="mt-2 text-[11px] text-[var(--color-text-danger)]">
                    {override.error.message}
                  </p>
                )}
                {accept.error && (
                  <p className="mt-2 text-[11px] text-[var(--color-text-danger)]">
                    {accept.error.message}
                  </p>
                )}

                <div className="mt-4 flex gap-1.5">
                  {overrideValue === null ? (
                    <>
                      <button
                        onClick={() => accept.mutate()}
                        disabled={accept.isPending}
                        className="flex flex-1 items-center justify-center gap-1.5 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 py-1.5 text-[11px] font-medium hover:bg-[var(--color-background-secondary)] disabled:opacity-50"
                      >
                        <IconCheck size={12} />
                        {accept.isPending ? "Accepting…" : "Accept"}
                      </button>
                      <button
                        onClick={() => setOverrideValue(ext.value)}
                        className="flex flex-1 items-center justify-center gap-1.5 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 py-1.5 text-[11px] font-medium hover:bg-[var(--color-background-secondary)]"
                      >
                        <IconEdit size={12} /> Override
                      </button>
                    </>
                  ) : (
                    <>
                      <SecondaryButton
                        size="sm"
                        className="flex-1 justify-center"
                        onClick={() => setOverrideValue(null)}
                      >
                        Cancel
                      </SecondaryButton>
                      <PrimaryButton
                        size="sm"
                        className="flex-1 justify-center"
                        onClick={() =>
                          overrideValue && override.mutate(overrideValue.trim())
                        }
                        disabled={
                          override.isPending ||
                          !overrideValue ||
                          overrideValue.trim() === ext.value
                        }
                      >
                        {override.isPending ? "Saving…" : "Save override"}
                      </PrimaryButton>
                    </>
                  )}
                </div>
              </>
            ) : (
              <p className="text-xs text-[var(--color-text-tertiary)]">Loading…</p>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between border-b-[0.5px] border-[var(--color-border-tertiary)] py-1.5 text-[12px] last:border-b-0">
      <span className="text-[var(--color-text-secondary)]">{k}</span>
      <span>{v}</span>
    </div>
  );
}
