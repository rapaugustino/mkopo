"use client";

/**
 * Custom dropdown primitive — styled options popover.
 *
 * Native ``<select>`` + ``<option>`` look unbranded because the
 * popup list is OS-controlled and unstyleable cross-browser
 * (Chrome shows a different list than Safari, both override our
 * tokens). For filter chips, picker rows, and other compact-select
 * surfaces where the popup list IS visible to the user, we use
 * this instead.
 *
 * Behaviours:
 *   - Click trigger to open; click outside or Escape to close.
 *   - Keyboard: ArrowUp/Down to move highlight, Enter to select,
 *     Home/End to jump.
 *   - ``aria-haspopup="listbox"`` + ``aria-expanded`` on the
 *     trigger; ``role="listbox"`` on the popover with
 *     ``aria-activedescendant`` on the highlighted option for
 *     screen-reader support.
 *   - Generic over the option value type so call sites stay typed.
 *
 * Style matches ``form-input-compact`` for the trigger so a
 * Dropdown alongside a native compact select reads as the same
 * design language.
 */

import {
  useCallback,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { IconCheck, IconChevronDown } from "@tabler/icons-react";

export interface DropdownOption<T extends string = string> {
  value: T;
  label: string;
}

interface Props<T extends string> {
  value: T;
  options: DropdownOption<T>[];
  onChange: (value: T) => void;
  /** Used for the trigger's ``aria-label`` + as the placeholder
   *  text when no option matches ``value`` (defensive — shouldn't
   *  happen in normal flow). */
  label?: string;
  /** Disable the entire control. The trigger renders muted; the
   *  popover doesn't open on click. */
  disabled?: boolean;
}

export function Dropdown<T extends string>({
  value,
  options,
  onChange,
  label,
  disabled = false,
}: Props<T>) {
  const [open, setOpen] = useState(false);
  // Keyboard-highlighted option index. Resets to the current
  // selection's index when the popover opens so ArrowDown starts
  // from the selected row.
  const [highlight, setHighlight] = useState<number>(() =>
    Math.max(0, options.findIndex((o) => o.value === value)),
  );
  const listboxId = useId();
  const triggerRef = useRef<HTMLButtonElement>(null);
  const popRef = useRef<HTMLDivElement>(null);

  const current = options.find((o) => o.value === value);

  const close = useCallback(() => setOpen(false), []);

  const choose = useCallback(
    (v: T) => {
      onChange(v);
      setOpen(false);
      // Return focus to the trigger so the next Tab continues
      // logically from where the user was.
      triggerRef.current?.focus();
    },
    [onChange],
  );

  // Toggle open + reset highlight on open. Doing this in the click
  // handler (instead of a useEffect that watches ``open``) avoids
  // the set-state-in-effect cascade the linter flags — and we don't
  // need an effect since the only transitions into "open" come from
  // user input on this control.
  const handleToggle = useCallback(() => {
    if (disabled) return;
    setOpen((wasOpen) => {
      if (!wasOpen) {
        const idx = options.findIndex((o) => o.value === value);
        setHighlight(idx >= 0 ? idx : 0);
      }
      return !wasOpen;
    });
  }, [disabled, options, value]);

  // Close on outside click.
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      const t = e.target as Node;
      if (
        !popRef.current?.contains(t) &&
        !triggerRef.current?.contains(t)
      ) {
        close();
      }
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open, close]);

  // Keyboard handling on the trigger / popover. We attach to
  // window while open so Arrow keys work even if focus isn't
  // exactly on the listbox (e.g. user clicked the trigger via
  // mouse but then reached for the arrow keys).
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
        triggerRef.current?.focus();
      } else if (e.key === "ArrowDown") {
        e.preventDefault();
        setHighlight((h) => Math.min(options.length - 1, h + 1));
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setHighlight((h) => Math.max(0, h - 1));
      } else if (e.key === "Home") {
        e.preventDefault();
        setHighlight(0);
      } else if (e.key === "End") {
        e.preventDefault();
        setHighlight(options.length - 1);
      } else if (e.key === "Enter") {
        e.preventDefault();
        const opt = options[highlight];
        if (opt) choose(opt.value);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, options, highlight, choose, close]);

  return (
    <div className="relative inline-block">
      <button
        ref={triggerRef}
        type="button"
        onClick={handleToggle}
        disabled={disabled}
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-label={label}
        aria-controls={open ? listboxId : undefined}
        className="inline-flex items-center gap-1.5 rounded-md border-[0.5px] px-2 py-1 text-[11.5px] font-medium transition-colors focus:outline-none disabled:opacity-50"
        style={{
          borderColor: open
            ? "var(--color-brand)"
            : "var(--color-border-tertiary)",
          background: "var(--color-background-primary)",
          color: "var(--color-text-primary)",
          boxShadow: open
            ? "0 0 0 3px rgba(10, 125, 98, 0.08)"
            : undefined,
        }}
      >
        <span className="truncate">
          {current?.label ?? label ?? "—"}
        </span>
        <IconChevronDown
          size={12}
          stroke={1.75}
          style={{
            color: "var(--color-text-secondary)",
            transition: "transform 120ms ease",
            transform: open ? "rotate(180deg)" : "none",
          }}
        />
      </button>

      {open && (
        <div
          ref={popRef}
          id={listboxId}
          role="listbox"
          aria-activedescendant={
            options[highlight] ? `${listboxId}-${highlight}` : undefined
          }
          className="absolute left-0 top-full z-50 mt-1 min-w-full overflow-hidden rounded-md border-[0.5px]"
          style={{
            borderColor: "var(--color-border-tertiary)",
            background: "var(--color-background-primary)",
            boxShadow: "var(--shadow-popover)",
            // Cap height + scroll for long lists; keeps the popover
            // from overflowing the viewport in tight rows.
            maxHeight: "min(40vh, 280px)",
            overflowY: "auto",
          }}
        >
          {options.map((o, i) => {
            const selected = o.value === value;
            const highlighted = i === highlight;
            return (
              <button
                key={o.value}
                id={`${listboxId}-${i}`}
                type="button"
                role="option"
                aria-selected={selected}
                onMouseEnter={() => setHighlight(i)}
                onClick={() => choose(o.value)}
                className="flex w-full items-center gap-2 whitespace-nowrap px-2.5 py-1.5 text-left text-[11.5px]"
                style={{
                  background: highlighted
                    ? "var(--color-background-secondary)"
                    : "transparent",
                  color: "var(--color-text-primary)",
                  fontWeight: selected ? 600 : 400,
                }}
              >
                <span
                  className="inline-flex h-3 w-3 shrink-0 items-center justify-center"
                  style={{ color: "var(--color-brand)" }}
                >
                  {selected && <IconCheck size={11} stroke={2.25} />}
                </span>
                <span className="truncate">{o.label}</span>
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
