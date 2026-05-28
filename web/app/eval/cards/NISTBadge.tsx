"use client";

/**
 * NIST AI 600-1 risk-category badge.
 *
 * NIST's *Artificial Intelligence Risk Management Framework:
 * Generative Artificial Intelligence Profile* (AI 600-1, July
 * 2024) enumerates 12 GenAI-specific risk categories — each with
 * its own suggested controls. The eval-page cards address
 * different categories; this badge lets a regulator scanning the
 * dashboard map a card → control objective without reading the
 * card's body text.
 *
 * The categories included here are the ones our cards actually
 * cover. Adding a new category is a one-line addition to
 * ``NIST_CATEGORIES`` below.
 *
 * Reference: https://nvlpubs.nist.gov/nistpubs/ai/NIST.AI.600-1.pdf
 */

import { Tooltip } from "@/app/components/Tooltip";

/** Friendly label → tooltip body. Tooltip text quotes the NIST 600-1
 *  category description so the link to the framework is direct. */
export const NIST_CATEGORIES = {
  confabulation: {
    label: "Confabulation",
    body: "NIST AI 600-1 §2.2 — the production of confidently stated but erroneous or false content. The hallucination canary. Faithfulness / groundedness / decision-verdict accuracy all map here.",
  },
  harmful_bias: {
    label: "Harmful Bias",
    body: "NIST AI 600-1 §2.6 — systematic and unfair discrimination resulting from training data, model design, or deployment context. The Adverse Impact Ratio screen lives here.",
  },
  data_privacy: {
    label: "Data Privacy",
    body: "NIST AI 600-1 §2.7 — unauthorized exposure of training data, inputs, or outputs. Tooltip-only on this dashboard; the borrower-auth + audit-trail surfaces are the load-bearing controls.",
  },
  info_security: {
    label: "Information Security",
    body: "NIST AI 600-1 §2.10 — adversarial inputs, prompt injection, data poisoning. Adversarial-injection coverage + refusal-rate trend address this directly.",
  },
  info_integrity: {
    label: "Information Integrity",
    body: "NIST AI 600-1 §2.11 — content provenance, accuracy of conveyed information, traceability. Calibration, AAL fidelity, per-agent economics all serve provenance / traceability.",
  },
  value_chain: {
    label: "Value Chain",
    body: "NIST AI 600-1 §2.12 — risks from third-party components, model upgrades, supply chain. The per-agent $/run + p95 latency card surfaces upstream-model regressions.",
  },
  cbrn: {
    label: "Dangerous Content",
    body: "NIST AI 600-1 §2.1 / §2.4 — CBRN, illegal, or otherwise dangerous content generation. Not directly relevant to credit decisioning, but the AAL drafter prompt-injection coverage transitively protects against this in chat surfaces.",
  },
} as const;

export type NISTCategoryKey = keyof typeof NIST_CATEGORIES;

interface Props {
  category: NISTCategoryKey;
}

/**
 * Tiny inline badge: dotted-underlined label that opens the NIST
 * 600-1 category description on hover. Designed to sit in the
 * footer line of an eval card without dominating the layout.
 */
export function NISTBadge({ category }: Props) {
  const meta = NIST_CATEGORIES[category];
  return (
    <Tooltip content={meta.body} underline maxWidth={340}>
      <span
        className="inline-flex items-center gap-1 rounded px-1 py-0 text-[10px] font-medium"
        style={{
          background: "var(--color-background-secondary)",
          color: "var(--color-text-secondary)",
        }}
      >
        NIST AI 600-1 · {meta.label}
      </span>
    </Tooltip>
  );
}
