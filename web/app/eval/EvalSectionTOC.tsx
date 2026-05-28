"use client";

/**
 * Sticky in-page jump-nav for the /eval dashboard.
 *
 * Why this and not a left sidebar: the global nav (top bar) already
 * does the page-level routing — sidebar would force a layout
 * rewrite for one heavy page. Within-page nav is the actual pain
 * here: /eval has ~15 sections (KPI tiles, trend chart, per-field,
 * 10 task cards, 4 diagnostics) and used to be one long scroll.
 *
 * The TOC sits below the BrandHeader and stays visible as you
 * scroll (sticky offset = brand header height + a hair). Click a
 * pill → smooth-scroll to that section. The active pill auto-
 * highlights based on which section is currently centered in the
 * viewport via IntersectionObserver.
 *
 * Sections are addressed by id — see ``EvalSection.tsx`` for the
 * matching heading component.
 */

import { useEffect, useState } from "react";

export interface TOCEntry {
  id: string;
  label: string;
}

interface Props {
  entries: TOCEntry[];
}

export function EvalSectionTOC({ entries }: Props) {
  const [activeId, setActiveId] = useState<string | null>(entries[0]?.id ?? null);

  // Highlight the section currently most-visible in the viewport.
  // IntersectionObserver is the right tool here — scroll-event
  // listeners are jittery and more expensive at scale.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const ids = entries.map((e) => e.id);
    const observed: HTMLElement[] = [];
    for (const id of ids) {
      const el = document.getElementById(id);
      if (el) observed.push(el);
    }
    if (observed.length === 0) return;

    const visible = new Map<string, number>();
    const obs = new IntersectionObserver(
      (rs) => {
        for (const r of rs) {
          if (r.isIntersecting) {
            visible.set(r.target.id, r.intersectionRatio);
          } else {
            visible.delete(r.target.id);
          }
        }
        // Pick the section with the highest visibility ratio. Ties
        // break to the first-listed (lowest scroll position).
        let bestId: string | null = null;
        let bestRatio = -1;
        for (const id of ids) {
          const r = visible.get(id) ?? 0;
          if (r > bestRatio) {
            bestRatio = r;
            bestId = id;
          }
        }
        if (bestId) setActiveId(bestId);
      },
      {
        // 30% threshold = once a third of the section is on-screen
        // it's considered the active one. Lower thresholds make the
        // highlight jitter as sections enter/leave.
        threshold: [0, 0.3, 0.6],
        // Top margin offsets for the sticky header — sections at
        // the bottom of the viewport take precedence visually so
        // we accept slightly-below-fold as "active".
        rootMargin: "-100px 0px -40% 0px",
      },
    );
    for (const el of observed) obs.observe(el);
    return () => obs.disconnect();
  }, [entries]);

  const handleClick = (id: string) => (e: React.MouseEvent) => {
    e.preventDefault();
    const el = document.getElementById(id);
    if (!el) return;
    // Smooth scroll, offset for sticky TOC header. The TOC pill
    // bar is ~36px tall and sits at ``top-[60px]`` (just below the
    // brand bar) — landing 110px below the viewport top keeps the
    // section heading visible instead of tucked under the TOC.
    const top = el.getBoundingClientRect().top + window.scrollY - 110;
    window.scrollTo({ top, behavior: "smooth" });
    history.replaceState(null, "", `#${id}`);
  };

  return (
    <nav
      aria-label="Eval dashboard sections"
      className="sticky top-[60px] z-30 -mx-1 overflow-x-auto rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)]/90 px-1 py-1 backdrop-blur-sm"
    >
      <ul className="flex items-center gap-0.5 text-[11.5px]">
        {entries.map((e) => {
          const isActive = e.id === activeId;
          return (
            <li key={e.id} className="shrink-0">
              <a
                href={`#${e.id}`}
                onClick={handleClick(e.id)}
                className="inline-flex items-center rounded px-2.5 py-1 font-medium transition-colors"
                style={{
                  background: isActive
                    ? "var(--color-background-secondary)"
                    : "transparent",
                  color: isActive
                    ? "var(--color-text-primary)"
                    : "var(--color-text-secondary)",
                }}
                aria-current={isActive ? "true" : undefined}
              >
                {e.label}
              </a>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
