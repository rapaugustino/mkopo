"use client";

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  IconBuildingBank,
  IconCheck,
  IconFileText,
  IconSparkles,
  IconUserCheck,
} from "@tabler/icons-react";
import { toast } from "sonner";

import { BrandHeader } from "@/app/components/BrandHeader";
import { EmptyState } from "@/app/components/EmptyState";
import { PrimaryButton } from "@/app/components/PrimaryButton";
import { SectionLabel } from "@/app/components/SectionLabel";
import { Skeleton } from "@/app/components/Skeleton";
import {
  api,
  type InstitutionSettings,
  type InstitutionSettingsPatch,
} from "@/lib/api";

/**
 * Staff settings page — lender identity + authorized officer +
 * credit reporting agency triple.
 *
 * The values here feed every agent that drafts a borrower-visible
 * artifact. The intake email uses lender name + sign-off; the
 * adverse-action letter uses every field; the term sheet picks up
 * lender name + address. Without them the LLM falls back to
 * "[LENDER NAME]" style placeholders.
 *
 * Single-form page with three sections (lender identity, signing
 * authority, credit reporting agency). The form starts as a
 * controlled local mirror of the server snapshot; Save patches the
 * server and refreshes the cache so dependent surfaces see new
 * values immediately. Empty inputs map to ``null`` (clearing a
 * field) — same trim-then-null logic the backend applies.
 */
export default function SettingsPage() {
  const qc = useQueryClient();
  const { data, isPending, error } = useQuery<InstitutionSettings, Error>({
    queryKey: ["institution-settings"],
    queryFn: () => api.getInstitutionSettings(),
  });

  // Local form state mirrors the server snapshot. We initialise once
  // from ``data`` and then track edits — saving sends the diff +
  // refreshes the cache. ``useEffect`` re-syncs the form when the
  // server cache changes from outside (e.g. another tab edited).
  const [form, setForm] = useState<InstitutionSettingsPatch>({});
  useEffect(() => {
    if (!data) return;
    setForm({
      lender_name: data.lender_name ?? "",
      lender_address: data.lender_address ?? "",
      lender_phone: data.lender_phone ?? "",
      lender_email: data.lender_email ?? "",
      authorized_officer_name: data.authorized_officer_name ?? "",
      authorized_officer_title: data.authorized_officer_title ?? "",
      credit_reporting_agency_name: data.credit_reporting_agency_name ?? "",
      credit_reporting_agency_address:
        data.credit_reporting_agency_address ?? "",
      credit_reporting_agency_phone: data.credit_reporting_agency_phone ?? "",
    });
  }, [data]);

  const save = useMutation({
    mutationFn: (body: InstitutionSettingsPatch) =>
      api.updateInstitutionSettings(body),
    onSuccess: (next) => {
      qc.setQueryData(["institution-settings"], next);
      toast.success("Institution settings saved", {
        description: next.configured
          ? "Agents will use these values in every borrower-visible letter."
          : "Add a lender name to start unlocking the agent placeholders.",
      });
    },
    onError: (err) => {
      toast.error("Couldn't save settings", { description: String(err) });
    },
  });

  if (isPending) return <SettingsSkeleton />;
  if (error) {
    return (
      <p className="text-sm text-[var(--color-text-danger)]">
        Error: {error.message}
      </p>
    );
  }
  if (!data) return null;

  const onChange =
    (k: keyof InstitutionSettingsPatch) =>
    (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
      setForm((f) => ({ ...f, [k]: e.target.value }));

  return (
    <div className="flex flex-col gap-4">
      <BrandHeader
        title="Settings"
        sub="Identity + signing authority + ECOA disclosures. Used by every agent that drafts a borrower-visible letter."
      />

      {!data.configured && (
        <EmptyState
          variant="spark"
          title="Lender contact info isn't set yet"
          description={
            <>
              Without these values the AI fills in <code>[LENDER NAME]</code>{" "}
              and similar placeholders. Add at least the lender name + address
              + signing officer so adverse-action letters and decision packets
              come out finished.
            </>
          }
        />
      )}

      <form
        className="flex flex-col gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate(form);
        }}
      >
        <Card title="Lender identity" Icon={IconBuildingBank}>
          <Field
            label="Lender name"
            placeholder="Mkopo Lending, LLC"
            value={form.lender_name ?? ""}
            onChange={onChange("lender_name")}
          />
          <Field
            label="Address"
            placeholder="500 Brannan Street, Suite 600 / San Francisco, CA 94107"
            value={form.lender_address ?? ""}
            onChange={onChange("lender_address")}
            multiline
          />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field
              label="Phone"
              placeholder="(415) 555-0188"
              value={form.lender_phone ?? ""}
              onChange={onChange("lender_phone")}
            />
            <Field
              label="Email"
              placeholder="loans@mkopo.example"
              type="email"
              value={form.lender_email ?? ""}
              onChange={onChange("lender_email")}
            />
          </div>
        </Card>

        <Card title="Signing authority" Icon={IconUserCheck}>
          <p className="mb-3 text-[11.5px] leading-relaxed text-[var(--color-text-tertiary)]">
            Name + title of the credit authority who signs adverse-action
            letters and decision packets. In a small lender this is one person;
            in a larger shop it&apos;s a designated committee chair.
          </p>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field
              label="Authorized officer name"
              placeholder="Jordan Davis"
              value={form.authorized_officer_name ?? ""}
              onChange={onChange("authorized_officer_name")}
            />
            <Field
              label="Title"
              placeholder="Chief Credit Officer"
              value={form.authorized_officer_title ?? ""}
              onChange={onChange("authorized_officer_title")}
            />
          </div>
        </Card>

        <Card title="Credit reporting agency" Icon={IconFileText}>
          <p className="mb-3 text-[11.5px] leading-relaxed text-[var(--color-text-tertiary)]">
            ECOA Reg B § 1002.9(b)(2) requires adverse-action letters that
            relied on a consumer report to disclose the agency&apos;s name,
            address, and a toll-free phone number. Leave blank if no report was
            consulted — the letter will omit the entire credit-reporting
            paragraph rather than emit a placeholder.
          </p>
          <Field
            label="Agency name"
            placeholder="Experian Information Solutions, Inc."
            value={form.credit_reporting_agency_name ?? ""}
            onChange={onChange("credit_reporting_agency_name")}
          />
          <Field
            label="Agency address"
            placeholder="P.O. Box 2002, Allen, TX 75013"
            value={form.credit_reporting_agency_address ?? ""}
            onChange={onChange("credit_reporting_agency_address")}
            multiline
          />
          <Field
            label="Agency phone"
            placeholder="(888) 397-3742"
            value={form.credit_reporting_agency_phone ?? ""}
            onChange={onChange("credit_reporting_agency_phone")}
          />
        </Card>

        <div className="flex items-center justify-end gap-2">
          {save.isSuccess && (
            <span className="flex items-center gap-1 text-[12px] text-[var(--color-text-success)]">
              <IconCheck size={12} />
              Saved
            </span>
          )}
          <PrimaryButton
            Icon={IconSparkles}
            disabled={save.isPending}
            type="submit"
          >
            {save.isPending ? "Saving…" : "Save settings"}
          </PrimaryButton>
        </div>
      </form>
    </div>
  );
}

