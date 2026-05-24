"use client";

import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  IconCheck,
  IconChevronDown,
  IconLoader2,
  IconUserCircle,
  IconUserOff,
} from "@tabler/icons-react";
import { motion } from "motion/react";
import { toast } from "sonner";

import { api, type Loan, type StaffUser } from "@/lib/api";

/**
 * Owner reassignment control on the loan detail page.
 *
 * Why this exists (#107): the loan owner is the staff member
 * responsible for moving the deal — when responsibility shifts
 * (vacation, expertise mismatch, workout handoff) the UI needs a
 * way to record that without an admin opening psql. The audit
 * event the backend writes preserves the "who → who, why" record
 * the committee will eventually ask about.
 *
 * UX pattern: a clickable badge that opens a popover with the
 * staff list + a reason input. We require a non-empty reason
 * because the backend does — same discipline as stage transitions.
 *
 * Click-outside closes the popover; Esc closes; clicking the
 * already-current owner is a no-op (no reassignment to self).
 */
export function OwnerPicker({ loan }: { loan: Loan }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [pendingOwnerId, setPendingOwnerId] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement | null>(null);

  // Staff list — only fetched once the popover opens, so the
  // pipeline view doesn't pay for it on every loan card click.
  const staffQuery = useQuery<StaffUser[], Error>({
    queryKey: ["staff-users"],
    queryFn: () => api.listStaffUsers(),
    enabled: open,
    staleTime: 60_000, // staff list doesn't change minute-to-minute
  });

  const reassign = useMutation({
    mutationFn: ({
      ownerId,
      reasonText,
    }: {
      ownerId: string | null;
      reasonText: string;
    }) => api.setLoanOwner(loan.id, ownerId, reasonText),
    onSuccess: async (_data, vars) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["loan", loan.id] }),
        queryClient.invalidateQueries({
          queryKey: ["loan", loan.id, "audit"],
        }),
        // The pipeline list shows owner initials per row — refresh
        // it so the move shows up there immediately.
        queryClient.invalidateQueries({ queryKey: ["loans"] }),
      ]);
      const targetName =
        vars.ownerId === null
          ? "Unassigned"
          : staffQuery.data?.find((u) => u.id === vars.ownerId)?.name ??
            "the new owner";
      toast.success(
        vars.ownerId === null
          ? "Loan unassigned"
          : `Reassigned to ${targetName}`,
      );
      setOpen(false);
      setReason("");
      setPendingOwnerId(null);
    },
    onError: (e) => {
      const msg = e instanceof Error ? e.message : String(e);
      toast.error("Couldn't reassign owner", { description: msg });
    },
  });

  // Close on click-outside.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
        setPendingOwnerId(null);
        setReason("");
      }
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  // Close on Escape.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        setPendingOwnerId(null);
        setReason("");
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const currentOwnerId = loan.owner?.id ?? null;
  const label = loan.owner ? loan.owner.name : "Unassigned";
  const initials = loan.owner?.initials ?? "—";

  return (
    <div ref={ref} className="relative inline-flex items-center text-[11px]">
      <span className="mr-1.5 text-[var(--color-text-secondary)]">Owner:</span>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex items-center gap-1.5 rounded-full border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] py-0.5 pr-2 pl-1 font-medium text-[var(--color-text-primary)] hover:bg-[var(--color-background-secondary)]"
        title="Reassign owner"
      >
        <span
          className="inline-flex h-4 w-4 items-center justify-center rounded-full text-[9px] font-semibold"
          style={{
            background: loan.owner
              ? "var(--color-background-info)"
              : "var(--color-background-secondary)",
            color: loan.owner
              ? "var(--color-text-info)"
              : "var(--color-text-tertiary)",
          }}
        >
          {initials}
        </span>
        <span>{label}</span>
        <IconChevronDown
          size={10}
          className="text-[var(--color-text-tertiary)]"
        />
      </button>

      {open && (
        <motion.div
          initial={{ opacity: 0, y: -4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.12 }}
          className="absolute top-full left-0 z-20 mt-1 w-72 rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] p-2 shadow-lg"
        >
          {pendingOwnerId === null || pendingOwnerId === undefined ? (
            // Stage 1: pick someone (or Unassign).
            <Picker
              currentOwnerId={currentOwnerId}
              staff={staffQuery.data ?? []}
              loading={staffQuery.isLoading}
              error={staffQuery.error}
              onPick={(id) => setPendingOwnerId(id ?? "__none__")}
            />
          ) : (
            // Stage 2: confirm with reason. ``__none__`` is the
            // sentinel for "unassign" because we can't use null in
            // a string-keyed state slot.
            <ConfirmReason
              targetName={
                pendingOwnerId === "__none__"
                  ? "Unassigned"
                  : staffQuery.data?.find((u) => u.id === pendingOwnerId)
                      ?.name ?? "the new owner"
              }
              currentName={label}
              reason={reason}
              setReason={setReason}
              busy={reassign.isPending}
              onCancel={() => setPendingOwnerId(null)}
              onConfirm={() =>
                reassign.mutate({
                  ownerId: pendingOwnerId === "__none__" ? null : pendingOwnerId,
                  reasonText: reason.trim(),
                })
              }
            />
          )}
        </motion.div>
      )}
    </div>
  );
}

