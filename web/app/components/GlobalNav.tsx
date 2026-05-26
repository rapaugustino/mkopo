"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  IconActivity,
  IconChartLine,
  IconLayoutGrid,
  IconListSearch,
  IconPencil,
  IconSettings,
  IconShieldHalf,
} from "@tabler/icons-react";
import { api, type ReviewTask } from "@/lib/api";

interface NavItem {
  href: string;
  label: string;
  Icon: React.ComponentType<{ size?: number }>;
  /** When true, treat the route as "active" for exact-match only. Otherwise startsWith. */
  exact?: boolean;
}

const NAV: NavItem[] = [
  { href: "/", label: "Pipeline", Icon: IconLayoutGrid, exact: true },
  { href: "/review-queue", label: "Review queue", Icon: IconListSearch },
  { href: "/eval", label: "Eval", Icon: IconChartLine },
  // Safety is the dedicated guardrails surface — input-side injection
  // detections + output-side constitutional judge rollups. The
  // observability page has a Safety tab too; this is the deep-dive.
  { href: "/safety", label: "Safety", Icon: IconShieldHalf },
  { href: "/observability", label: "Observability", Icon: IconActivity },
  // Prompts is the management surface for the system prompts every
  // agent uses. Staff-only by route gate (the underlying endpoint
  // requires CurrentUserDep).
  { href: "/prompts", label: "Prompts", Icon: IconPencil },
  // Institution settings — lender identity + signing authority +
  // credit reporting agency. Drives the "Real identifiers" block
  // every borrower-visible agent threads into its LLM prompt.
  { href: "/settings", label: "Settings", Icon: IconSettings },
];

/**
 * Global nav row inside the brand bar. Active link gets the brand-green
 * underline (matches the per-page tab nav styling for visual continuity).
 *
 * Review-queue link shows an open-count badge so users notice items
 * waiting without clicking through — the kind of small ambient signal
 * that makes the app feel alive.
 */
export function GlobalNav() {
  const pathname = usePathname();
  const reviewCountQuery = useQuery<ReviewTask[], Error>({
    queryKey: ["review-tasks", "open"],
    queryFn: () => api.listReviewTasks("open"),
    refetchInterval: 30_000,
    refetchOnWindowFocus: true,
  });
  const openCount = reviewCountQuery.data?.length ?? 0;

  return (
    <nav className="flex items-center gap-1" aria-label="Primary">
      {NAV.map((item) => {
        const active = item.exact
          ? pathname === item.href
          : pathname.startsWith(item.href);
        const Icon = item.Icon;
        return (
          <Link
            key={item.href}
            href={item.href}
            // ``hidden xs:inline``-style behaviour for the label: on
            // narrow screens we render the icon alone (with the
            // human-readable label exposed via ``title`` + ``aria-label``
            // for screen readers + tooltip). Five tappable icons fit
            // comfortably on an iPhone-SE width row; five labelled
            // items don't.
            title={item.label}
            aria-label={item.label}
            className={
              "relative flex items-center gap-1.5 rounded-md px-2 py-1 text-xs font-medium transition-colors sm:px-2.5 " +
              (active
                ? "text-[var(--color-text-primary)]"
                : "text-[var(--color-text-secondary)] hover:text-[var(--color-text-primary)]")
            }
          >
            <Icon size={14} />
            <span className="hidden sm:inline">{item.label}</span>
            {item.href === "/review-queue" && openCount > 0 && (
              <span
                className="inline-flex h-[16px] min-w-[16px] items-center justify-center rounded-full px-1 text-[10px] font-medium"
                style={{
                  background: "var(--color-background-warning)",
                  color: "var(--color-text-warning)",
                }}
              >
                {openCount}
              </span>
            )}
            {active && (
              <span
                className="absolute -bottom-[10px] left-0 right-0 h-[2px]"
                style={{ background: "var(--color-brand)" }}
              />
            )}
          </Link>
        );
      })}
    </nav>
  );
}
