"use client";

import { use, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  IconArrowLeft,
  IconCheck,
  IconDeviceFloppy,
  IconHistory,
  IconRefresh,
  IconRestore,
} from "@tabler/icons-react";
import { toast } from "sonner";
import { api, type PromptDetail, type PromptVersion } from "@/lib/api";
import { BrandHeader } from "@/app/components/BrandHeader";
import { Pill } from "@/app/components/Pill";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SecondaryButton } from "@/app/components/SecondaryButton";
import { Skeleton } from "@/app/components/Skeleton";

/**
 * Prompt detail — edit + version history + activate.
 *
 * Three regions:
 *
 *  1. **Header** — back link, label, identifier, current active pill.
 *  2. **Editor** — textarea bound to local state, with Save (creates
 *     a new version + activates) and Restore default buttons.
 *  3. **Version history** — list of every stored version with
 *     activate / view buttons. The active version is highlighted;
 *     selecting an older one loads its body into the editor (useful
 *     for "edit from a prior known-good version").
 *
 * The Save button always *activates* the new version because that's
 * what users actually want — the alternative ("create draft without
 * activating") is a power-user case we can ship later.
 */
export default function PromptDetailPage({
  params,
}: {
  // Next 16 changed the params shape to a Promise. Unwrap with React.use().
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const identifier = decodeURIComponent(id);
  const queryClient = useQueryClient();

  const detailQuery = useQuery<PromptDetail, Error>({
    queryKey: ["prompt", identifier],
    queryFn: () => api.getPromptDetail(identifier),
  });
  const detail = detailQuery.data;

  // Editor local state. The seed-once pattern: when detail first
  // loads, fill the textarea with the active version's body (or the
  // code default). After that the user owns the textarea — query
  // refetches don't overwrite in-progress edits. React-19's
  // "set state during render with a guard" avoids the
  // react-hooks/set-state-in-effect warning and commits the seed
  // value in a single paint instead of flashing the empty default.
  const [draft, setDraft] = useState<string>("");
  const [changeNote, setChangeNote] = useState<string>("");
  const [seededFromDetail, setSeededFromDetail] = useState<PromptDetail | null>(
    null,
  );
  if (detail && seededFromDetail !== detail && draft === "") {
    setSeededFromDetail(detail);
    const active = detail.versions.find((v) => v.is_active);
    setDraft(active?.body ?? detail.default_body);
  }

  const activeVersion = useMemo(
    () => detail?.versions.find((v) => v.is_active),
    [detail],
  );
  const isDirty = useMemo(() => {
    if (!detail) return false;
    const baseline = activeVersion?.body ?? detail.default_body;
    return draft !== baseline;
  }, [draft, activeVersion, detail]);

  const save = useMutation({
    mutationFn: () =>
      api.createPromptVersion(identifier, {
        body: draft,
        change_note: changeNote.trim(),
        activate: true,
      }),
    onSuccess: () => {
      toast.success("Saved new version", {
        description: "The runtime will pick it up on the next agent call.",
      });
      setChangeNote("");
      queryClient.invalidateQueries({ queryKey: ["prompt", identifier] });
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
    },
    onError: (e) => {
      toast.error("Couldn't save", {
        description: e instanceof Error ? e.message : String(e),
      });
    },
  });

  const activate = useMutation({
    mutationFn: (version: number) =>
      api.activatePromptVersion(identifier, version),
    onSuccess: (row) => {
      toast.success(`Activated v${row.version}`);
      setDraft(row.body);
      queryClient.invalidateQueries({ queryKey: ["prompt", identifier] });
      queryClient.invalidateQueries({ queryKey: ["prompts"] });
    },
    onError: (e) =>
      toast.error("Couldn't activate", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  if (detailQuery.error) {
    return (
      <div className="flex flex-col gap-3">
        <BrandHeader title="Prompts" sub="Detail" />
        <p className="text-[12.5px] text-[var(--color-text-danger)]">
          Couldn&apos;t load: {detailQuery.error.message}
        </p>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <header className="flex items-start justify-between gap-3">
        <div className="flex flex-col gap-1.5">
          <Link
            href="/prompts"
            className="inline-flex items-center gap-1 text-[11.5px] text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]"
          >
            <IconArrowLeft size={11} />
            All prompts
          </Link>
          <h1 className="font-editorial text-[22px] tracking-tight">
            {detail?.label ?? <Skeleton width="w-64" height="h-5" />}
          </h1>
          {detail && (
            <p className="text-[12px] text-[var(--color-text-secondary)]">
              {detail.description}
            </p>
          )}
          <p className="font-mono text-[10.5px] text-[var(--color-text-tertiary)]">
            {identifier}
          </p>
        </div>
        {activeVersion && (
          <Pill variant="success">
            v{activeVersion.version} active
          </Pill>
        )}
      </header>

      {/* Editor */}
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <div className="mb-2 flex items-baseline justify-between gap-3">
          <p className="text-[12.5px] font-medium">Active body</p>
          <div className="flex items-center gap-2 text-[11px] text-[var(--color-text-secondary)]">
            <span>{draft.length.toLocaleString()} chars</span>
            {isDirty && (
              <Pill variant="warn">unsaved changes</Pill>
            )}
          </div>
        </div>

        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          disabled={!detail || save.isPending}
          rows={Math.max(10, Math.min(28, draft.split("\n").length + 2))}
          className="form-input w-full font-mono text-[12px] leading-relaxed"
          style={{ minHeight: 220 }}
          spellCheck={false}
        />

        <div className="mt-3 flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
          <label className="flex flex-1 flex-col gap-1">
            <span className="text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
              What changed?
            </span>
            <input
              type="text"
              value={changeNote}
              onChange={(e) => setChangeNote(e.target.value)}
              disabled={save.isPending}
              maxLength={512}
              placeholder="Short description for the audit log + history list"
              className="form-input"
            />
          </label>

          <div className="flex items-end gap-2">
            <SecondaryButton
              Icon={IconRestore}
              onClick={() => {
                if (!detail) return;
                setDraft(detail.default_body);
                if (!changeNote.trim()) {
                  setChangeNote("Restore code default");
                }
                toast.info("Restored code default — review and save to activate.");
              }}
              disabled={!detail || save.isPending}
            >
              Restore default
            </SecondaryButton>
            <PrimaryButton
              Icon={IconDeviceFloppy}
              onClick={() => save.mutate()}
              disabled={
                !detail ||
                save.isPending ||
                !isDirty ||
                changeNote.trim().length === 0
              }
            >
              {save.isPending ? "Saving…" : "Save & activate"}
            </PrimaryButton>
          </div>
        </div>

        {!isDirty && detail && detail.versions.length === 0 && (
          <p className="mt-2 text-[11px] text-[var(--color-text-tertiary)]">
            No DB versions yet — the runtime is using the code default
            shown above. Saving here will create v1.
          </p>
        )}
      </div>

      {/* Version history */}
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <p className="mb-3 flex items-center gap-1.5 text-[12.5px] font-medium">
          <IconHistory size={13} />
          Version history
          {detail && (
            <span className="text-[11px] text-[var(--color-text-tertiary)]">
              ({detail.versions.length})
            </span>
          )}
        </p>
        {detailQuery.isPending ? (
          <Skeleton width="w-full" height="h-24" />
        ) : detail && detail.versions.length === 0 ? (
          <p className="text-[12px] text-[var(--color-text-tertiary)]">
            No saved versions yet. The runtime is on the code default.
          </p>
        ) : (
          <div className="flex flex-col">
            {detail!.versions.map((v) => (
              <VersionRow
                key={v.id}
                version={v}
                isPending={activate.isPending}
                onLoadIntoEditor={(body) => {
                  setDraft(body);
                  toast.info(`Loaded v${v.version} into the editor`);
                }}
                onActivate={() => activate.mutate(v.version)}
              />
            ))}
          </div>
        )}
      </div>

      <p className="text-[11px] text-[var(--color-text-tertiary)]">
        Changes take effect on the next agent call (the runtime
        re-reads the active prompt cache after every save).
        <button
          type="button"
          onClick={() =>
            queryClient.invalidateQueries({
              queryKey: ["prompt", identifier],
            })
          }
          className="ml-1 inline-flex items-center gap-1 text-[var(--color-text-info)] hover:underline"
        >
          <IconRefresh size={10} />
          Reload
        </button>
      </p>
    </div>
  );
}


function VersionRow({
  version,
  isPending,
  onLoadIntoEditor,
  onActivate,
}: {
  version: PromptVersion;
  isPending: boolean;
  onLoadIntoEditor: (body: string) => void;
  onActivate: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  return (
    <div className="border-t-[0.5px] border-[var(--color-border-tertiary)] py-2 first:border-t-0">
      <div className="flex items-center gap-3 text-[12px]">
        <span className="w-[64px] font-medium tabular-nums">
          v{version.version}
        </span>
        {version.is_active ? (
          <Pill variant="success">active</Pill>
        ) : (
          <Pill variant="neutral">history</Pill>
        )}
        <span className="flex-1 truncate text-[var(--color-text-secondary)]">
          {version.change_note || (
            <em className="text-[var(--color-text-tertiary)]">no change note</em>
          )}
        </span>
        <span className="hidden text-[11px] text-[var(--color-text-tertiary)] sm:inline">
          {new Date(version.created_at).toLocaleString(undefined, {
            month: "short",
            day: "numeric",
            year: "numeric",
            hour: "2-digit",
            minute: "2-digit",
          })}
        </span>
        <button
          type="button"
          onClick={() => setExpanded((e) => !e)}
          className="text-[11px] text-[var(--color-text-info)] hover:underline"
        >
          {expanded ? "Hide" : "View"}
        </button>
        <button
          type="button"
          onClick={() => onLoadIntoEditor(version.body)}
          className="text-[11px] text-[var(--color-text-info)] hover:underline"
        >
          Load into editor
        </button>
        {!version.is_active && (
          <button
            type="button"
            onClick={onActivate}
            disabled={isPending}
            className="inline-flex items-center gap-1 rounded-md bg-[var(--color-background-secondary)] px-2 py-0.5 text-[11px] font-medium hover:bg-[var(--color-border-tertiary)] disabled:opacity-50"
          >
            <IconCheck size={11} />
            Activate
          </button>
        )}
      </div>
      {expanded && (
        <pre className="mt-2 max-h-96 overflow-auto rounded-md bg-[var(--color-background-secondary)] px-3 py-2 font-mono text-[11px] leading-relaxed whitespace-pre-wrap text-[var(--color-text-primary)]">
          {version.body}
        </pre>
      )}
    </div>
  );
}
