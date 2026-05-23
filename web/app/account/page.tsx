"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import {
  IconArrowRight,
  IconLogout,
  IconPencil,
  IconPlus,
  IconUser,
} from "@tabler/icons-react";
import { toast } from "sonner";

import {
  borrowerAuthApi,
  type ApiError,
  type MyLoanRow,
} from "@/lib/borrowerApi";
import { useAuth } from "@/app/borrower/AuthProvider";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SecondaryButton } from "@/app/components/SecondaryButton";
import { humanizeLoanType, humanizeStage } from "@/lib/humanize";

/**
 * Borrower dashboard — the landing surface after login.
 *
 * What's here:
 *   - List of the borrower's loans, each linking to its status page.
 *   - "Start a new application" CTA.
 *   - Inline "edit my name" form (the only mutation that lives here).
 *
 * What's NOT here, and intentionally so:
 *   - Withdraw / data-export / erasure controls. Those route through
 *     the Phase 3 agent chat so every destructive action carries the
 *     confirmation interrupt + tool-call audit pattern.
 *   - "Update my income / employer / credit score". Those go through
 *     the agent too because they invalidate the materials hash and
 *     should be done deliberately with a re-underwriting prompt.
 *
 * Auth gate: anonymous users get bounced to /login?next=/account.
 */
export default function AccountPage() {
  const router = useRouter();
  const auth = useAuth();

  useEffect(() => {
    if (auth.status === "anonymous") {
      router.replace(`/login?next=${encodeURIComponent("/account")}`);
    }
  }, [auth.status, router]);

  const loansQuery = useQuery<MyLoanRow[], ApiError>({
    queryKey: ["my-loans"],
    queryFn: () => borrowerAuthApi.myLoans(),
    enabled: auth.status === "authed",
    // Polls so the stage chip updates as the loan moves through
    // intake → underwriting → decision without the user reloading.
    refetchInterval: 30_000,
  });

  if (auth.status !== "authed" || !auth.user) {
    // Loading + anonymous both get a calm placeholder rather than
    // a redirect flicker. The effect above handles the redirect.
    return (
      <div className="py-12 text-center text-[12.5px] text-[var(--color-text-tertiary)]">
        Loading…
      </div>
    );
  }

  const loans = loansQuery.data ?? [];

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-5">
      {/* Header with user identity + sign-out. */}
      <header className="flex items-start justify-between gap-3 rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-4">
        <div>
          <p className="text-[11px] uppercase tracking-wider text-[var(--color-text-tertiary)]">
            Account
          </p>
          <p className="font-editorial mt-1 text-[22px] tracking-tight">
            {auth.user.name || auth.user.email}
          </p>
          <p className="mt-0.5 text-[12px] text-[var(--color-text-secondary)]">
            {auth.user.email}
          </p>
        </div>
        <button
          type="button"
          onClick={() => {
            void auth.logout().then(() => router.push("/"));
          }}
          className="inline-flex items-center gap-1.5 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2.5 py-1.5 text-[11.5px] text-[var(--color-text-secondary)] hover:bg-[var(--color-background-secondary)]"
        >
          <IconLogout size={12} />
          Sign out
        </button>
      </header>

      {/* Loans list. The launchpad. */}
      <section className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-4">
        <div className="flex items-baseline justify-between gap-2">
          <p className="text-[13px] font-medium">Your applications</p>
          <Link
            href="/apply"
            className="inline-flex items-center gap-1 text-[12px] text-[var(--color-text-info)] hover:underline"
          >
            <IconPlus size={12} />
            Start a new application
          </Link>
        </div>

        {loansQuery.isPending && (
          <p className="mt-4 text-[12px] text-[var(--color-text-tertiary)]">
            Loading your applications…
          </p>
        )}

        {!loansQuery.isPending && loans.length === 0 && (
          <div className="mt-4 rounded-md bg-[var(--color-background-secondary)] px-4 py-6 text-center">
            <p className="text-[13px] text-[var(--color-text-primary)]">
              No applications yet.
            </p>
            <p className="mt-1 text-[12px] text-[var(--color-text-secondary)]">
              When you're ready, start one — we'll guide you through it.
            </p>
            <Link
              href="/apply"
              className="mt-3 inline-flex items-center gap-1.5 rounded-md bg-[var(--color-brand)] px-3 py-1.5 text-[12px] font-medium"
              style={{ color: "var(--color-brand-light)" }}
            >
              Apply now <IconArrowRight size={12} />
            </Link>
          </div>
        )}

        {loans.length > 0 && (
          <ul className="mt-3 flex flex-col gap-1.5">
            {loans.map((loan) => (
              <LoanRow key={loan.loan_id} loan={loan} />
            ))}
          </ul>
        )}
      </section>

      {/* Contact info — typo-class only. Anything that affects the
          decision lives in Phase 3 agent chat (when shipped) or
          the per-loan field-edit form on the status page. */}
      <ContactInfoCard />

      {/* Data & privacy footer — links to the dedicated page for
          the bigger compliance levers (export, erasure). */}
      <div className="flex items-center justify-between gap-3 rounded-md bg-[var(--color-background-secondary)] px-4 py-3 text-[12px]">
        <span className="text-[var(--color-text-secondary)]">
          Manage data export and account erasure.
        </span>
        <Link
          href="/account/privacy"
          className="font-medium text-[var(--color-text-info)] hover:underline"
        >
          Data &amp; privacy →
        </Link>
      </div>
    </div>
  );
}