function Picker({
  currentOwnerId,
  staff,
  loading,
  error,
  onPick,
}: {
  currentOwnerId: string | null;
  staff: StaffUser[];
  loading: boolean;
  error: Error | null;
  onPick: (ownerId: string | null) => void;
}) {
  if (loading) {
    return (
      <div className="flex items-center gap-2 px-2 py-3 text-[12px] text-[var(--color-text-secondary)]">
        <IconLoader2 size={12} className="animate-spin" />
        Loading staff…
      </div>
    );
  }
  if (error) {
    return (
      <p className="px-2 py-3 text-[12px] text-[var(--color-text-danger)]">
        {error.message}
      </p>
    );
  }
  return (
    <div className="flex flex-col gap-0.5">
      <p className="px-2 pt-1 pb-1.5 text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-tertiary)]">
        Reassign to
      </p>
      {staff.map((u) => (
        <button
          type="button"
          key={u.id}
          onClick={() => u.id !== currentOwnerId && onPick(u.id)}
          disabled={u.id === currentOwnerId}
          className="flex items-center gap-2 rounded-md px-2 py-1.5 text-left text-[12.5px] hover:bg-[var(--color-background-secondary)] disabled:opacity-50"
        >
          <span
            className="inline-flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[9.5px] font-semibold"
            style={{
              background: "var(--color-background-info)",
              color: "var(--color-text-info)",
            }}
          >
            {u.initials}
          </span>
          <span className="flex-1 truncate">
            <span className="font-medium">{u.name}</span>{" "}
            <span className="text-[10.5px] text-[var(--color-text-tertiary)]">
              · {u.role}
            </span>
          </span>
          {u.id === currentOwnerId && (
            <IconCheck size={11} className="text-[var(--color-brand)]" />
          )}
        </button>
      ))}
      {currentOwnerId !== null && (
        <button
          type="button"
          onClick={() => onPick(null)}
          className="mt-1 flex items-center gap-2 rounded-md border-t border-[var(--color-border-tertiary)] px-2 pt-2 pb-1.5 text-left text-[12px] text-[var(--color-text-secondary)] hover:bg-[var(--color-background-secondary)]"
        >
          <IconUserOff size={11} />
          Unassign
        </button>
      )}
    </div>
  );
}

function ConfirmReason({
  targetName,
  currentName,
  reason,
  setReason,
  busy,
  onCancel,
  onConfirm,
}: {
  targetName: string;
  currentName: string;
  reason: string;
  setReason: (s: string) => void;
  busy: boolean;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  return (
    <div className="flex flex-col gap-2 p-1">
      <div className="flex items-start gap-2">
        <IconUserCircle
          size={14}
          className="mt-0.5 shrink-0 text-[var(--color-text-secondary)]"
        />
        <p className="text-[12px] leading-relaxed">
          Reassign from{" "}
          <span className="font-medium text-[var(--color-text-primary)]">
            {currentName}
          </span>{" "}
          to{" "}
          <span className="font-medium text-[var(--color-text-primary)]">
            {targetName}
          </span>
          ?
        </p>
      </div>
      <label className="flex flex-col gap-1">
        <span className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
          Reason
        </span>
        <textarea
          value={reason}
          onChange={(e) => setReason(e.target.value)}
          disabled={busy}
          rows={2}
          placeholder="On vacation, asset-class fit, etc."
          className="form-input"
          autoFocus
        />
      </label>
      <div className="mt-1 flex items-center justify-end gap-1.5">
        <button
          type="button"
          onClick={onCancel}
          disabled={busy}
          className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2 py-1 text-[11px] font-medium hover:bg-[var(--color-background-secondary)]"
        >
          Cancel
        </button>
        <button
          type="button"
          onClick={onConfirm}
          disabled={busy || reason.trim().length === 0}
          className="rounded-md bg-[var(--color-brand)] px-2 py-1 text-[11px] font-medium text-white disabled:opacity-45"
        >
          {busy ? "Saving…" : "Reassign"}
        </button>
      </div>
    </div>
  );
}
