"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { IconNote, IconSend } from "@tabler/icons-react";
import { toast } from "sonner";
import { api, type AuditEvent } from "@/lib/api";
import {
  humanizeAuditAction,
  humanizeRisk,
  humanizeStage,
  titleCase,
} from "@/lib/humanize";
import { EventRow } from "@/app/components/EventRow";
import type { ActorType, EventIntent } from "@/app/components/ActorIcon";
import { Pill, type PillVariant } from "@/app/components/Pill";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { QuoteBlock } from "@/app/components/QuoteBlock";
import { SectionLabel } from "@/app/components/SectionLabel";
import { SecondaryButton } from "@/app/components/SecondaryButton";

interface Props {
  loanId: string;
  events: AuditEvent[];
}

interface NormalisedEvent {
  id: string;
  actor: ActorType;
  intent: EventIntent;
  actorLabel: string;
  body?: string;
  quoteCaption?: string;
  quoteBody?: string;
  pills?: Array<{ variant: PillVariant; label: string }>;
  time: string;
}

/** Pretty file-size formatter shared by audit-event renderers.
 *  Keeps the timeline rows tight ("418 KB", "2.1 MB") instead of the
 *  unreadable raw byte count. */
function formatBytes(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  if (bytes >= 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${bytes} B`;
}

function shortRelative(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diff / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}d ago`;
  return new Date(iso).toLocaleDateString();
}

function asActorType(t: string): ActorType {
  if (
    t === "agent" ||
    t === "user" ||
    t === "system" ||
    t === "borrower"
  ) {
    return t;
  }
  return "system";
}

/**
 * Normalises a raw audit_event into a typed shape the timeline can render
 * without leaking JSON. New action types just need a case here.
 *
 * Why this lives in the component rather than the backend: the audit log
 * is the source of truth for *what happened*; this is purely how we *show*
 * it. Two clients (a CLI tool, a compliance export) might present the
 * same event differently.
 */
function normalise(e: AuditEvent): NormalisedEvent {
  const actor = asActorType(e.actor_type);
  const p = (e.payload ?? {}) as Record<string, unknown>;

  const base = {
    id: e.id,
    actor,
    time: shortRelative(e.created_at),
  };

  switch (e.action) {
    case "loan_created":
    case "seed_loan_created":
      return {
        ...base,
        intent: "intake",
        actorLabel: "Application received",
        body: p.source ? `via ${p.source}` : "",
      };
    case "document_uploaded":
      return {
        ...base,
        intent: "default",
        actorLabel: "Document uploaded",
        body: (p.filename as string) ?? "",
        pills:
          typeof p.chunks_embedded === "number"
            ? [{ variant: "neutral", label: `${p.chunks_embedded} chunks embedded` }]
            : undefined,
      };
    case "stage_changed":
    case "stage_transition": {
      // A human moved the loan from one stage to another and recorded
      // their reasoning. The reason is the *most important* part of
      // the row from an audit standpoint — promote it to a QuoteBlock
      // so it visually reads as a stated rationale, not a footnote.
      const reason = typeof p.reason === "string" ? p.reason : "";
      return {
        ...base,
        intent: "default",
        actorLabel: `Stage: ${humanizeStage((p.from ?? p.from_stage) as string | undefined)} → ${humanizeStage((p.to ?? p.to_stage) as string | undefined)}`,
        quoteBody: reason || undefined,
        quoteCaption: reason ? "Reason" : undefined,
      };
    }
    case "extraction_complete":
      return {
        ...base,
        actor: "agent",
        intent: "extract",
        actorLabel: "Mkopo Lens AI",
        body: `Extracted ${p.n_extracted ?? "?"} of ${p.n_required ?? "?"} required fields.`,
      };
    case "underwriting_complete":
      return {
        ...base,
        actor: "agent",
        intent: "summarise",
        actorLabel: "Mkopo Lens AI",
        body: `Underwriting summary generated — recommendation: ${
          p.recommendation ? titleCase(p.recommendation as string) : "—"
        }.`,
        pills:
          typeof p.risk_band === "string"
            ? [
                {
                  variant:
                    p.risk_band === "low"
                      ? "success"
                      : p.risk_band === "med"
                        ? "warn"
                        : "danger",
                  label: `Risk: ${humanizeRisk(p.risk_band)}`,
                },
              ]
            : undefined,
        quoteCaption: "Rationale",
        quoteBody: typeof p.rationale === "string" ? (p.rationale as string) : undefined,
      };
    case "decision_complete":
      return {
        ...base,
        actor: "agent",
        intent: "decide",
        actorLabel: "Mkopo Lens AI",
        body: `Decision: ${
          p.path ? titleCase(p.path as string) : "—"
        }${
          typeof p.confidence === "number"
            ? ` (${Math.round((p.confidence as number) * 100)}% confidence)`
            : ""
        }.`,
        quoteCaption: "Verdict",
        quoteBody: typeof p.verdict === "string" ? (p.verdict as string) : undefined,
      };
    case "send_email":
      return {
        ...base,
        actor: actor === "agent" ? "user" : actor,
        intent: "send",
        actorLabel: p.drafted_by_agent ? "Doc request sent · drafted by AI" : "Doc request sent",
        body: typeof p.to === "string" ? `To ${p.to}` : "",
        quoteCaption: typeof p.subject === "string" ? `Subject: ${p.subject}` : undefined,
        quoteBody: typeof p.body_text === "string" ? (p.body_text as string) : undefined,
      };
    case "inbound_email":
      return {
        ...base,
        actor: "borrower",
        intent: "reply",
        actorLabel: "Borrower replied",
        body: typeof p.from === "string" ? `from ${p.from}` : "",
        quoteCaption: typeof p.subject === "string" ? p.subject : undefined,
        quoteBody: typeof p.body_text === "string" ? (p.body_text as string) : undefined,
      };
    case "internal_note":
      return {
        ...base,
        actor: "user",
        intent: "note",
        actorLabel: "Internal note",
        quoteBody: typeof p.body_text === "string" ? (p.body_text as string) : undefined,
      };

    // ---- Borrower-portal events ----------------------------------------
    //
    // These come from the self-service application surface. We render
    // them with the borrower colour palette and structured chips so the
    // raw JSON payload never leaks through to the timeline body.

    case "borrower_applied":
      return {
        ...base,
        actor: "borrower",
        intent: "reply",
        actorLabel: "Borrower applied via portal",
        body: typeof p.borrower === "string" ? (p.borrower as string) : "",
        pills: [
          ...(p.amount
            ? [
                {
                  variant: "info" as PillVariant,
                  label: `$${Number(p.amount).toLocaleString()}`,
                },
              ]
            : []),
          ...(p.loan_type
            ? [
                {
                  variant: "neutral" as PillVariant,
                  label: titleCase(p.loan_type as string),
                },
              ]
            : []),
          ...(p.property_type
            ? [
                {
                  variant: "neutral" as PillVariant,
                  label: titleCase(p.property_type as string),
                },
              ]
            : []),
          ...(Array.isArray(p.guarantors) && (p.guarantors as unknown[]).length > 0
            ? [
                {
                  variant: "neutral" as PillVariant,
                  label: `${(p.guarantors as unknown[]).length} guarantor${
                    (p.guarantors as unknown[]).length === 1 ? "" : "s"
                  }`,
                },
              ]
            : []),
        ],
        quoteCaption:
          typeof p.property_address === "string"
            ? (p.property_address as string)
            : undefined,
        quoteBody:
          typeof p.purpose === "string" && (p.purpose as string).length > 0
            ? (p.purpose as string)
            : undefined,
      };
    case "borrower_document_uploaded":
      return {
        ...base,
        actor: "borrower",
        intent: "default",
        actorLabel: "Borrower uploaded a document",
        body: typeof p.filename === "string" ? (p.filename as string) : "",
        pills: [
          ...(typeof p.page_count === "number"
            ? [
                {
                  variant: "neutral" as PillVariant,
                  label: `${p.page_count} page${p.page_count === 1 ? "" : "s"}`,
                },
              ]
            : []),
          ...(typeof p.size_bytes === "number"
            ? [
                {
                  variant: "neutral" as PillVariant,
                  label: formatBytes(p.size_bytes as number),
                },
              ]
            : []),
          ...(typeof p.pages_needing_ocr === "number" &&
          (p.pages_needing_ocr as number) > 0
            ? [
                {
                  variant: "warn" as PillVariant,
                  label: `${p.pages_needing_ocr} need OCR`,
                },
              ]
            : []),
          ...(typeof p.chunks_embedded === "number" &&
          (p.chunks_embedded as number) > 0
            ? [
                {
                  variant: "success" as PillVariant,
                  label: `${p.chunks_embedded} chunks indexed`,
                },
              ]
            : []),
        ],
      };

    // ---- Operational / system events -----------------------------------

    case "autonomy_changed":
      return {
        ...base,
        intent: "default",
        actorLabel: "Autonomy mode changed",
        body:
          typeof p.from === "string" && typeof p.to === "string"
            ? `${titleCase(p.from as string)} → ${titleCase(p.to as string)}`
            : "",
        quoteBody:
          typeof p.reason === "string" ? (p.reason as string) : undefined,
        quoteCaption: "Reason",
      };
    case "orchestrator_advanced":
      return {
        ...base,
        actor: "system",
        intent: "default",
        actorLabel: "Orchestrator advanced stage",
        body:
          typeof p.to_stage === "string"
            ? `Auto-advanced to ${humanizeStage(p.to_stage as string)} after ${p.after ?? "previous step"}.`
            : "",
      };

    default:
      // Unknown action — humanise the verb and render the payload as
      // "Key: value · Key: value" so we never leak snake_case or raw
      // JSON to operators. Diagnostic deep-dive lives in Observability.
      return {
        ...base,
        intent: "default",
        actorLabel: humanizeAuditAction(e.action),
        body: humanizePayload(p),
      };
  }
}

/**
 * Compact "Key: value · Key: value" rendering of an arbitrary audit
 * payload. Used by the default case so unknown audit actions still
 * read like a friendly log line rather than a JSON dump.
 *
 *  - Keys are humanised via {@link titleCase} (so ``to_stage`` reads
 *    "To Stage", not the canonical "Stage" we'd give the field
 *    elsewhere — for unknown actions we don't want to guess at the
 *    intended label).
 *  - String values get the same treatment when they look like a
 *    snake_case enum (``decline_with_reasons`` → "Decline With
 *    Reasons"). Numbers, booleans, dates pass through.
 *  - Nested objects/arrays are summarised as "{n keys}" / "[n items]"
 *    rather than recursed into — deep nesting belongs in
 *    Observability, not in the timeline.
 *  - At most 4 entries; further keys are summarised as "+N more" so
 *    the row stays one line.
 */
function humanizePayload(p: Record<string, unknown>): string {
  const entries = Object.entries(p);
  if (entries.length === 0) return "";
  const formatted: string[] = [];
  for (const [k, v] of entries.slice(0, 4)) {
    formatted.push(`${titleCase(k)}: ${formatScalar(v)}`);
  }
  if (entries.length > 4) {
    formatted.push(`+${entries.length - 4} more`);
  }
  return formatted.join(" · ");
}

function formatScalar(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "string") {
    // Snake_case-looking strings get title-cased; everything else
    // passes through (we don't want to capitalise a free-text reason).
    return /^[a-z0-9]+(_[a-z0-9]+)+$/.test(v) ? titleCase(v) : v;
  }
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return `[${v.length} items]`;
  if (typeof v === "object") return `{${Object.keys(v as object).length} keys}`;
  return String(v);
}