function LoanRow({ loan }: { loan: MyLoanRow }) {
  return (
    <li>
      <Link
        href={`/apply/${loan.loan_id}`}
        className="flex items-center justify-between gap-3 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-3 py-2.5 text-[12.5px] hover:bg-[var(--color-background-secondary)]"
      >
        <div className="min-w-0 flex-1">
          <p className="truncate font-medium text-[var(--color-text-primary)]">
            {loan.reference}
          </p>
          <p className="mt-0.5 truncate text-[11.5px] text-[var(--color-text-secondary)]">
            ${Number(loan.amount).toLocaleString()} · {humanizeLoanType(loan.loan_type)}{" "}
            {loan.loan_class}
          </p>
          <p className="mt-1 line-clamp-1 text-[11px] text-[var(--color-text-tertiary)]">
            {loan.next_step}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span
            className="rounded px-2 py-0.5 text-[10.5px] font-medium uppercase tracking-wider"
            style={{
              background: STAGE_BG[loan.stage] ?? "var(--color-background-secondary)",
              color: STAGE_FG[loan.stage] ?? "var(--color-text-secondary)",
            }}
          >
            {humanizeStage(loan.stage)}
          </span>
          <IconArrowRight size={13} className="text-[var(--color-text-tertiary)]" />
        </div>
      </Link>
    </li>
  );
}

/** Inline name-edit. Keeps the dashboard self-contained — no
 *  separate "Edit profile" page for a single field. */
function ContactInfoCard() {
  const auth = useAuth();
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(auth.user?.name ?? "");

  // Re-sync the local field whenever the auth user updates (e.g.
  // after a successful save).
  useEffect(() => {
    setName(auth.user?.name ?? "");
  }, [auth.user]);

  const save = useMutation({
    mutationFn: () =>
      borrowerAuthApi.updateContact({ name: name.trim() || undefined }),
    onSuccess: (user) => {
      auth.setUser(user);
      setEditing(false);
      toast.success("Saved");
    },
    onError: (e) => {
      const err = e as unknown as ApiError;
      toast.error(err.message || "Couldn't save");
    },
  });

  if (!auth.user) return null;

  return (
    <section className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-4">
      <div className="flex items-baseline justify-between gap-2">
        <p className="text-[13px] font-medium">Contact information</p>
        {!editing && (
          <button
            type="button"
            onClick={() => setEditing(true)}
            className="inline-flex items-center gap-1 text-[12px] text-[var(--color-text-info)] hover:underline"
          >
            <IconPencil size={11} />
            Edit
          </button>
        )}
      </div>

      <div className="mt-3 grid grid-cols-2 gap-3">
        <Field label="Name" Icon={IconUser}>
          {editing ? (
            <input
              type="text"
              value={name}
              onChange={(e) => setName(e.target.value)}
              disabled={save.isPending}
              className="form-input"
            />
          ) : (
            <p className="text-[13px] text-[var(--color-text-primary)]">
              {auth.user.name || "—"}
            </p>
          )}
        </Field>
        <Field label="Email" Icon={IconUser}>
          {/* Email is fixed for now — changing it is a Phase 1c
              concern (email-verify magic-link flow). */}
          <p className="text-[13px] text-[var(--color-text-primary)]">
            {auth.user.email}
          </p>
        </Field>
      </div>

      {editing && (
        <div className="mt-3 flex justify-end gap-2">
          <SecondaryButton
            type="button"
            onClick={() => {
              setName(auth.user!.name);
              setEditing(false);
            }}
            disabled={save.isPending}
          >
            Cancel
          </SecondaryButton>
          <PrimaryButton
            type="button"
            onClick={() => save.mutate()}
            disabled={save.isPending || !name.trim()}
          >
            {save.isPending ? "Saving…" : "Save"}
          </PrimaryButton>
        </div>
      )}
    </section>
  );
}

function Field({
  label,
  Icon,
  children,
}: {
  label: string;
  Icon: React.ComponentType<{ size?: number; className?: string }>;
  children: React.ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1">
      <span className="inline-flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
        <Icon size={10} />
        {label}
      </span>
      {children}
    </div>
  );
}

// Stage pill palette. Mirrors the staff-side StagePill but with the
// borrower-friendly subset of stages.
const STAGE_BG: Record<string, string> = {
  intake: "var(--color-background-info)",
  underwriting: "var(--color-background-info)",
  decision: "var(--color-background-warning)",
  conditions: "var(--color-background-warning)",
  closing: "var(--color-background-success)",
  approved: "var(--color-background-success)",
  servicing: "var(--color-background-success)",
  declined: "var(--color-background-danger)",
  withdrawn: "var(--color-background-secondary)",
};
const STAGE_FG: Record<string, string> = {
  intake: "var(--color-text-info)",
  underwriting: "var(--color-text-info)",
  decision: "var(--color-text-warning)",
  conditions: "var(--color-text-warning)",
  closing: "var(--color-text-success)",
  approved: "var(--color-text-success)",
  servicing: "var(--color-text-success)",
  declined: "var(--color-text-danger)",
  withdrawn: "var(--color-text-tertiary)",
};
