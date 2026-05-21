"use client";

import { use, useMemo } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import {
  IconAlertTriangle,
  IconUser,
  IconUsers,
} from "@tabler/icons-react";
import { api, type LoanRef, type PartyProfile, type RelatedParty } from "@/lib/api";
import {
  humanizeLoanType,
  humanizePartyType,
  humanizeRisk,
  humanizeRole,
  humanizeStage,
} from "@/lib/humanize";
import { BrandHeader } from "@/app/components/BrandHeader";
import { Pill } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";
import { StatTile } from "@/app/components/StatTile";

interface PageProps {
  params: Promise<{ id: string }>;
}

function initials(name: string): string {
  const parts = name.split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0][0]!.toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]!).toUpperCase();
}

function formatMoney(s: string | number): string {
  const n = typeof s === "string" ? Number(s) : s;
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(0)}K`;
  return `$${n.toFixed(0)}`;
}

/**
 * Three-column SVG graph: party → loans → property labels.
 *
 * Coordinates are computed from the loan count rather than hard-coded.
 * For more than ~6 loans we'd want pagination, but for the portfolio
 * scope this fits comfortably.
 */
function ConcentrationGraph({
  profile,
  loans,
}: {
  profile: PartyProfile;
  loans: LoanRef[];
}) {
  const ROW_H = 60;
  const PADDING_Y = 30;
  const W = 640;
  const H = Math.max(280, PADDING_Y * 2 + ROW_H * Math.max(1, loans.length) - 20);

  // Vertical centring of each loan row
  const rowY = (i: number) =>
    PADDING_Y + (i + 0.5) * ((H - PADDING_Y * 2) / loans.length);

  const partyX = 100;
  const loanX = 275;
  const propX = 495;
  const loanW = 90;
  const propW = 110;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      xmlns="http://www.w3.org/2000/svg"
      style={{ width: "100%", height: "auto", display: "block" }}
      aria-label="Concentration graph: party connected to loans and properties"
    >
      {/* Lines: party → loan and loan → property */}
      {loans.map((_, i) => {
        const y = rowY(i);
        return (
          <g key={`lines-${i}`}>
            <line
              x1={partyX + 38}
              y1={H / 2}
              x2={loanX}
              y2={y}
              stroke="#888780"
              strokeWidth={1}
            />
            <line
              x1={loanX + loanW}
              y1={y}
              x2={propX}
              y2={y}
              stroke="#888780"
              strokeWidth={1}
            />
          </g>
        );
      })}

      {/* Party node (circle) — centred vertically */}
      <g>
        <circle
          cx={partyX}
          cy={H / 2}
          r={38}
          fill="var(--color-background-warning)"
          stroke="var(--color-text-warning)"
          strokeWidth={1.5}
        />
        <text
          x={partyX}
          y={H / 2 - 4}
          textAnchor="middle"
          fontSize={13}
          fontWeight={500}
          fill="var(--color-text-primary)"
        >
          {initials(profile.name)}
        </text>
        <text
          x={partyX}
          y={H / 2 + 12}
          textAnchor="middle"
          fontSize={10}
          fill="var(--color-text-secondary)"
        >
          {humanizeRole(profile.role)}
        </text>
      </g>

      {/* Loan nodes */}
      {loans.map((loan, i) => {
        const y = rowY(i);
        const fill =
          loan.risk_band === "high"
            ? "var(--color-background-danger)"
            : loan.risk_band === "med"
              ? "var(--color-background-warning)"
              : "var(--color-background-info)";
        const stroke =
          loan.risk_band === "high"
            ? "var(--color-text-danger)"
            : loan.risk_band === "med"
              ? "var(--color-text-warning)"
              : "var(--color-text-info)";
        return (
          <g key={loan.id}>
            <a href={`/loans/${loan.id}`}>
              <rect
                x={loanX}
                y={y - 20}
                width={loanW}
                height={40}
                rx={6}
                fill={fill}
                stroke={stroke}
                strokeWidth={loan.risk_band ? 1.5 : 1}
              />
              <text
                x={loanX + loanW / 2}
                y={y - 2}
                textAnchor="middle"
                fontSize={11}
                fontWeight={500}
                fill="var(--color-text-primary)"
              >
                {loan.reference}
              </text>
              <text
                x={loanX + loanW / 2}
                y={y + 12}
                textAnchor="middle"
                fontSize={10}
                fill="var(--color-text-secondary)"
              >
                {formatMoney(loan.amount)} · {humanizeStage(loan.stage)}
              </text>
            </a>
          </g>
        );
      })}

      {/* Property labels (right column) — placeholder text since we don't
          surface property-type/city in the LoanRef payload yet. The mockup
          shows "14-unit MF · Tacoma, WA"; until we wire that, show loan_type. */}
      {loans.map((loan, i) => {
        const y = rowY(i);
        return (
          <g key={`prop-${loan.id}`}>
            <rect
              x={propX}
              y={y - 20}
              width={propW}
              height={40}
              rx={6}
              fill="var(--color-background-secondary)"
              stroke="var(--color-border-tertiary)"
              strokeWidth={0.5}
            />
            <text
              x={propX + propW / 2}
              y={y - 2}
              textAnchor="middle"
              fontSize={11}
              fontWeight={500}
              fill="var(--color-text-primary)"
            >
              {humanizeLoanType(loan.loan_type)}
            </text>
            <text
              x={propX + propW / 2}
              y={y + 12}
              textAnchor="middle"
              fontSize={10}
              fill="var(--color-text-secondary)"
            >
              risk: {humanizeRisk(loan.risk_band)}
            </text>
          </g>
        );
      })}
    </svg>
  );
}

function RelatedPartyRow({ p }: { p: RelatedParty }) {
  return (
    <Link
      href={`/parties/${p.party_id}`}
      className="flex items-center justify-between rounded-md bg-[var(--color-background-secondary)] px-3 py-2 text-[12px] hover:bg-[var(--color-background-primary)]"
    >
      <div className="flex items-center gap-3">
        <span
          className="inline-block h-1.5 w-1.5 rounded-full"
          style={{ background: "var(--color-text-info)" }}
        />
        <span className="font-medium">{p.name}</span>
        <span className="text-[var(--color-text-secondary)]">
          {humanizeRole(p.role)} · {p.shared_loan_count} loan
          {p.shared_loan_count === 1 ? "" : "s"}
        </span>
      </div>
      <span className="text-[var(--color-text-secondary)]">
        {formatMoney(p.shared_exposure)} shared
      </span>
    </Link>
  );
}

export default function PartyInspectorPage({ params }: PageProps) {
  const { id } = use(params);

  const profileQuery = useQuery<PartyProfile, Error>({
    queryKey: ["party-profile", id],
    queryFn: () => api.getPartyProfile(id),
  });

  const profile = profileQuery.data;
  const exposureUtilisation = useMemo(() => {
    if (!profile) return 0;
    const exposure = Number(profile.active_exposure);
    const limit = Number(profile.policy_limit);
    return limit > 0 ? exposure / limit : 0;
  }, [profile]);

  const flagHighConcentration = exposureUtilisation >= 0.8;

  if (profileQuery.isPending) {
    return <p className="text-sm text-[var(--color-text-secondary)]">Loading party profile…</p>;
  }
  if (profileQuery.error || !profile) {
    return (
      <p className="text-sm text-[var(--color-text-danger)]">
        {profileQuery.error?.message ?? "Party not found"}
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <BrandHeader
        leading={
          <div
            className="flex h-[38px] w-[38px] shrink-0 items-center justify-center rounded-full text-[13px] font-medium"
            style={{
              background: "var(--color-background-warning)",
              color: "var(--color-text-warning)",
            }}
          >
            {initials(profile.name)}
          </div>
        }
        title={profile.name}
        sub={`${humanizeRole(profile.role)} · ${humanizePartyType(profile.party_type)}${profile.email ? ` · ${profile.email}` : ""}`}
        badge={
          flagHighConcentration ? (
            <Pill variant="danger" leading={<IconAlertTriangle size={11} />}>
              High concentration
            </Pill>
          ) : undefined
        }
      />

      <div className="grid grid-cols-4 gap-2">
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          <StatTile
            label="Active exposure"
            value={formatMoney(profile.active_exposure)}
            trend={
              exposureUtilisation > 0
                ? `${Math.round(exposureUtilisation * 100)}% of policy limit`
                : undefined
            }
            trendColor={
              flagHighConcentration ? "var(--color-text-danger)" : undefined
            }
          />
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          <StatTile label="Active loans" value={profile.active_loans.length} />
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          <StatTile
            label="Delinquencies"
            value={profile.delinquencies}
            trend="no payment data"
            Icon={IconUser}
          />
        </div>
        <div className="rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]">
          <StatTile
            label="Policy limit"
            value={formatMoney(profile.policy_limit)}
          />
        </div>
      </div>

      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] p-4">
        {profile.active_loans.length === 0 ? (
          <p className="px-2 py-6 text-center text-sm text-[var(--color-text-secondary)]">
            No active loans for this party yet. Loans count as active once
            they transition past intake (underwriting / decision / closing /
            servicing / approved).
          </p>
        ) : (
          <ConcentrationGraph profile={profile} loans={profile.active_loans} />
        )}
      </div>

      <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
        <SectionLabel Icon={IconUsers}>
          Related parties across these loans
        </SectionLabel>
        {profile.related_parties.length === 0 ? (
          <p className="text-xs text-[var(--color-text-tertiary)]">
            No co-guarantors / co-borrowers on this party&apos;s active loans.
          </p>
        ) : (
          <div className="flex flex-col gap-1">
            {profile.related_parties.map((p) => (
              <RelatedPartyRow key={p.party_id} p={p} />
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
