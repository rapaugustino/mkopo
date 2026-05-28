"use client";

import { useEffect, useState } from "react";
import { IconExternalLink, IconLoader2, IconX } from "@tabler/icons-react";
import { motion } from "motion/react";

/**
 * In-app document viewer.
 *
 * Renders a short-lived presigned download URL in one of three shapes:
 *
 *   - **PDFs** ⟶ browser-native viewer inside an iframe (Chrome,
 *     Safari, Firefox all ship one). No PDF.js bundle; we let the
 *     browser do the work.
 *   - **Images** ⟶ <img> with object-contain.
 *   - **Anything else** ⟶ "open in new tab" link. Word docs, CSVs,
 *     binaries — the user picks the right local app.
 *
 * The fetch is delegated via the `fetchUrl` prop so this same
 * component works on both surfaces (staff bearer auth and borrower
 * cookie auth) without knowing which one is calling it.
 */
interface DocumentViewerProps {
  /** Async fn that mints the presigned URL. Returns the response
   *  payload from either ``api.getDocumentDownloadUrl`` (staff) or
   *  ``borrowerAuthApi.getDocumentDownloadUrl`` (borrower). */
  fetchUrl: () => Promise<{
    url: string;
    filename: string;
    content_type: string;
    expires_in_seconds: number;
  }>;
  /** Display filename for the modal title. Optional — falls back to
   *  whatever the API returns. */
  filename?: string;
  /** Closes the modal. The component manages its own visibility — the
   *  parent just unmounts when this fires. */
  onClose: () => void;
}

export function DocumentViewer({
  fetchUrl,
  filename: initialFilename,
  onClose,
}: DocumentViewerProps) {
  const [state, setState] = useState<
    | { kind: "loading" }
    | { kind: "ready"; url: string; filename: string; contentType: string }
    | { kind: "error"; message: string }
  >({ kind: "loading" });

  useEffect(() => {
    let cancelled = false;
    fetchUrl()
      .then((res) => {
        if (cancelled) return;
        setState({
          kind: "ready",
          url: res.url,
          filename: res.filename,
          contentType: res.content_type,
        });
      })
      .catch((e) => {
        if (cancelled) return;
        setState({ kind: "error", message: String(e) });
      });
    return () => {
      cancelled = true;
    };
  }, [fetchUrl]);

  // Close on Escape — modal keyboard convention.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const titleFilename =
    state.kind === "ready" ? state.filename : (initialFilename ?? "Document");

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4"
      style={{ background: "var(--color-overlay-strong)" }}
      onClick={onClose}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.96 }}
        animate={{ opacity: 1, scale: 1 }}
        exit={{ opacity: 0, scale: 0.96 }}
        transition={{ duration: 0.15 }}
        className="relative flex h-[88vh] w-full max-w-5xl flex-col overflow-hidden rounded-xl border border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] shadow-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between gap-3 border-b border-[var(--color-border-tertiary)] px-4 py-3">
          <div className="min-w-0 flex-1">
            <p className="truncate text-[13px] font-medium">{titleFilename}</p>
            {state.kind === "ready" && (
              <p className="truncate text-[11px] text-[var(--color-text-tertiary)]">
                {state.contentType} · link expires in 5 minutes
              </p>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-1">
            {state.kind === "ready" && (
              <a
                href={state.url}
                target="_blank"
                rel="noopener noreferrer"
                title="Open in new tab"
                className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-background-secondary)]"
              >
                <IconExternalLink size={14} />
              </a>
            )}
            <button
              type="button"
              onClick={onClose}
              title="Close (Esc)"
              className="inline-flex h-7 w-7 items-center justify-center rounded-md text-[var(--color-text-secondary)] hover:bg-[var(--color-background-secondary)]"
            >
              <IconX size={14} />
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-hidden bg-[var(--color-background-secondary)]">
          {state.kind === "loading" && (
            <div className="flex h-full items-center justify-center gap-2 text-[12.5px] text-[var(--color-text-secondary)]">
              <IconLoader2 size={14} className="animate-spin" />
              Loading document…
            </div>
          )}
          {state.kind === "error" && (
            <div className="flex h-full flex-col items-center justify-center gap-2 px-6 text-center">
              <p className="text-[13px] font-medium text-[var(--color-text-danger)]">
                Couldn't load this document.
              </p>
              <p className="max-w-md text-[12px] text-[var(--color-text-secondary)]">
                {state.message}
              </p>
            </div>
          )}
          {state.kind === "ready" && <ViewerBody {...state} />}
        </div>
      </motion.div>
    </div>
  );
}

function ViewerBody({
  url,
  filename,
  contentType,
}: {
  url: string;
  filename: string;
  contentType: string;
}) {
  if (contentType === "application/pdf") {
    // Browser-native PDF viewer. No PDF.js bundle — Chromium, Safari,
    // and Firefox all ship one. The `#toolbar=0` fragment hides the
    // bulky default toolbar; users still get scroll + zoom via the
    // browser's own controls.
    return (
      <iframe
        src={`${url}#toolbar=0`}
        title={filename}
        className="h-full w-full border-0"
      />
    );
  }
  if (contentType.startsWith("image/")) {
    return (
      <div className="flex h-full items-center justify-center p-4">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={url}
          alt={filename}
          className="max-h-full max-w-full object-contain"
        />
      </div>
    );
  }
  if (contentType.startsWith("text/")) {
    return (
      <iframe
        src={url}
        title={filename}
        className="h-full w-full border-0 bg-white"
      />
    );
  }
  // Fallback: anything we can't render inline. Punt to the user's
  // local app via "Download to view".
  return (
    <div className="flex h-full flex-col items-center justify-center gap-3 px-6 text-center">
      <p className="text-[13px] font-medium">In-app preview not available</p>
      <p className="max-w-md text-[12px] text-[var(--color-text-secondary)]">
        This file type ({contentType || "unknown"}) doesn't render in the
        browser. Download it to view in the appropriate application.
      </p>
      <a
        href={url}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 rounded-md bg-[var(--color-text-primary)] px-3 py-1.5 text-[12px] font-medium text-[var(--color-background-primary)] hover:opacity-90"
      >
        <IconExternalLink size={12} />
        Download {filename}
      </a>
    </div>
  );
}
