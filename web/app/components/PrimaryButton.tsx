import type { ButtonHTMLAttributes, ReactNode } from "react";

interface Props extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** Optional Tabler icon component, rendered left of the label. */
  Icon?: React.ComponentType<{ size?: number }>;
  /** Tighter padding for inline buttons (sm = AskTheFile send),
   *  default md (toolbar buttons), lg for primary CTAs in wizards and
   *  full-page empty states where the button is the page's primary
   *  affordance (apply wizard "Continue", annotation submit, etc.). */
  size?: "sm" | "md" | "lg";
  children: ReactNode;
}

/**
 * The brand-green primary action button. Centralised so we don't keep
 * inlining ``bg: var(--color-brand)`` + ``text-white`` everywhere — and
 * so the *text* colour matches the mockups: the prototypes use
 * ``--color-brand-light`` (#E1F5EE), a faintly green-tinted off-white,
 * not pure ``#fff``. The tint is subtle but it's the difference
 * between "generic primary button" and "this app has a brand".
 *
 * Usage:
 *     <PrimaryButton Icon={IconSparkles} onClick={...}>Run intake</PrimaryButton>
 *     <PrimaryButton size="lg" type="submit">Continue</PrimaryButton>
 */
export function PrimaryButton({
  Icon,
  size = "md",
  children,
  className = "",
  ...rest
}: Props) {
  const padding =
    size === "sm" ? "px-2.5 py-1" : size === "lg" ? "px-4 py-2" : "px-3 py-1.5";
  const text = size === "lg" ? "text-[13px]" : "text-xs";
  const iconSize = size === "lg" ? 16 : 14;
  return (
    <button
      {...rest}
      className={`flex items-center gap-1.5 rounded-md ${text} font-medium disabled:opacity-50 ${padding} ${className}`}
      style={{
        background: "var(--color-brand)",
        color: "var(--color-brand-light)",
        ...rest.style,
      }}
    >
      {Icon && <Icon size={iconSize} />}
      {children}
    </button>
  );
}