function Card({
  title,
  Icon,
  children,
}: {
  title: string;
  Icon: React.ComponentType<{ size?: number }>;
  children: React.ReactNode;
}) {
  return (
    <section className="flex flex-col gap-3 rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <SectionLabel Icon={Icon}>{title}</SectionLabel>
      <div className="flex flex-col gap-3">{children}</div>
    </section>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
  type = "text",
  multiline = false,
}: {
  label: string;
  value: string;
  onChange: (
    e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>,
  ) => void;
  placeholder?: string;
  type?: string;
  multiline?: boolean;
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-[11px] font-medium uppercase tracking-wider text-[var(--color-text-secondary)]">
        {label}
      </span>
      {multiline ? (
        <textarea
          className="form-input min-h-[68px]"
          value={value}
          onChange={onChange}
          placeholder={placeholder}
          rows={2}
        />
      ) : (
        <input
          className="form-input"
          type={type}
          value={value}
          onChange={onChange}
          placeholder={placeholder}
        />
      )}
    </label>
  );
}

function SettingsSkeleton() {
  return (
    <div className="flex flex-col gap-4">
      <Skeleton width="w-full" height="h-12" />
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="flex flex-col gap-3 rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3"
        >
          <Skeleton width="w-32" height="h-3" />
          <Skeleton width="w-full" height="h-9" />
          <Skeleton width="w-full" height="h-9" />
        </div>
      ))}
    </div>
  );
}
