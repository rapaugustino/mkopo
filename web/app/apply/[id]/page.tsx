"use client";

import { use, useRef, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  IconArrowLeft,
  IconBuildingBank,
  IconCheck,
  IconCircleCheck,
  IconCircleDashed,
  IconCloudUpload,
  IconFileText,
  IconFlagCheck,
  IconGavel,
  IconLoader2,
  IconMicroscope,
  IconShieldCheck,
  IconX,
} from "@tabler/icons-react";
import { motion } from "motion/react";
import { toast } from "sonner";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

interface BorrowerStatus {
  loan_id: string;
  reference: string;
  stage: string;
  next_step: string;
  submitted_at: string;
  documents: { filename: string; uploaded_at: string; size_bytes: number }[];
}

const STAGES = [
  { key: "intake", label: "Application received", Icon: IconCircleCheck },
  { key: "underwriting", label: "Underwriting", Icon: IconMicroscope },
  { key: "decision", label: "Decision", Icon: IconGavel },
  { key: "conditions", label: "Conditions to close", Icon: IconShieldCheck },
  { key: "closing", label: "Closing", Icon: IconFlagCheck },
  { key: "approved", label: "Approved", Icon: IconCheck },
  { key: "servicing", label: "Servicing", Icon: IconBuildingBank },
];

const TERMINAL_DECLINED = "declined";

