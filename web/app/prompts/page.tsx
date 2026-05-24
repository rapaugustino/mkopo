"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  IconChevronRight,
  IconClock,
  IconPencil,
} from "@tabler/icons-react";
import { api, type PromptSummary } from "@/lib/api";
import { BrandHeader } from "@/app/components/BrandHeader";
import { Pill } from "@/app/components/Pill";
import { Skeleton } from "@/app/components/Skeleton";

/**
 * System prompts management — list view.
 *
 * Every prompt the agents send to the LLM lives in the ``prompts``
 * table, indexed by a stable identifier (e.g. ``intake.draft_doc_request.personal``).
 * This page is the index of those identifiers; clicking one opens
 * the detail page with the current body, full version history, and
 * edit controls.
 *
 * Why a dedicated page rather than a settings tab: the underwriting
 * team needs a clear "this is where I tune what the AI says" surface
 * separate from observability ("what did it do") and eval ("how
 * accurately did it do it"). Three pages, three jobs.
 */
export default function PromptsListPage() {
  const query = useQuery<PromptSummary[], Error>({
    queryKey: ["prompts"],
    queryFn: () => api.listPrompts(),
    // Slow refresh — prompt edits are rare, and the user lands here
    // intentionally rather than passing through.
    refetchInterval: 60_000,
  });

  return (
    <div className="flex flex-col gap-3">
      <BrandHeader
        title="Prompts"
        sub="System prompts used by every agent · edit + version + roll back"
      />

      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <p className="mb-3 text-[12px] text-[var(--color-text-secondary)]">
          Each row is one identifier the runtime looks up. Edits create
          a new version and (if activated) become the body the next agent
          run uses — no redeploy. The code-default body is always
          available as a "Restore default" button on the detail page.
        </p>

        {query.isPending ? (
          <RowSkeletons />
        ) : query.error ? (
          <p className="text-[12px] text-[var(--color-text-danger)]">
            Couldn&apos;t load prompts: {query.error.message}
          </p>
        ) : (
          <div className="flex flex-col">
            {(query.data ?? []).map((row) => (
              <PromptRow key={row.identifier} row={row} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function PromptRow({ row }: { row: PromptSummary }) {
  // "code default" — the identifier is in the registry but no DB row
  // exists yet (only possible right after a code add before the
  // startup seed has run, or if someone DELETEd from the table by
  // hand). The runtime still has a body to use; just not one the UI
  // can show history for.
  const onCodeDefault = row.active_version == null;
  return (
    <Link
      href={`/prompts/${encodeURIComponent(row.identifier)}`}
      className="flex items-center gap-3 border-t-[0.5px] border-[var(--color-border-tertiary)] py-2.5 text-[12.5px] hover:bg-[var(--color-background-secondary)] first:border-t-0"
    >
      <div className="min-w-0 flex-1">
        <p className="truncate font-medium">{row.label}</p>
        <p className="mt-0.5 truncate text-[11.5px] text-[var(--color-text-secondary)]">
          {row.description}
        </p>
        <p className="mt-1 truncate font-mono text-[10.5px] text-[var(--color-text-tertiary)]">
          {row.identifier}
        </p>
      </div>
      <div className="flex items-center gap-3">
        {onCodeDefault ? (
          <Pill variant="neutral">code default</Pill>
        ) : (
          <Pill variant="success">v{row.active_version}</Pill>
        )}
        <span className="hidden text-[11px] text-[var(--color-text-tertiary)] sm:inline-block">
          {row.n_versions} version{row.n_versions === 1 ? "" : "s"}
        </span>
        {row.active_at && (
          <span className="hidden items-center gap-1 text-[11px] text-[var(--color-text-tertiary)] md:inline-flex">
            <IconClock size={11} />
            {relativeTime(row.active_at)}
          </span>
        )}
        <IconChevronRight
          size={14}
          className="text-[var(--color-text-tertiary)]"
        />
      </div>
    </Link>
  );
}

function RowSkeletons() {
  return (
    <div className="flex flex-col gap-2 py-2">
      {[0, 1, 2, 3, 4, 5].map((i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton width="w-48" height="h-3" />
          <Skeleton width="w-24" height="h-3" />
          <Skeleton width="w-16" height="h-3" />
        </div>
      ))}
    </div>
  );
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const sec = Math.floor(diff / 1000);
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  if (sec < 86400 * 30) return `${Math.floor(sec / 86400)}d ago`;
  return new Date(iso).toLocaleDateString();
}

// IconPencil is imported but unused above — kept to match the
// navigation icon. Silence the linter by referencing it.
void IconPencil;
