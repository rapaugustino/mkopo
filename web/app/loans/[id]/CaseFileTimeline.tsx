"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { IconNote, IconSend, IconSparkles } from "@tabler/icons-react";
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
  if (t === "agent" || t === "user" || t === "system") return t;
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
    case "stage_transition":
      return {
        ...base,
        intent: "default",
        actorLabel: `Stage: ${humanizeStage((p.from ?? p.from_stage) as string | undefined)} → ${humanizeStage((p.to ?? p.to_stage) as string | undefined)}`,
        body: (p.reason as string) ?? "",
      };
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
    default:
      // Unknown action — humanise the action verb so we never leak
      // snake_case into the timeline, and keep raw JSON as the body for
      // diagnostic visibility.
      return {
        ...base,
        intent: "default",
        actorLabel: humanizeAuditAction(e.action),
        body: Object.keys(p).length > 0 ? JSON.stringify(p) : "",
      };
  }
}

function Composer({ loanId }: { loanId: string }) {
  const [text, setText] = useState("");
  const queryClient = useQueryClient();
  const addNote = useMutation({
    mutationFn: (body: string) => api.addNote(loanId, body, "internal_note"),
    onSuccess: () => {
      setText("");
      queryClient.invalidateQueries({ queryKey: ["loan", loanId, "audit"] });
    },
  });

  return (
    <div className="rounded-md bg-[var(--color-background-secondary)] p-3">
      <textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        placeholder="Reply to borrower or add an internal note…"
        disabled={addNote.isPending}
        rows={2}
        className="block w-full resize-y rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 py-2 text-[13px] text-[var(--color-text-primary)] placeholder:text-[var(--color-text-tertiary)] focus:border-[var(--color-brand)] focus:outline-none disabled:opacity-50"
      />
      <div className="mt-2 flex flex-wrap items-center justify-between gap-2">
        <button
          disabled
          title="LLM drafting wired in a later phase"
          className="flex items-center gap-1 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2.5 py-1 text-[11px] text-[var(--color-text-tertiary)] disabled:cursor-not-allowed"
        >
          <IconSparkles size={12} /> Draft with AI
        </button>
        <div className="flex gap-1.5">
          <button
            onClick={() => addNote.mutate(text.trim())}
            disabled={addNote.isPending || !text.trim()}
            className="flex items-center gap-1 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2.5 py-1 text-[11px] text-[var(--color-text-primary)] hover:bg-[var(--color-background-secondary)] disabled:opacity-50"
          >
            <IconNote size={12} /> Internal note
          </button>
          <PrimaryButton
            size="sm"
            Icon={IconSend}
            onClick={() => addNote.mutate(text.trim())}
            disabled={addNote.isPending || !text.trim()}
          >
            {addNote.isPending ? "Sending…" : "Send"}
          </PrimaryButton>
        </div>
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
