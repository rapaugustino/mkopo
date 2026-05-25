"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import {
  IconAlertOctagon,
  IconCheck,
  IconMessage2,
  IconThumbDown,
  IconThumbUp,
  IconTrash,
  IconX,
} from "@tabler/icons-react";
import { toast } from "sonner";
import {
  api,
  type Annotation,
  type AnnotationTargetKind,
  type AnnotationVerdict,
} from "@/lib/api";
import { Pill } from "@/app/components/Pill";

interface Props {
  targetKind: AnnotationTargetKind;
  targetId: string;
}


/**
 * Annotations panel — mounted inside LLMCallDrawer / AgentRunDrawer.
 *
 * Behaviour:
 *
 *   - Top row: three verdict buttons (good / bad / incorrect). Click
 *     opens an inline note textarea, submit posts the annotation.
 *   - Below: list of existing annotations for this target, newest
 *     first. Each row shows the verdict pill, the relative time,
 *     the note, and a delete button.
 *   - "bad" / "incorrect" verdicts that successfully auto-spawned a
 *     review_task render an inline "→ Review queue" deep link so
 *     the operator can jump straight there.
 *
 * The component owns its own queries so dropping it inside either
 * drawer is one JSX line — no state plumbing needed.
 */
export function AnnotationPanel({ targetKind, targetId }: Props) {
  const queryClient = useQueryClient();
  const [stagingVerdict, setStagingVerdict] = useState<
    AnnotationVerdict | null
  >(null);
  const [note, setNote] = useState("");

  const listQuery = useQuery<Annotation[], Error>({
    queryKey: ["annotations", targetKind, targetId],
    queryFn: () => api.listAnnotations(targetKind, targetId),
  });

  const create = useMutation({
    mutationFn: (verdict: AnnotationVerdict) =>
      api.createAnnotation({
        target_kind: targetKind,
        target_id: targetId,
        verdict,
        note: note.trim() || null,
      }),
    onSuccess: (row) => {
      setStagingVerdict(null);
      setNote("");
      queryClient.invalidateQueries({
        queryKey: ["annotations", targetKind, targetId],
      });
      // The drift / failures rollups on /eval consume annotations too.
      queryClient.invalidateQueries({ queryKey: ["eval-diagnostics"] });
      if (row.spawned_review_task_id) {
        toast.success("Annotation saved", {
          description: "Auto-added a follow-up to the review queue.",
        });
      } else {
        toast.success("Annotation saved");
      }
    },
    onError: (e) =>
      toast.error("Couldn't save annotation", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  const remove = useMutation({
    mutationFn: (id: string) => api.deleteAnnotation(id),
    onSuccess: () => {
      queryClient.invalidateQueries({
        queryKey: ["annotations", targetKind, targetId],
      });
      queryClient.invalidateQueries({ queryKey: ["eval-diagnostics"] });
      toast.success("Annotation deleted");
    },
    onError: (e) =>
      toast.error("Couldn't delete annotation", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  const rows = listQuery.data ?? [];

  return (
    <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 py-2.5">
      <p className="flex items-center gap-1.5 text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
        <IconMessage2 size={11} />
        Annotations
        {rows.length > 0 && (
          <span className="rounded bg-[var(--color-background-secondary)] px-1.5 py-px text-[10px] text-[var(--color-text-tertiary)] normal-case tracking-normal">
            {rows.length}
          </span>
        )}
      </p>

      {/* Verdict buttons. Each opens the note input below; the
          actual POST happens when the user clicks Save in the
          input. Keeps the affordance discoverable while still
          requiring a deliberate save click. */}
      <div className="mt-2 flex flex-wrap gap-1.5">
        <VerdictButton
          icon={IconThumbUp}
          label="Good"
          variant="success"
          active={stagingVerdict === "good"}
          onClick={() => {
            setStagingVerdict("good");
          }}
        />
        <VerdictButton
          icon={IconThumbDown}
          label="Bad"
          variant="warn"
          active={stagingVerdict === "bad"}
          onClick={() => {
            setStagingVerdict("bad");
          }}
        />
        <VerdictButton
          icon={IconAlertOctagon}
          label="Incorrect"
          variant="danger"
          active={stagingVerdict === "incorrect"}
          onClick={() => {
            setStagingVerdict("incorrect");
          }}
        />
      </div>

      {/* Staging input. Shown only after a verdict button is
          clicked, so the panel reads as cheap-to-engage rather
          than a always-open form. */}
      {stagingVerdict !== null && (
        <div className="mt-2 flex flex-col gap-2 rounded-md bg-[var(--color-background-secondary)] px-2.5 py-2.5">
          <p className="text-[11px] text-[var(--color-text-secondary)]">
            Recording <strong>{stagingVerdict}</strong> on this {humanKind(targetKind)}.
            {stagingVerdict !== "good" && (
              <span className="ml-1 text-[var(--color-text-tertiary)]">
                A follow-up review task will be opened automatically.
              </span>
            )}
          </p>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            disabled={create.isPending}
            rows={2}
            maxLength={4000}
            placeholder="Optional — what made it good / bad / incorrect?"
            className="form-input text-[12px]"
          />
          <div className="flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => {
                setStagingVerdict(null);
                setNote("");
              }}
              disabled={create.isPending}
              className="inline-flex items-center gap-1 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2.5 py-1 text-[11.5px] hover:bg-[var(--color-background-secondary)]"
            >
              <IconX size={11} />
              Cancel
            </button>
            <button
              type="button"
              onClick={() => create.mutate(stagingVerdict)}
              disabled={create.isPending}
              className="inline-flex items-center gap-1 rounded-md bg-[var(--color-brand)] px-2.5 py-1 text-[11.5px] font-medium disabled:opacity-50"
              style={{ color: "var(--color-brand-light)" }}
            >
              <IconCheck size={11} />
              {create.isPending ? "Saving…" : "Save annotation"}
            </button>
          </div>
        </div>
      )}

      {/* Existing annotations list. Empty state is intentionally
          quiet (just whitespace) — the verdict buttons above are
          what carries the affordance. */}
      {rows.length > 0 && (
        <ul className="mt-3 flex flex-col gap-1.5">
          {rows.map((a) => (
            <AnnotationRow
              key={a.id}
              annotation={a}
              onDelete={() => remove.mutate(a.id)}
              deleting={remove.isPending}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function humanKind(k: AnnotationTargetKind): string {
  return k.replace("_", " ");
}

function VerdictButton({
  icon: Icon,
  label,
  variant,
  active,
  onClick,
}: {
  icon: React.ComponentType<{ size?: number }>;
  label: string;
  variant: "success" | "warn" | "danger";
  active: boolean;
  onClick: () => void;
}) {
  const colour =
    variant === "success"
      ? "var(--color-text-success)"
      : variant === "warn"
        ? "var(--color-text-warning)"
        : "var(--color-text-danger)";
  return (
    <button
      type="button"
      onClick={onClick}
      className="inline-flex items-center gap-1 rounded-md border-[0.5px] px-2.5 py-1 text-[11.5px] font-medium transition-colors"
      style={{
        borderColor: active ? colour : "var(--color-border-tertiary)",
        color: colour,
        background: active
          ? "var(--color-background-secondary)"
          : "var(--color-background-primary)",
      }}
    >
      <Icon size={11} />
      {label}
    </button>
  );
}

function AnnotationRow({
  annotation,
  onDelete,
  deleting,
}: {
  annotation: Annotation;
  onDelete: () => void;
  deleting: boolean;
}) {
  const variant: "success" | "warn" | "danger" =
    annotation.verdict === "good"
      ? "success"
      : annotation.verdict === "bad"
        ? "warn"
        : "danger";
  return (
    <li className="flex flex-col gap-1 rounded-md bg-[var(--color-background-secondary)] px-2.5 py-2">
      <div className="flex items-center gap-2">
        <Pill variant={variant}>{annotation.verdict}</Pill>
        <span className="text-[10.5px] tabular-nums text-[var(--color-text-tertiary)]">
          {relativeTime(annotation.created_at)}
        </span>
        {annotation.spawned_review_task_id && (
          <Link
            href="/review-queue"
            className="text-[10.5px] text-[var(--color-text-info)] hover:underline"
          >
            → review queue
          </Link>
        )}
        <button
          type="button"
          onClick={onDelete}
          disabled={deleting}
          className="ml-auto inline-flex items-center gap-1 text-[10.5px] text-[var(--color-text-tertiary)] hover:text-[var(--color-text-danger)]"
          aria-label="Delete annotation"
        >
          <IconTrash size={10} />
        </button>
      </div>
      {annotation.note && (
        <p className="text-[11.5px] leading-relaxed text-[var(--color-text-primary)]">
          {annotation.note}
        </p>
      )}
    </li>
  );
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}
