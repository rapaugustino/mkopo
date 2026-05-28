"use client";

/**
 * Hover/focus tooltip primitive.
 *
 * Single source of truth for tooltip styling across the app —
 * Safety dashboard tile labels, Eval metric definitions, scenario
 * category pills, etc. Keep callers thin so the visual language
 * stays consistent.
 *
 * Behavior:
 * - Appears on hover (mouse) AND focus (keyboard / screen reader).
 * - 200ms delay on open so brief mouseovers don't spam tooltips.
 * - Closes instantly on mouseleave/blur — no exit delay (that feels
 *   sluggish on dense pages).
 * - Renders above its trigger by default; flips to below if the
 *   container is near the top of the viewport.
 *
 * Why not a third-party (Radix / Headless UI):
 * - One screen, one primitive: the cost of a new dependency here
 *   isn't justified.
 * - We want full control over the typography + the
 *   ``content`` prop's React-node support (so callers can embed
 *   citations and short formulas).
 *
 * Accessibility:
 * - The trigger gets ``aria-describedby`` pointing at the tooltip
 *   when open. Screen readers read the label then the description.
 * - ``role="tooltip"`` on the floating element.
 * - Escape key closes (handy when a tooltip steals focus).
 */

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
  type ReactNode,
} from "react";

interface Props {
  /** What you're hovering over. */
  children: ReactNode;
  /** What to show in the tooltip. ReactNode so callers can embed
   *  formulas, citations, multi-line definitions. */
  content: ReactNode;
  /** Maximum width of the floating bubble. Default 280px keeps
   *  tooltips readable without dominating the page. */
  maxWidth?: number;
  /** Force placement. Default "auto" picks above if there's room,
   *  below otherwise. */
  placement?: "auto" | "top" | "bottom";
  /** When true, the trigger gets a dotted underline to advertise
   *  the tooltip's existence. Use for inline label text; skip on
   *  large clickable areas (whole cards) where the underline would
   *  read as noise. */
  underline?: boolean;
}

const OPEN_DELAY_MS = 200;

export function Tooltip({
  children,
  content,
  maxWidth = 280,
  placement = "auto",
  underline = false,
}: Props) {
  const [open, setOpen] = useState(false);
  const [actualPlacement, setActualPlacement] = useState<"top" | "bottom">(
    "top",
  );
  // Horizontal anchor — ``center`` is the default; flips to ``left``
  // or ``right`` when the trigger sits too close to the matching
  // viewport edge for the centred-on-trigger tooltip to fit. This
  // prevents the tooltip from clipping inside narrow side-drawers
  // and at the edges of the eval-page TOC.
  const [hAnchor, setHAnchor] = useState<"center" | "left" | "right">(
    "center",
  );
  const tooltipId = useId();
  const triggerRef = useRef<HTMLSpanElement>(null);
  const openTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelOpenTimer = useCallback(() => {
    if (openTimerRef.current) {
      clearTimeout(openTimerRef.current);
      openTimerRef.current = null;
    }
  }, []);

  const handleOpen = useCallback(() => {
    cancelOpenTimer();
    openTimerRef.current = setTimeout(() => {
      // Decide vertical + horizontal placement based on the trigger's
      // position in the viewport.
      if (triggerRef.current) {
        const rect = triggerRef.current.getBoundingClientRect();
        const vw = window.innerWidth;
        // Vertical: flip below if there's < 80px above the trigger.
        if (placement === "auto") {
          setActualPlacement(rect.top < 80 ? "bottom" : "top");
        } else {
          setActualPlacement(placement);
        }
        // Horizontal: if the trigger sits within ``maxWidth / 2 + 12``
        // px of either edge, anchor the tooltip to that edge so it
        // grows inwards instead of clipping. 12px is the breathing
        // gap to the viewport / drawer wall.
        const halfWidth = maxWidth / 2 + 12;
        const centerX = (rect.left + rect.right) / 2;
        if (centerX < halfWidth) {
          setHAnchor("left");
        } else if (centerX > vw - halfWidth) {
          setHAnchor("right");
        } else {
          setHAnchor("center");
        }
      }
      setOpen(true);
    }, OPEN_DELAY_MS);
  }, [cancelOpenTimer, placement, maxWidth]);

  const handleClose = useCallback(() => {
    cancelOpenTimer();
    setOpen(false);
  }, [cancelOpenTimer]);

  // Escape closes — accessibility nicety for keyboard users.
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") handleClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, handleClose]);

  useEffect(() => () => cancelOpenTimer(), [cancelOpenTimer]);

  return (
    <span
      ref={triggerRef}
      className="relative inline-flex items-center"
      onMouseEnter={handleOpen}
      onMouseLeave={handleClose}
      onFocus={handleOpen}
      onBlur={handleClose}
      aria-describedby={open ? tooltipId : undefined}
    >
      <span
        className={
          underline
            ? "cursor-help underline decoration-dotted decoration-[var(--color-border-secondary)] underline-offset-2"
            : undefined
        }
        // tabIndex makes the tooltip keyboard-focusable when the
        // wrapped element isn't focusable on its own (e.g. a span
        // label inside a card).
        tabIndex={underline ? 0 : undefined}
      >
        {children}
      </span>
      {open && (
        <span
          role="tooltip"
          id={tooltipId}
          // Horizontal positioning: see ``hAnchor`` logic in
          // ``handleOpen``. ``center`` is the default (most cases);
          // ``left``/``right`` only triggered near viewport edges so
          // the tooltip doesn't clip inside drawers or off-screen.
          className={
            "pointer-events-none absolute z-50 rounded-md px-2.5 py-1.5 text-[11.5px] leading-relaxed shadow-md " +
            (hAnchor === "center"
              ? "left-1/2 -translate-x-1/2"
              : hAnchor === "left"
                ? "left-0"
                : "right-0")
          }
          style={{
            background: "var(--color-background-inverse, #1a1a1a)",
            color: "var(--color-text-inverse, #fafafa)",
            maxWidth,
            // Position above (mt-1.5 from bottom) or below (mt-1.5
            // from top) the trigger.
            top: actualPlacement === "bottom" ? "calc(100% + 6px)" : undefined,
            bottom: actualPlacement === "top" ? "calc(100% + 6px)" : undefined,
            whiteSpace: "normal",
            // Width — fluid up to maxWidth so short content stays
            // tight, long content wraps cleanly.
            width: "max-content",
          }}
        >
          {content}
        </span>
      )}
    </span>
  );
}

/**
 * Convenience: a small ``ⓘ`` glyph that opens the tooltip on hover.
 * Use when the label text shouldn't be cluttered with a dotted
 * underline (dense KPI cards).
 */
export function InfoTooltip({
  content,
  maxWidth,
}: {
  content: ReactNode;
  maxWidth?: number;
}) {
  return (
    <Tooltip content={content} maxWidth={maxWidth}>
      <span
        aria-label="More info"
        className="ml-1 inline-flex h-3.5 w-3.5 cursor-help items-center justify-center rounded-full text-[9px] font-medium"
        style={{
          background: "var(--color-background-secondary)",
          color: "var(--color-text-tertiary)",
          border: "0.5px solid var(--color-border-tertiary)",
        }}
        tabIndex={0}
      >
        ?
      </span>
    </Tooltip>
  );
}
