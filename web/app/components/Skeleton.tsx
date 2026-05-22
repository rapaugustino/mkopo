import type { CSSProperties } from "react";

interface Props {
  /** Tailwind width class (``w-24``, ``w-full``, etc.) or a CSS width string. */
  width?: string;
  /** Tailwind height class or pixel string. Defaults to 12px. */
  height?: string;
  /** Rounded shape: default md, "full" for circles, "sm" for chips. */
  shape?: "sm" | "md" | "lg" | "full";
  className?: string;
}

const RADIUS: Record<NonNullable<Props["shape"]>, string> = {
  sm: "rounded-sm",
  md: "rounded-md",
  lg: "rounded-lg",
  full: "rounded-full",
};

/**
 * A single placeholder rectangle for content that's loading.
 *
 * Why a primitive: we want skeleton screens to read as one consistent
 * idiom across the app — the gentle shimmer, the warm neutral colour,
 * the radius matching the eventual content. Composing many small
 * Skeletons into shape-matched layouts (timeline rows, KPI tiles,
 * pipeline rows) reads as the same thing loading, not five different
 * loading spinners.
 *
 * The shimmer is a CSS gradient animation (no JS, no Framer Motion
 * dep needed) — Tailwind's ``animate-pulse`` is too jumpy for this
 * scale of UI, so we use a slower linear gradient sweep.
 */
export function Skeleton({
  width,
  height = "h-3",
  shape = "md",
  className = "",
}: Props) {
  const widthClass = width ?? "w-full";
  // If the caller passed a Tailwind utility (starts with "w-" / "h-"),
  // append it as a class; otherwise treat it as a raw CSS value.
  const isUtilWidth = widthClass.startsWith("w-") || widthClass.startsWith("max-w-");
  const isUtilHeight = height.startsWith("h-");
  const style: CSSProperties = {};
  if (!isUtilWidth) style.width = widthClass;
  if (!isUtilHeight) style.height = height;

  return (
    <span
      aria-hidden="true"
      className={`mkopo-skeleton inline-block ${RADIUS[shape]} ${
        isUtilWidth ? widthClass : ""
      } ${isUtilHeight ? height : ""} ${className}`}
      style={style}
    />
  );
}
