import type { ReactNode } from "react";

export type PillVariant =
  | "info"
  | "warn"
  | "danger"
  | "success"
  | "ai"
  | "neutral";

/**
 * Small chip primitive used across the UI for status badges, event metadata,
 * cited fields, principal-reasons, etc. Variants pair a background with a
 * matching text color so callers don't have to reason about color tokens.
 *
 * Why a primitive: we were inlining the same five style combinations in five
 * components — anywhere we want to tighten the visual language (border?
 * tracking? text weight?), one change here propagates.
 */
const STYLE: Record<PillVariant, { bg: string; fg: string }> = {
  info: {
    bg: "var(--color-background-info)",
    fg: "var(--color-text-info)",
  },
  warn: {
    bg: "var(--color-background-warning)",
    fg: "var(--color-text-warning)",
  },
  danger: {
    bg: "var(--color-background-danger)",
    fg: "var(--color-text-danger)",
  },
  success: {
    bg: "var(--color-background-success)",
    fg: "var(--color-text-success)",
  },
  // "ai" carries the secondary-accent (coral) voice — used wherever
  // we mark AI / agent-generated content (drafts, agent runs, AI
  // suggestions). The two-voice palette (brand-green = system-
  // validated, coral = AI-drafted) lets operators scan a screen and
  // distinguish rule outcomes from model output without reading.
  ai: {
    bg: "var(--color-accent-light)",
    fg: "var(--color-accent-text)",
  },
  neutral: {
    bg: "var(--color-background-secondary)",
    fg: "var(--color-text-secondary)",
  },
};

interface Props {
  children: ReactNode;
  variant?: PillVariant;
  /** Tiny leading element — usually a Tabler icon. */
  leading?: ReactNode;
  /** Make text smaller (10px vs 11px). Used in dense places like timeline event metadata. */
  size?: "xs" | "sm";
  title?: string;
}

export function Pill({
  children,
  variant = "neutral",
  leading,
  size = "sm",
  title,
}: Props) {
  const s = STYLE[variant];
  const textSize = size === "xs" ? "text-[10px]" : "text-[11px]";
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 rounded px-1.5 py-0.5 font-medium ${textSize}`}
      style={{ background: s.bg, color: s.fg }}
    >
      {leading}
      {children}
    </span>
  );
}