/** Composer for adding entries to the case-file timeline.
 *
 *  Two real actions:
 *  - **Internal note** (``internal_note``) — visible to underwriters
 *    only; lands as the secondary button.
 *  - **Send to borrower** (``borrower_reply``) — fires off as a
 *    borrower-facing message; lands as the primary button.
 *
 *  These call the same endpoint with different ``kind`` values so the
 *  audit row colours differently and downstream filters can split
 *  them apart. The previous version had a single mutation invoked
 *  from both buttons (so they were duplicates) and a dead "Draft
 *  with AI" button that was permanently disabled — both were flagged
 *  in the UI audit and are gone now.
 */
type ComposerKind = "internal_note" | "borrower_reply";

function Composer({ loanId }: { loanId: string }) {
  const [text, setText] = useState("");
  const queryClient = useQueryClient();
  const addNote = useMutation({
    mutationFn: ({ body, kind }: { body: string; kind: ComposerKind }) =>
      api.addNote(loanId, body, kind),
    onSuccess: (_data, variables) => {
      setText("");
      queryClient.invalidateQueries({ queryKey: ["loan", loanId, "audit"] });
      if (variables.kind === "borrower_reply") {
        toast.success("Message sent to borrower", {
          description: "Saved to the case file audit log.",
        });
      } else {
        toast.success("Internal note added", {
          description: "Visible to underwriters only.",
        });
      }
    },
    onError: (e) =>
      toast.error("Couldn't post note", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  const post = (kind: ComposerKind) =>
    addNote.mutate({ body: text.trim(), kind });
  const submitting = addNote.isPending;

  return (
    <div className="rounded-md bg-[var(--color-background-secondary)] p-3">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Reply to borrower or add an internal note…"
        disabled={submitting}
        rows={2}
        className="form-input-on-card block resize-y"
      />
      <div className="mt-2 flex flex-wrap items-center justify-end gap-1.5">
        <SecondaryButton
          size="sm"
          Icon={IconNote}
          onClick={() => post("internal_note")}
          disabled={submitting || !text.trim()}
        >
          {submitting ? "Saving…" : "Internal note"}
        </SecondaryButton>
        <PrimaryButton
          size="sm"
          Icon={IconSend}
          onClick={() => post("borrower_reply")}
          disabled={submitting || !text.trim()}
        >
          {submitting ? "Sending…" : "Send to borrower"}
        </PrimaryButton>
      </div>
      {addNote.error && (
        <p className="mt-2 text-[11px] text-[var(--color-text-danger)]">
          {addNote.error.message}
        </p>
      )}
    </div>
  );
}

export function CaseFileTimeline({ loanId, events }: Props) {
  const normalised = events.map(normalise);
  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
      <div className="border-b-[0.5px] border-[var(--color-border-tertiary)] px-4 pt-3 pb-1">
        <SectionLabel
          dense
          trailing={
            <>
              {events.length} event{events.length === 1 ? "" : "s"} · newest first
            </>
          }
        >
          Activity
        </SectionLabel>
      </div>

      <div className="divide-y-[0.5px] divide-[var(--color-border-tertiary)] px-4">
        {normalised.map((n) => (
          <EventRow
            key={n.id}
            actor={n.actor}
            intent={n.intent}
            actorLabel={n.actorLabel}
            time={n.time}
            pills={
              n.pills && n.pills.length > 0 ? (
                <div className="flex flex-wrap gap-1">
                  {n.pills.map((p, i) => (
                    <Pill key={i} variant={p.variant} size="xs">
                      {p.label}
                    </Pill>
                  ))}
                </div>
              ) : undefined
            }
            body={n.body || undefined}
            detail={
              n.quoteBody ? (
                <QuoteBlock caption={n.quoteCaption}>{n.quoteBody}</QuoteBlock>
              ) : undefined
            }
          />
        ))}
        {normalised.length === 0 && (
          <p className="px-2 py-6 text-center text-sm text-[var(--color-text-tertiary)]">
            No activity yet.
          </p>
        )}
      </div>

      <div className="border-t-[0.5px] border-[var(--color-border-tertiary)] p-3">
        <Composer loanId={loanId} />
      </div>
    </div>
  );
}
