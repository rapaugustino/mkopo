"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { IconPlus, IconX } from "@tabler/icons-react";
import { motion } from "motion/react";
import { toast } from "sonner";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SecondaryButton } from "@/app/components/SecondaryButton";

interface Props {
  open: boolean;
  onClose: () => void;
}

type LoanType = "bridge" | "permanent" | "construction" | "refinance";
type LoanClass = "business" | "personal";

interface FormState {
  loan_class: LoanClass;
  loan_type: LoanType;
  amount: string; // string in the form for the numeric input; parsed on submit
  borrower_name: string;
  borrower_email: string;
  guarantor_name: string;
  guarantor_email: string;
}

const EMPTY: FormState = {
  loan_class: "business",
  loan_type: "bridge",
  amount: "",
  borrower_name: "",
  borrower_email: "",
  guarantor_name: "",
  guarantor_email: "",
};

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

/**
 * New loan creation modal.
 *
 * Mirrors the minimum information the backend's ``LoanCreate`` schema
 * requires (loan_type, amount, borrower party, optional guarantor) so
 * the modal can submit directly to ``POST /loans`` without an
 * intermediate form-validation layer.
 *
 * The borrower email becomes the canonical address the intake agent
 * writes to — the modal flags this in the helper text so the user
 * doesn't fat-finger the address that downstream emails will land on.
 */
