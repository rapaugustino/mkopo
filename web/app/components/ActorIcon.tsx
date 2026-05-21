import {
  IconAlertTriangle,
  IconFileText,
  IconGavel,
  IconMailForward,
  IconMailOpened,
  IconSend,
  IconSparkles,
  IconUserPlus,
  IconNote,
} from "@tabler/icons-react";

/**
 * Coloured circle for a case-file event. Picks (background, foreground,
 * icon) based on actor type + optional intent. Mirrors the cf-icon-*
 * variants in the case file mockup.
 *
 * The 5 visual archetypes (matching mockup):
 *   - alert    — red, used for risk-signal events
 *   - borrower — amber, used for inbound emails
 *   - user     — info-blue, used for user actions (send, assign, note)
 *   - ai       — brand green, used for AI actions (extraction, summary, decision)
 *   - system   — neutral grey, used for plumbing (application received)
 */

export type ActorType = "agent" | "user" | "system" | "borrower";

/** Refines the icon when the actor type doesn't fully determine the look. */
export type EventIntent =
  | "default"
  | "alert"
  | "send"
  | "reply"
  | "assign"
  | "extract"
  | "summarise"
  | "decide"
  | "note"
  | "intake";

const PALETTE: Record<
  "alert" | "borrower" | "user" | "ai" | "system",
  { bg: string; fg: string }
> = {
  alert: {
    bg: "var(--color-background-danger)",
    fg: "var(--color-text-danger)",
  },
  borrower: {
    bg: "var(--color-background-warning)",
    fg: "var(--color-text-warning)",
  },
  user: { bg: "var(--color-background-info)", fg: "var(--color-text-info)" },
  ai: { bg: "var(--color-brand-light)", fg: "var(--color-brand)" },
  system: {
    bg: "var(--color-background-secondary)",
    fg: "var(--color-text-secondary)",
  },
};

function paletteFor(
  actor: ActorType,
  intent: EventIntent,
): keyof typeof PALETTE {
  if (intent === "alert") return "alert";
  if (actor === "agent") return "ai";
  if (actor === "borrower") return "borrower";
  if (actor === "user") return "user";
  return "system";
}

function iconFor(intent: EventIntent, actor: ActorType) {
  switch (intent) {
    case "alert":
      return IconAlertTriangle;
    case "send":
      return IconSend;
    case "reply":
      return IconMailOpened;
    case "assign":
      return IconUserPlus;
    case "extract":
      return IconSparkles;
    case "summarise":
      return IconFileText;
    case "decide":
      return IconGavel;
    case "note":
      return IconNote;
    case "intake":
      return IconMailForward;
    default:
      // Fall back by actor type
      if (actor === "agent") return IconSparkles;
      if (actor === "user") return IconSend;
      if (actor === "borrower") return IconMailOpened;
      return IconMailForward;
  }
}

interface Props {
  actor: ActorType;
  intent?: EventIntent;
  size?: number;
}

export function ActorIcon({ actor, intent = "default", size = 28 }: Props) {
  const palette = PALETTE[paletteFor(actor, intent)];
  const Icon = iconFor(intent, actor);
  return (
    <div
      className="flex shrink-0 items-center justify-center rounded-full"
      style={{
        width: size,
        height: size,
        background: palette.bg,
        color: palette.fg,
      }}
    >
      <Icon size={Math.round(size * 0.5)} />
    </div>
  );
}
