import type { ReactNode } from "react";

interface Props {
  children: ReactNode;
  /** Optional leading icon component (e.g. an `@tabler/icons-react` import). */
  Icon?: React.ComponentType<{ size?: number }>;
  /** Optional right-aligned slot (timestamps, counts, tiny actions). */
  trailing?: ReactNode;
  /** Tighten the bottom margin. Use inside dense cards (timeline rows). */
  dense?: boolean;
}

/**
 * Standard section heading used inside cards — mirrors `.lo-section-title`
 * from the workspace + decision mockups: 12px, medium weight, mixed
 * case, secondary text colour, optional leading icon with 6px gap.
 *
 * This is intentionally distinct from the uppercase-tracking treatment
 * used on table headers and on stage tile labels (pipeline view). The
 * difference makes content-card section labels feel more conversational
 * — less data-grid, more analyst-tool.
 */
export function SectionLabel({ children, Icon, trailing, dense = false }: Props) {
  return (
    <div
      className={`flex items-center justify-between gap-3 ${
        dense ? "mb-1.5" : "mb-2.5"
      }`}
    >
      <p className="flex items-center gap-1.5 text-[12px] font-medium text-[var(--color-text-secondary)]">
        {Icon && <Icon size={13} />}
        {children}
      </p>
      {trailing && (
        <span className="text-[11px] font-normal text-[var(--color-text-tertiary)]">
          {trailing}
        </span>
      )}
    </div>
  );
}
