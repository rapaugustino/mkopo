"use client";

import { use, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
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
  IconLogout,
  IconMicroscope,
  IconShieldCheck,
  IconX,
} from "@tabler/icons-react";
import { motion } from "motion/react";
import { toast } from "sonner";

import { useAuth } from "@/app/borrower/AuthProvider";
import { borrowerAuthApi } from "@/lib/borrowerApi";
import { BorrowerChat } from "./BorrowerChat";

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
  const router = useRouter();
  const auth = useAuth();

  // Auth gate. Anonymous → bounce to /login with this URL as next.
  // We do it in an effect (not a top-level redirect) so the
  // ``status === "loading"`` first render shows the loading state
  // rather than the login redirect flicker.
  useEffect(() => {
    if (auth.status === "anonymous") {
      const next = `/apply/${id}`;
      router.replace(`/login?next=${encodeURIComponent(next)}`);
    }
  }, [auth.status, id, router]);

  const statusQuery = useQuery<BorrowerStatus, Error>({
    queryKey: ["borrower-status", id],
    // Only fire after auth resolves to "authed" — fetching while
    // anonymous would 401 in a confusing loop while the redirect
    // is queued.
    enabled: auth.status === "authed",
    queryFn: async () => {
      const res = await fetch(
        `${API_URL}/api/v1/borrower-portal/loans/${id}/status`,
        { credentials: "include" },
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
        { method: "POST", body: form, credentials: "include" },
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
      {/* Borrower-facing header row: back-nav on the left, user chip
          + logout on the right. Always visible — the borrower
          should always see who they're signed in as and have one
          click to sign out. */}
      <div className="flex items-center justify-between gap-3">
        <Link
          href="/apply"
          className="inline-flex w-fit items-center gap-1.5 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2.5 py-1.5 text-[12px] text-[var(--color-text-primary)] hover:bg-[var(--color-background-secondary)]"
        >
          <IconArrowLeft size={13} />
          Start a new application
        </Link>
        {auth.user && (
          <div className="flex items-center gap-2 text-[12px] text-[var(--color-text-secondary)]">
            <span className="hidden sm:inline">
              Signed in as <strong>{auth.user.email}</strong>
            </span>
            <button
              type="button"
              onClick={() => {
                void auth.logout().then(() => router.push("/"));
              }}
              className="inline-flex items-center gap-1 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2 py-1 text-[11.5px] hover:bg-[var(--color-background-secondary)]"
            >
              <IconLogout size={11} />
              Sign out
            </button>
          </div>
        )}
      </div>
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

          {/* The agent surface. Always visible — even on terminal
              stages the borrower might want to ask "why was I
              declined?" or request a data export. The agent's read
              tools work in any stage; write tools refuse cleanly
              on closed loans. */}
          <BorrowerChat loanId={id} />

          {/* Form-based self-service for borrowers who prefer
              clicking to typing. Hidden on terminal stages where
              borrower mutations no longer make sense. */}
          {!IMMUTABLE_STAGES.has(stage) && (
            <BorrowerActions loanId={id} stage={stage} />
          )}
        </>
      )}
    </div>
  );
}

const IMMUTABLE_STAGES = new Set([
  "closing",
  "servicing",
  "declined",
  "withdrawn",
]);

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

// ----- borrower self-service for this loan ------------------------------

/**
 * Two-card action panel: edit underwriting-feeding fields, and
 * withdraw the application.
 *
 * Rendered only on non-terminal stages (see ``IMMUTABLE_STAGES``
 * above). Field edits post-decision drift the materials hash and
 * force re-underwriting; the loan status page surfaces that via
 * the existing MaterialsDriftBanner machinery on the staff side.
 * Withdrawal is terminal — confirmation modal + reason required.
 */
function BorrowerActions({
  loanId,
  stage,
}: {
  loanId: string;
  stage: string;
}) {
  return (
    <section className="flex flex-col gap-3">
      <EditFieldsCard loanId={loanId} />
      <WithdrawCard loanId={loanId} stage={stage} />
    </section>
  );
}