export function NewLoanModal({ open, onClose }: Props) {
  const queryClient = useQueryClient();
  const router = useRouter();
  const [form, setForm] = useState<FormState>(EMPTY);

  const create = useMutation({
    mutationFn: async (state: FormState) => {
      const body = {
        loan_type: state.loan_type,
        loan_class: state.loan_class,
        amount: Number(state.amount),
        borrower_email: state.borrower_email,
        parties: [
          {
            name: state.borrower_name,
            party_type: state.borrower_name.toLowerCase().includes("llc") ||
            state.borrower_name.toLowerCase().includes("inc") ||
            state.borrower_name.toLowerCase().includes("lp") ||
            state.borrower_name.toLowerCase().includes("corp")
              ? "entity"
              : "person",
            role: "borrower",
            email: state.borrower_email,
          },
          ...(state.guarantor_name
            ? [
                {
                  name: state.guarantor_name,
                  party_type: "person",
                  role: "guarantor",
                  email: state.guarantor_email || null,
                },
              ]
            : []),
        ],
      };
      const res = await fetch(`${API_URL}/api/v1/loans`, {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(body),
      });
      if (res.status === 401 && typeof window !== "undefined") {
        const next = encodeURIComponent(window.location.pathname);
        window.location.href = `/staff/login?next=${next}`;
        throw new Error("Not authenticated");
      }
      if (!res.ok) throw new Error(`Create ${res.status}: ${await res.text()}`);
      return (await res.json()) as { id: string; reference: string };
    },
    onSuccess: async (loan) => {
      await queryClient.invalidateQueries({ queryKey: ["loans"] });
      toast.success(`Loan ${loan.reference} created`, {
        description: "Opening case file…",
      });
      setForm(EMPTY);
      onClose();
      router.push(`/loans/${loan.id}`);
    },
    onError: (e) =>
      toast.error("Couldn't create loan", {
        description: e instanceof Error ? e.message : String(e),
      }),
  });

  if (!open) return null;

  const valid =
    Number(form.amount) > 0 &&
    form.borrower_name.trim().length > 1 &&
    /^[^@]+@[^@]+\.[^@]+$/.test(form.borrower_email);

  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.14 }}
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-labelledby="new-loan-title"
    >
      <motion.div
        initial={{ opacity: 0, y: 8, scale: 0.98 }}
        animate={{ opacity: 1, y: 0, scale: 1 }}
        exit={{ opacity: 0, y: 4, scale: 0.99 }}
        transition={{ duration: 0.18, ease: "easeOut" }}
        className="w-full max-w-lg overflow-hidden rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] shadow-xl"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="flex items-center justify-between border-b-[0.5px] border-[var(--color-border-tertiary)] px-5 py-4">
          <div>
            <p id="new-loan-title" className="text-[15px] font-medium tracking-tight">
              New loan
            </p>
            <p className="mt-0.5 text-[12px] text-[var(--color-text-secondary)]">
              The intake agent picks up from here — drop in docs, click Run intake,
              and the borrower email is drafted automatically.
            </p>
          </div>
          <button
            onClick={onClose}
            aria-label="Close"
            className="text-[var(--color-text-tertiary)] hover:text-[var(--color-text-secondary)]"
          >
            <IconX size={16} />
          </button>
        </header>

        <div className="grid grid-cols-2 gap-3 px-5 py-4">
          {/* Loan class — full-width, distinct from the rest of the
              form. Picking it first lets the borrower (or operator)
              orient before they hit the loan-type / amount fields,
              and the underlying intake required-fields list branches
              on this. */}
          <div className="col-span-2">
            <Field
              label="Loan class"
              hint="Commercial real estate vs. consumer / personal credit. Drives which fields underwriting needs."
            >
              <div className="grid grid-cols-2 gap-1.5">
                {(["business", "personal"] as const).map((klass) => {
                  const active = form.loan_class === klass;
                  return (
                    <button
                      key={klass}
                      type="button"
                      onClick={() => setForm({ ...form, loan_class: klass })}
                      className="rounded-md px-3 py-2 text-left"
                      style={{
                        background: active
                          ? "var(--color-background-success)"
                          : "var(--color-background-secondary)",
                        border: active
                          ? "1px solid var(--color-brand)"
                          : "0.5px solid var(--color-border-tertiary)",
                        color: active
                          ? "var(--color-brand)"
                          : "var(--color-text-primary)",
                      }}
                    >
                      <p className="text-[13px] font-medium">
                        {klass === "business" ? "Business" : "Personal"}
                      </p>
                      <p
                        className="text-[10.5px]"
                        style={{
                          color: active
                            ? "var(--color-brand)"
                            : "var(--color-text-tertiary)",
                        }}
                      >
                        {klass === "business"
                          ? "DSCR / LTV / debt yield · CRE asset"
                          : "DTI / FICO floor · individual borrower"}
                      </p>
                    </button>
                  );
                })}
              </div>
            </Field>
          </div>
          <Field label="Loan type">
            <select
              value={form.loan_type}
              onChange={(e) =>
                setForm({ ...form, loan_type: e.target.value as LoanType })
              }
              className="form-input"
            >
              <option value="bridge">Bridge</option>
              <option value="permanent">Permanent</option>
              <option value="construction">Construction</option>
              <option value="refinance">Refinance</option>
            </select>
          </Field>

          <Field label="Amount (USD)">
            <input
              type="number"
              min={1}
              step={1000}
              value={form.amount}
              onChange={(e) => setForm({ ...form, amount: e.target.value })}
              placeholder="2400000"
              className="form-input"
            />
          </Field>

          <Field label="Borrower entity" hint="LLC / individual name">
            <input
              type="text"
              value={form.borrower_name}
              onChange={(e) =>
                setForm({ ...form, borrower_name: e.target.value })
              }
              placeholder="e.g. Atlas Holdings LLC"
              className="form-input"
            />
          </Field>

          <Field
            label="Borrower email"
            hint="Where the intake agent sends the doc request"
          >
            <input
              type="email"
              value={form.borrower_email}
              onChange={(e) =>
                setForm({ ...form, borrower_email: e.target.value })
              }
              placeholder="contact@atlasholdings.example"
              className="form-input"
            />
          </Field>

          <div className="col-span-2 mt-1">
            <p className="mb-1.5 text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
              Optional guarantor
            </p>
            <div className="grid grid-cols-2 gap-3">
              <input
                type="text"
                value={form.guarantor_name}
                onChange={(e) =>
                  setForm({ ...form, guarantor_name: e.target.value })
                }
                placeholder="Guarantor name"
                className="form-input"
              />
              <input
                type="email"
                value={form.guarantor_email}
                onChange={(e) =>
                  setForm({ ...form, guarantor_email: e.target.value })
                }
                placeholder="Guarantor email (optional)"
                className="form-input"
              />
            </div>
          </div>
        </div>

        <footer className="flex items-center justify-end gap-2 border-t-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-secondary)] px-5 py-3">
          <SecondaryButton type="button" onClick={onClose} disabled={create.isPending}>
            Cancel
          </SecondaryButton>
          <PrimaryButton
            type="button"
            Icon={IconPlus}
            onClick={() => create.mutate(form)}
            disabled={!valid || create.isPending}
          >
            {create.isPending ? "Creating…" : "Create loan"}
          </PrimaryButton>
        </footer>
      </motion.div>
    </motion.div>
  );
}

function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[10px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
        {label}
      </span>
      {children}
      {hint && (
        <span className="text-[10px] text-[var(--color-text-tertiary)]">{hint}</span>
      )}
    </label>
  );
}
