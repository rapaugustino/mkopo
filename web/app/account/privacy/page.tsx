"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import {
  IconArrowLeft,
  IconDownload,
  IconShieldLock,
  IconTrash,
} from "@tabler/icons-react";
import { toast } from "sonner";

import { borrowerAuthApi, type ApiError } from "@/lib/borrowerApi";
import { useAuth } from "@/app/borrower/AuthProvider";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SecondaryButton } from "@/app/components/SecondaryButton";

/**
 * Borrower data & privacy controls.
 *
 * Two surfaces:
 *
 *  - **Export my data** — DSAR-style JSON dump of everything we hold.
 *    Triggers a download client-side; backend returns a JSON blob.
 *  - **Request erasure** — soft-delete the account + all associated
 *    loans, with the regulatory retention window enforced. User is
 *    signed out immediately after a successful erasure.
 *
 * No mid-action interrupt UX here (that lives in Phase 3); the
 * erasure flow uses a confirmation modal pattern with an explicit
 * "I understand" checkbox + reason text — the user really should
 * read what they're agreeing to.
 */
export default function PrivacyPage() {
  const router = useRouter();
  const auth = useAuth();

  useEffect(() => {
    if (auth.status === "anonymous") {
      router.replace(`/login?next=${encodeURIComponent("/account/privacy")}`);
    }
  }, [auth.status, router]);

  if (auth.status !== "authed") {
    return (
      <div className="py-12 text-center text-[12.5px] text-[var(--color-text-tertiary)]">
        Loading…
      </div>
    );
  }

  return (
    <div className="mx-auto flex max-w-2xl flex-col gap-5">
      <header className="flex items-center justify-between gap-3">
        <Link
          href="/account"
          className="inline-flex items-center gap-1.5 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2.5 py-1.5 text-[12px] text-[var(--color-text-primary)] hover:bg-[var(--color-background-secondary)]"
        >
          <IconArrowLeft size={13} />
          Back to account
        </Link>
        <div className="flex items-center gap-1.5 text-[12px] text-[var(--color-text-secondary)]">
          <IconShieldLock size={12} />
          Data &amp; privacy
        </div>
      </header>

      <DataExportCard />
      <ErasureCard />
    </div>
  );
}

// ---- Data export -----------------------------------------------------------

function DataExportCard() {
  const exportMut = useMutation({
    mutationFn: () => borrowerAuthApi.exportMyData(),
    onSuccess: (data) => {
      // Client-side blob download. Real-world this would be an
      // async email-link flow, but for the demo a synchronous JSON
      // dump is honest and inspectable.
      const blob = new Blob([JSON.stringify(data, null, 2)], {
        type: "application/json",
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `mkopo-data-export-${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      toast.success("Data export downloaded");
    },
    onError: (e) => {
      const err = e as unknown as ApiError;
      toast.error(err.message || "Export failed");
    },
  });

  return (
    <section className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-5 py-4">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <p className="text-[13.5px] font-medium">Export my data</p>
          <p className="mt-1 text-[12px] leading-relaxed text-[var(--color-text-secondary)]">
            Download a JSON copy of everything we hold about you — your
            account record, every application, the documents you uploaded
            (referenced by hash), and the full audit log. Useful for your
            own records or if you'd like to share with another lender.
          </p>
        </div>
        <PrimaryButton
          Icon={IconDownload}
          onClick={() => exportMut.mutate()}
          disabled={exportMut.isPending}
        >
          {exportMut.isPending ? "Preparing…" : "Download"}
        </PrimaryButton>
      </div>
    </section>
  );
}

// ---- Erasure ---------------------------------------------------------------

function ErasureCard() {
  const router = useRouter();
  const auth = useAuth();
  const [confirming, setConfirming] = useState(false);
  const [reason, setReason] = useState("");
  const [understood, setUnderstood] = useState(false);

  const erase = useMutation({
    mutationFn: () =>
      borrowerAuthApi.requestErasure({ reason, confirm: understood }),
    onSuccess: async (data) => {
      toast.success("Erasure requested", {
        description: data.message,
        duration: 10_000,
      });
      // Backend hasn't cleared the cookie itself; do it client-side
      // so the AuthProvider's next /me call sees anonymous and
      // bounces us to /login. Then route home.
      await auth.logout();
      router.push("/");
    },
    onError: (e) => {
      const err = e as unknown as ApiError;
      toast.error(err.message || "Erasure failed");
    },
  });

  if (!confirming) {
    return (
      <section className="rounded-lg border-[0.5px] border-[var(--color-text-danger)] bg-[var(--color-background-primary)] px-5 py-4">
        <div className="flex items-start justify-between gap-4">
          <div className="flex-1">
            <p className="text-[13.5px] font-medium text-[var(--color-text-danger)]">
              Request account erasure
            </p>
            <p className="mt-1 text-[12px] leading-relaxed text-[var(--color-text-secondary)]">
              We'll hide your account and any applications from our
              operational views immediately. We're required by lending
              regulations to keep the underlying records for{" "}
              <strong>25 months</strong> (Reg B/ECOA) on declined or
              withdrawn applications, and <strong>5 years</strong> (HMDA)
              on approved ones. After the retention window expires
              they're permanently deleted by an automated sweep.
            </p>
          </div>
          <SecondaryButton
            Icon={IconTrash}
            onClick={() => setConfirming(true)}
          >
            Start erasure
          </SecondaryButton>
        </div>
      </section>
    );
  }

  return (
    <section className="rounded-lg border-[0.5px] border-[var(--color-text-danger)] bg-[var(--color-background-danger)] px-5 py-4">
      <p className="text-[13.5px] font-medium text-[var(--color-text-danger)]">
        Confirm account erasure
      </p>
      <p className="mt-2 text-[12px] leading-relaxed text-[var(--color-text-danger)] opacity-90">
        This signs you out immediately. Your account and all your
        applications enter the regulatory retention period and become
        unrecoverable through any borrower-side action.
      </p>

      <label className="mt-4 flex flex-col gap-1">
        <span className="text-[10.5px] font-medium uppercase tracking-wider text-[var(--color-text-danger)]">
          Reason
        </span>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          disabled={erase.isPending}
          rows={2}
          placeholder="A short reason for our records (we read every one)."
          className="form-input"
        />
      </label>

      <label className="mt-3 flex items-start gap-2 text-[12px] text-[var(--color-text-danger)]">
        <input
          type="checkbox"
          checked={understood}
          onChange={(e) => setUnderstood(e.target.checked)}
          disabled={erase.isPending}
          className="mt-0.5"
        />
        <span>
          I understand the retention windows above and want my account
          and applications removed from operational views immediately.
        </span>
      </label>

      <div className="mt-4 flex items-center justify-end gap-2">
        <SecondaryButton
          onClick={() => {
            setConfirming(false);
            setUnderstood(false);
            setReason("");
          }}
          disabled={erase.isPending}
        >
          Cancel
        </SecondaryButton>
        <button
          type="button"
          onClick={() => erase.mutate()}
          disabled={
            erase.isPending || !understood || reason.trim().length === 0
          }
          className="inline-flex items-center gap-1.5 rounded-md px-3 py-1.5 text-[12px] font-medium disabled:opacity-45"
          style={{
            background: "var(--color-text-danger)",
            color: "white",
          }}
        >
          <IconTrash size={13} />
          {erase.isPending ? "Working…" : "Erase my account"}
        </button>
      </div>
    </section>
  );
}
