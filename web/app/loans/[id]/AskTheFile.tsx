"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  IconMessages,
  IconSearch,
  IconSend,
  IconLayoutGrid,
} from "@tabler/icons-react";
import {
  api,
  type AskResponse,
  type CitedChunk,
  type ComparableLoan,
  type RiskBand,
} from "@/lib/api";
import { humanizeLoanType, humanizeRisk } from "@/lib/humanize";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SectionLabel } from "@/app/components/SectionLabel";

interface Props {
  loanId: string;
}

interface HistoryEntry {
  question: string;
  answer: AskResponse;
}

const RISK_COLOR: Record<RiskBand, string> = {
  low: "var(--color-text-success)",
  med: "var(--color-text-warning)",
  high: "var(--color-text-danger)",
};

function formatMoney(s: string): string {
  const n = Number(s);
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

function ComparableRow({ c }: { c: ComparableLoan }) {
  const pct = Math.round(c.similarity * 100);
  return (
    <Link
      href={`/loans/${c.loan_id}`}
      className="flex items-center justify-between gap-3 rounded-md px-2.5 py-2 text-xs hover:bg-[var(--color-background-secondary)]"
    >
      <div className="flex min-w-0 items-center gap-2">
        <span className="font-medium text-[var(--color-text-info)]">
          {c.reference}
        </span>
        <span className="truncate text-[var(--color-text-secondary)]">
          {c.borrower || "—"} · {humanizeLoanType(c.loan_type)} · {formatMoney(c.amount)}
        </span>
      </div>
      <div className="flex shrink-0 items-center gap-2">
        {c.risk_band && (
          <span
            className="inline-block h-1.5 w-1.5 rounded-full"
            style={{ background: RISK_COLOR[c.risk_band] }}
            title={`Risk band: ${humanizeRisk(c.risk_band)}`}
          />
        )}
        <span className="font-medium text-[var(--color-text-secondary)]">
          {pct}%
        </span>
      </div>
    </Link>
  );
}

function Citations({ citations }: { citations: CitedChunk[] }) {
  if (citations.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap gap-1.5">
      {citations.map((c, i) => (
        <span
          key={`${c.document_id}-${c.ordinal}`}
          title={c.content}
          className="cursor-help rounded px-1.5 py-0.5 text-[10px] font-medium"
          style={{
            background: "var(--color-background-info)",
            color: "var(--color-text-info)",
          }}
        >
          [{i + 1}] {c.filename}
          {c.ordinal > 0 ? ` · chunk ${c.ordinal + 1}` : ""}
          {" · "}
          {Math.round(c.similarity * 100)}%
        </span>
      ))}
    </div>
  );
}

export function AskTheFile({ loanId }: Props) {
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  const comparablesQuery = useQuery<ComparableLoan[], Error>({
    queryKey: ["loan", loanId, "comparables"],
    queryFn: () => api.getComparables(loanId),
  });

  const ask = useMutation({
    mutationFn: (q: string) => api.askLoan(loanId, q),
    onSuccess: (data, variables) => {
      setHistory((h) => [...h, { question: variables, answer: data }]);
      setQuestion("");
    },
  });

  const onSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const q = question.trim();
    if (!q || ask.isPending) return;
    ask.mutate(q);
  };

  const comparables = comparablesQuery.data ?? [];

  return (
    <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
      {/* Chat (2/3 width) */}
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3 md:col-span-2">
        <SectionLabel Icon={IconMessages}>Ask the file</SectionLabel>
        {history.length === 0 && !ask.isPending && (
          <p className="px-1 py-3 text-xs text-[var(--color-text-tertiary)]">
            Vector search across this loan&apos;s documents + comparable loan
            summaries. Try: &ldquo;What&apos;s the NOI on the appraisal?&rdquo;
            or &ldquo;Compare this deal to similar Tacoma multifamily.&rdquo;
          </p>
        )}
        <div className="flex flex-col gap-3">
          {history.map((entry, idx) => (
            <div key={idx} className="flex flex-col gap-2">
              <div className="self-end max-w-[80%] rounded-md bg-[var(--color-background-primary)] border-[0.5px] border-[var(--color-border-tertiary)] px-3 py-2 text-[13px]">
                {entry.question}
              </div>
              <div className="rounded-md bg-[var(--color-background-secondary)] px-3 py-2 text-[13px] leading-relaxed">
                {entry.answer.answer}
                <Citations citations={entry.answer.citations} />
              </div>
            </div>
          ))}
          {ask.isPending && (
            <div className="rounded-md bg-[var(--color-background-secondary)] px-3 py-2 text-[13px] text-[var(--color-text-tertiary)]">
              Thinking…
            </div>
          )}
          {ask.error && (
            <p className="rounded bg-[var(--color-background-danger)] px-3 py-2 text-xs text-[var(--color-text-danger)]">
              {ask.error.message}
            </p>
          )}
        </div>
        <form
          onSubmit={onSubmit}
          className="mt-3 flex items-center gap-2 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2.5 py-1.5"
        >
          <IconSearch size={14} className="text-[var(--color-text-tertiary)]" />
          <input
            type="text"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="Ask anything about this file…"
            disabled={ask.isPending}
            className="flex-1 bg-transparent text-[13px] outline-none placeholder:text-[var(--color-text-tertiary)] disabled:opacity-50"
          />
          <PrimaryButton
            type="submit"
            size="sm"
            Icon={IconSend}
            disabled={ask.isPending || !question.trim()}
          >
            Send
          </PrimaryButton>
        </form>
      </div>

      {/* Comparable loans (1/3 width) */}
      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel Icon={IconLayoutGrid}>Comparable loans</SectionLabel>
        {comparablesQuery.isPending && (
          <p className="text-xs text-[var(--color-text-tertiary)]">Loading…</p>
        )}
        {comparablesQuery.error && (
          <p className="text-xs text-[var(--color-text-danger)]">
            {comparablesQuery.error.message}
          </p>
        )}
        {!comparablesQuery.isPending && comparables.length === 0 && (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            No comparables yet — needs other underwritten loans to compare
            against. Run the underwriting agent on more seeded loans.
          </p>
        )}
        <div className="flex flex-col gap-0.5">
          {comparables.map((c) => (
            <ComparableRow key={c.loan_id} c={c} />
          ))}
        </div>
      </div>
    </div>
  );
}