export default function ApplyStatusPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  const queryClient = useQueryClient();

  const statusQuery = useQuery<BorrowerStatus, Error>({
    queryKey: ["borrower-status", id],
    queryFn: async () => {
      const res = await fetch(
        `${API_URL}/api/v1/borrower-portal/loans/${id}/status`,
      );
      if (!res.ok) throw new Error(`Status fetch failed: ${res.status}`);
      return (await res.json()) as BorrowerStatus;
    },
    // Borrower expects to see updates without manual refresh as their
    // application moves through the lender's pipeline. 30s poll is
    // gentle enough not to be wasteful.
    refetchInterval: 30_000,
  });

  const upload = useMutation({
    mutationFn: async (file: File) => {
      const form = new FormData();
      form.append("file", file);
      const res = await fetch(
        `${API_URL}/api/v1/borrower-portal/loans/${id}/documents`,
        { method: "POST", body: form },
      );
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({
        queryKey: ["borrower-status", id],
      });
      toast.success("Document uploaded — your underwriter will see it.");
    },
    onError: (e) =>
      toast.error("Upload failed", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  const status = statusQuery.data;
  const stage = status?.stage ?? "intake";
  const declined = stage === TERMINAL_DECLINED;
  const stageIdx = declined
    ? -1
    : STAGES.findIndex((s) => s.key === stage);

  return (
    <div className="flex flex-col gap-4">
      {/* Borrower-facing back-nav. Always-visible, never blocking — the
          status page is where a borrower lands after submitting, and
          they should always be able to start another application or
          jump back to the application form without browser back. */}
      <Link
        href="/apply"
        className="inline-flex w-fit items-center gap-1.5 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2.5 py-1.5 text-[12px] text-[var(--color-text-primary)] hover:bg-[var(--color-background-secondary)]"
      >
        <IconArrowLeft size={13} />
        Start a new application
      </Link>
      {!status && !statusQuery.error && (
        <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-6 text-center text-[12.5px] text-[var(--color-text-tertiary)]">
          Loading your application status…
        </div>
      )}
      {statusQuery.error && (
        <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-6 text-center text-[13px] text-[var(--color-text-danger)]">
          We couldn&apos;t find this application. Check the link and try again.
        </div>
      )}

      {status && (
        <>
          <header className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-4">
            <p className="text-[11px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
              Application
            </p>
            <p className="mt-1 text-[18px] font-medium tracking-tight">
              {status.reference}
            </p>
            <p className="mt-2 text-[12.5px] leading-relaxed text-[var(--color-text-secondary)]">
              {status.next_step}
            </p>
            <p className="mt-3 text-[11px] text-[var(--color-text-tertiary)]">
              Submitted{" "}
              {new Date(status.submitted_at).toLocaleString(undefined, {
                dateStyle: "medium",
                timeStyle: "short",
              })}
              {" · "}
              We&apos;ll keep this page updated as your application moves
              forward.
            </p>
          </header>

          {/* Stage tracker — same lifecycle the underwriter sees, but
              rendered as a horizontal progress trail with borrower-
              friendly copy. */}
          <section className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-4">
            <p className="mb-4 text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
              Where your application is
            </p>
            {declined ? (
              <div className="flex items-center gap-3 rounded-md bg-[var(--color-background-danger)] px-3 py-2">
                <IconX size={16} style={{ color: "var(--color-text-danger)" }} />
                <p className="text-[12.5px] text-[var(--color-text-danger)]">
                  Your application was declined. You should have received
                  an email explaining the reason.
                </p>
              </div>
            ) : (
              <ol className="flex flex-col gap-0">
                {STAGES.map((s, i) => {
                  const done = i < stageIdx;
                  const active = i === stageIdx;
                  return (
                    <li
                      key={s.key}
                      className="flex gap-3 text-[12.5px]"
                      style={{
                        color: done || active
                          ? "var(--color-text-primary)"
                          : "var(--color-text-tertiary)",
                      }}
                    >
                      <div className="flex flex-col items-center">
                        <span
                          className="relative inline-flex h-[22px] w-[22px] items-center justify-center rounded-full"
                          style={{
                            background: active
                              ? "var(--color-background-info)"
                              : done
                                ? "var(--color-background-success)"
                                : "var(--color-background-secondary)",
                            color: active
                              ? "var(--color-text-info)"
                              : done
                                ? "var(--color-brand)"
                                : "var(--color-text-tertiary)",
                          }}
                        >
                          {done ? (
                            <IconCheck size={12} />
                          ) : active ? (
                            <IconLoader2 size={11} className="animate-spin" />
                          ) : (
                            <s.Icon size={11} />
                          )}
                        </span>
                        {i < STAGES.length - 1 && (
                          <span
                            className="my-0.5 w-[1.5px] flex-1"
                            style={{
                              background: done
                                ? "var(--color-brand)"
                                : "var(--color-border-tertiary)",
                              opacity: done ? 0.4 : 1,
                              minHeight: "16px",
                            }}
                          />
                        )}
                      </div>
                      <div className="flex-1 pb-3">
                        <p
                          style={{
                            fontWeight: active ? 500 : 400,
                          }}
                        >
                          {s.label}
                        </p>
                      </div>
                    </li>
                  );
                })}
              </ol>
            )}
          </section>

          <DocsUploader
            uploading={upload.isPending}
            onFiles={(files) => files.forEach((f) => upload.mutate(f))}
            documents={status.documents}
          />
        </>
      )}
    </div>
  );
}

// ---- documents uploader ----------------------------------------------------

function DocsUploader({
  uploading,
  onFiles,
  documents,
}: {
  uploading: boolean;
  onFiles: (files: File[]) => void;
  documents: { filename: string; uploaded_at: string; size_bytes: number }[];
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragOver, setDragOver] = useState(false);

  return (
    <section className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-4">
      <p className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
        Documents
      </p>
      <p className="mt-1 mb-3 text-[12px] text-[var(--color-text-tertiary)]">
        Attach the loan packet, appraisal, rent roll, financials, or anything
        else relevant. PDFs are extracted automatically.
      </p>

      {documents.length > 0 && (
        <div className="mb-3 flex flex-col divide-y-[0.5px] divide-[var(--color-border-tertiary)]">
          {documents.map((d) => (
            <div
              key={d.filename + d.uploaded_at}
              className="flex items-center justify-between gap-3 py-2 text-[12.5px]"
            >
              <span className="flex items-center gap-2">
                <IconFileText
                  size={14}
                  style={{ color: "var(--color-text-secondary)" }}
                />
                <span className="font-medium">{d.filename}</span>
              </span>
              <span className="text-[11px] text-[var(--color-text-tertiary)]">
                {new Date(d.uploaded_at).toLocaleDateString()}
              </span>
            </div>
          ))}
        </div>
      )}

      <motion.div
        role="button"
        tabIndex={0}
        onClick={() => inputRef.current?.click()}
        onKeyDown={(e) => {
          if (e.key === "Enter" || e.key === " ") inputRef.current?.click();
        }}
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setDragOver(false);
          const files = Array.from(e.dataTransfer.files);
          if (files.length > 0) onFiles(files);
        }}
        whileTap={{ scale: 0.99 }}
        className="flex flex-col items-center gap-1.5 rounded-md border border-dashed py-5 text-center cursor-pointer transition-colors"
        style={{
          borderColor: dragOver
            ? "var(--color-brand)"
            : "var(--color-border-tertiary)",
          background: dragOver
            ? "var(--color-background-success)"
            : "var(--color-background-secondary)",
        }}
      >
        <span
          className="inline-flex h-7 w-7 items-center justify-center rounded-full"
          style={{
            background: "var(--color-background-primary)",
            color: dragOver
              ? "var(--color-brand)"
              : "var(--color-text-secondary)",
          }}
        >
          {uploading ? (
            <IconLoader2 size={14} className="animate-spin" />
          ) : (
            <IconCloudUpload size={14} />
          )}
        </span>
        <p className="text-[12px] text-[var(--color-text-primary)]">
          {uploading ? "Uploading…" : "Drop files here or click to upload"}
        </p>
        <p className="text-[10.5px] text-[var(--color-text-tertiary)]">
          PDF or plain text. Up to ~20MB each.
        </p>
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
          className="sr-only"
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            if (files.length > 0) onFiles(files);
            e.target.value = "";
          }}
        />
      </motion.div>
    </section>
  );
}

// Suppress an unused-import warning until we wire dashed-circle UI on
// pending stages explicitly.
void IconCircleDashed;