function EditFieldsCard({ loanId }: { loanId: string }) {
  const queryClient = useQueryClient();
  const [editing, setEditing] = useState(false);
  // Fields the borrower can self-edit. Server-side whitelist is the
  // source of truth; this client form just covers the personal-loan
  // case (most likely to need a quick correction).
  const [annualIncome, setAnnualIncome] = useState("");
  const [monthlyDebt, setMonthlyDebt] = useState("");
  const [employer, setEmployer] = useState("");
  const [creditScore, setCreditScore] = useState("");

  const save = useMutation({
    mutationFn: () =>
      borrowerAuthApi.updateLoanFields(loanId, {
        annual_income: annualIncome ? Number(annualIncome) : undefined,
        monthly_debt_payments: monthlyDebt ? Number(monthlyDebt) : undefined,
        employer: employer || undefined,
        credit_score: creditScore ? Number(creditScore) : undefined,
      }),
    onSuccess: async (res) => {
      if ((res.changed?.length ?? 0) === 0) {
        toast.message("No changes to save");
      } else {
        toast.success("Saved", {
          description: `Updated ${res.changed.length} field${res.changed.length === 1 ? "" : "s"}.`,
        });
      }
      await queryClient.invalidateQueries({
        queryKey: ["borrower-status", loanId],
      });
      setEditing(false);
      setAnnualIncome("");
      setMonthlyDebt("");
      setEmployer("");
      setCreditScore("");
    },
    onError: (e) => {
      const err = e as unknown as { message?: string };
      toast.error(err.message || "Couldn't save");
    },
  });

  if (!editing) {
    return (
      <div className="flex items-start justify-between gap-3 rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-4">
        <div>
          <p className="text-[13px] font-medium">Need to correct something?</p>
          <p className="mt-1 text-[12px] leading-relaxed text-[var(--color-text-secondary)]">
            Update income, employer, monthly debts, or credit score on this
            application. If a change matters to the underwriting decision,
            we'll automatically re-run it before any further progress.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setEditing(true)}
          className="inline-flex items-center gap-1 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 py-1.5 text-[12px] font-medium hover:bg-[var(--color-background-secondary)]"
        >
          Edit
        </button>
      </div>
    );
  }

  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-text-info)] bg-[var(--color-background-primary)] px-5 py-4">
      <p className="text-[13px] font-medium">Update underwriting fields</p>
      <p className="mt-1 text-[11.5px] text-[var(--color-text-secondary)]">
        Leave a field blank to keep its current value. We&apos;ll only
        save what you actually change.
      </p>
      <div className="mt-3 grid grid-cols-2 gap-3">
        <Field label="Annual income (USD)">
          <input
            type="number"
            min={0}
            value={annualIncome}
            onChange={(e) => setAnnualIncome(e.target.value)}
            disabled={save.isPending}
            className="form-input"
          />
        </Field>
        <Field label="Monthly debt payments (USD)">
          <input
            type="number"
            min={0}
            value={monthlyDebt}
            onChange={(e) => setMonthlyDebt(e.target.value)}
            disabled={save.isPending}
            className="form-input"
          />
        </Field>
        <Field label="Employer">
          <input
            type="text"
            value={employer}
            onChange={(e) => setEmployer(e.target.value)}
            disabled={save.isPending}
            className="form-input"
          />
        </Field>
        <Field label="Credit score (FICO 300–850)">
          <input
            type="number"
            min={300}
            max={850}
            value={creditScore}
            onChange={(e) => setCreditScore(e.target.value)}
            disabled={save.isPending}
            className="form-input"
          />
        </Field>
      </div>
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={() => {
            setEditing(false);
            setAnnualIncome("");
            setMonthlyDebt("");
            setEmployer("");
            setCreditScore("");
          }}
          disabled={save.isPending}
          className="inline-flex items-center gap-1 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] px-3 py-1.5 text-[12px] font-medium hover:bg-[var(--color-background-secondary)]"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={() => save.mutate()}
          disabled={save.isPending}
          className="inline-flex items-center gap-1 rounded-md px-3 py-1.5 text-[12px] font-medium disabled:opacity-45"
          style={{
            background: "var(--color-brand)",
            color: "var(--color-brand-light)",
          }}
        >
          {save.isPending ? "Saving…" : "Save changes"}
        </button>
      </div>
    </div>
  );
}

function WithdrawCard({ loanId, stage }: { loanId: string; stage: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");

  const withdraw = useMutation({
    mutationFn: () => borrowerAuthApi.withdrawLoan(loanId, reason),
    onSuccess: async () => {
      toast.success("Application withdrawn", {
        description:
          "Your application is closed. We'll be here if you want to apply again later.",
      });
      await queryClient.invalidateQueries({
        queryKey: ["borrower-status", loanId],
      });
      router.push("/account");
    },
    onError: (e) => {
      const err = e as unknown as { message?: string };
      toast.error(err.message || "Couldn't withdraw");
    },
  });

  void stage;
  if (!confirming) {
    return (
      <div className="flex items-start justify-between gap-3 rounded-lg border-[0.5px] border-[var(--color-text-danger)] bg-[var(--color-background-primary)] px-5 py-4">
        <div>
          <p className="text-[13px] font-medium text-[var(--color-text-danger)]">
            Withdraw this application
          </p>
          <p className="mt-1 text-[12px] leading-relaxed text-[var(--color-text-secondary)]">
            Cancels your application. This is final — you'd need to
            start a new application to come back. Your data still ages
            out per our retention policy.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setConfirming(true)}
          className="inline-flex items-center gap-1 rounded-md border-[0.5px] border-[var(--color-text-danger)] bg-[var(--color-background-primary)] px-3 py-1.5 text-[12px] font-medium text-[var(--color-text-danger)] hover:bg-[var(--color-background-danger)]"
        >
          Withdraw…
        </button>
      </div>
    );
  }

  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-text-danger)] bg-[var(--color-background-danger)] px-5 py-4">
      <p className="text-[13px] font-medium text-[var(--color-text-danger)]">
        Confirm withdrawal
      </p>
      <p className="mt-1 text-[12px] leading-relaxed text-[var(--color-text-danger)] opacity-90">
        Tell us briefly why — we read every reason and it helps us
        improve. We're not going to argue with your decision.
      </p>
      <label className="mt-3 flex flex-col gap-1">
        <span className="text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-danger)]">
          Reason
        </span>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          disabled={withdraw.isPending}
          rows={2}
          placeholder="Found a better rate / no longer need the loan / etc."
          className="form-input"
        />
      </label>
      <div className="mt-3 flex justify-end gap-2">
        <button
          type="button"
          onClick={() => {
            setConfirming(false);
            setReason("");
          }}
          disabled={withdraw.isPending}
          className="inline-flex items-center gap-1 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 py-1.5 text-[12px] font-medium hover:bg-[var(--color-background-secondary)]"
        >
          Keep my application
        </button>
        <button
          type="button"
          onClick={() => withdraw.mutate()}
          disabled={withdraw.isPending || reason.trim().length === 0}
          className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium disabled:opacity-45"
          style={{
            background: "var(--color-text-danger)",
            color: "white",
          }}
        >
          {withdraw.isPending ? "Withdrawing…" : "Withdraw application"}
        </button>
      </div>
    </div>
  );
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
        {label}
      </span>
      {children}
    </label>
  );
}
