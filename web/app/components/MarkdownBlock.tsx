"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

/**
 * Shared markdown renderer for assistant-produced text.
 *
 * Why this exists: every LLM in the system returns markdown by
 * default — lists, **bold**, code fences, occasionally tables.
 * Rendering it as raw text (the previous behaviour) means the user
 * sees literal asterisks and pound signs in chat replies, decision
 * verdicts, and timeline quote blocks. This component is the one
 * place we centralise the rendering decisions: which markdown
 * features are enabled (GFM), how the output is styled, and the
 * security constraints (no raw HTML — react-markdown is safe by
 * default; we never set ``rehypeRaw`` or ``skipHtml: false``).
 *
 * GFM (GitHub-Flavoured Markdown) gives us tables, strikethrough,
 * task lists, and autolink — the things actually used in LLM
 * responses and underwriting rationales.
 *
 * Usage:
 *   <MarkdownBlock>{message.text}</MarkdownBlock>
 *
 * Styling is deliberately tight: this matches the surrounding chat
 * bubble / quote-block typography. If you need a different look
 * (e.g. the decision panel's verdict needs larger headings), pass
 * ``variant``.
 */
interface Props {
  children: string;
  /** Layout variant. ``compact`` (default) is tight for chat bubbles;
   *  ``relaxed`` adds more vertical breathing room for long-form
   *  rationales + AALs. */
  variant?: "compact" | "relaxed";
  /** Additional Tailwind classes layered on top of the variant. */
  className?: string;
}

export function MarkdownBlock({
  children,
  variant = "compact",
  className,
}: Props) {
  return (
    <div
      className={[
        "markdown-block",
        variant === "compact" ? "markdown-compact" : "markdown-relaxed",
        className ?? "",
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <ReactMarkdown
        // GFM = tables, strikethrough, task lists, autolinks. Plenty
        // for what agents actually produce, and nothing we don't
        // want.
        remarkPlugins={[remarkGfm]}
        // Open links in new tabs and never trust any rel/target that
        // might come in from upstream — explicit override means
        // markdown can't be used to navigate the parent page.
        components={{
          a: ({ children: linkChildren, href, ...rest }) => (
            <a
              {...rest}
              href={href}
              target="_blank"
              rel="noopener noreferrer"
            >
              {linkChildren}
            </a>
          ),
          // Code blocks default to react-markdown's plain rendering —
          // we DON'T pull in a syntax highlighter (would bloat the
          // bundle significantly). Inline + block code both get the
          // same monospace styling via globals.css `.markdown-block code`.
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
