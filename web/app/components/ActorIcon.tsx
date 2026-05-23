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

// Render the right icon for (intent, actor). Implemented as a real
// component (not a factory returning a component reference) so React
// doesn't see a new "component" being created on every render — the
// react-hooks/static-components lint passes, and the icon's identity
// is stable across renders that don't change the relevant props.
function IntentIcon({
  intent,
  actor,
  size,
}: {
  intent: EventIntent;
  actor: ActorType;
  size: number;
}) {
  const px = Math.round(size);
  switch (intent) {
    case "alert":
      return <IconAlertTriangle size={px} />;
    case "send":
      return <IconSend size={px} />;
    case "reply":
      return <IconMailOpened size={px} />;
    case "assign":
      return <IconUserPlus size={px} />;
    case "extract":
      return <IconSparkles size={px} />;
    case "summarise":
      return <IconFileText size={px} />;
    case "decide":
      return <IconGavel size={px} />;
    case "note":
      return <IconNote size={px} />;
    case "intake":
      return <IconMailForward size={px} />;
    default:
      // Fall back by actor type
      if (actor === "agent") return <IconSparkles size={px} />;
      if (actor === "user") return <IconSend size={px} />;
      if (actor === "borrower") return <IconMailOpened size={px} />;
      return <IconMailForward size={px} />;
  }
}

interface Props {
  actor: ActorType;
  intent?: EventIntent;
  size?: number;
}

export function ActorIcon({ actor, intent = "default", size = 28 }: Props) {
  const palette = PALETTE[paletteFor(actor, intent)];
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
      <IntentIcon intent={intent} actor={actor} size={size * 0.5} />
    </div>
  );
}
