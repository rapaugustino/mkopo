"use client";

import { useCallback, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  IconCloudUpload,
  IconFile,
  IconFileTypePdf,
  IconFileTypeTxt,
  IconLoader2,
} from "@tabler/icons-react";
import { toast } from "sonner";
import { api, type LoanDocument } from "@/lib/api";
import { humanizeDocType } from "@/lib/humanize";
import { DocumentViewer } from "@/app/components/DocumentViewer";
import { Pill } from "@/app/components/Pill";
import { SectionLabel } from "@/app/components/SectionLabel";

interface Props {
  loanId: string;
}

// ---- helpers --------------------------------------------------------------

const KB = 1024;
const MB = KB * 1024;
function formatSize(bytes: number): string {
  if (bytes >= MB) return `${(bytes / MB).toFixed(1)} MB`;
  if (bytes >= KB) return `${(bytes / KB).toFixed(0)} KB`;
  return `${bytes} B`;
}

// Real component (not a factory returning a component type) — React
// doesn't have to re-create a component during render, and the
// react-hooks/static-components lint passes.
function FileTypeIcon({
  contentType,
  size = 14,
}: {
  contentType: string;
  size?: number;
}) {
  if (contentType === "application/pdf") return <IconFileTypePdf size={size} />;
  if (contentType.startsWith("text/")) return <IconFileTypeTxt size={size} />;
  return <IconFile size={size} />;
}

function relativeTime(iso: string): string {
  const diff = Date.now() - new Date(iso).getTime();
  const min = Math.floor(diff / 60_000);
  if (min < 1) return "just now";
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.floor(hr / 24);
  if (day < 30) return `${day}d ago`;
  return new Date(iso).toLocaleDateString();
}

// ---- row ------------------------------------------------------------------

function DocRow({
  doc,
  onOpen,
}: {
  doc: LoanDocument;
  onOpen: (doc: LoanDocument) => void;
}) {
  const ocrPages = doc.extract.pages_needing_ocr ?? 0;
  const totalPages = doc.extract.page_count;
  return (
    <button
      type="button"
      onClick={() => onOpen(doc)}
      className="-mx-2 flex w-full items-center justify-between gap-3 rounded-md px-2 py-2 text-left text-[12.5px] transition-colors hover:bg-[var(--color-background-secondary)] focus:outline-none focus-visible:bg-[var(--color-background-secondary)]"
    >
      <div className="flex min-w-0 items-center gap-2.5">
        <span
          className="inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md"
          style={{
            background: "var(--color-background-secondary)",
            color: "var(--color-text-secondary)",
          }}
        >
          <FileTypeIcon contentType={doc.content_type} size={14} />
        </span>
        <div className="min-w-0">
          <p className="truncate font-medium">{doc.filename}</p>
          <p className="truncate text-[11px] text-[var(--color-text-tertiary)]">
            {humanizeDocType(doc.doc_type)} · {formatSize(doc.size_bytes)}
            {totalPages ? ` · ${totalPages} page${totalPages === 1 ? "" : "s"}` : ""}
            {" · "}
            {relativeTime(doc.created_at)}
          </p>
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-1.5">
        {ocrPages > 0 && (
          <Pill variant="warn" size="xs">
            {ocrPages} page{ocrPages === 1 ? "" : "s"} need OCR
          </Pill>
        )}
        {doc.extract.method === "pypdf" && ocrPages === 0 && (
          <Pill variant="success" size="xs">
            Text extracted
          </Pill>
        )}
      </div>
    </button>
  );
}

// ---- dropzone -------------------------------------------------------------

function Dropzone({
  onFiles,
  uploading,
  acceptHint,
}: {
  onFiles: (files: File[]) => void;
  uploading: boolean;
  acceptHint: string;
}) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [dragOver, setDragOver] = useState(false);

  const pick = () => inputRef.current?.click();
  const onDrop = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    setDragOver(false);
    const files = Array.from(e.dataTransfer.files);
    if (files.length > 0) onFiles(files);
  };

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={pick}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") pick();
      }}
      onDragOver={(e) => {
        e.preventDefault();
        setDragOver(true);
      }}
      onDragLeave={() => setDragOver(false)}
      onDrop={onDrop}
      className="flex flex-col items-center gap-1.5 rounded-md border border-dashed py-5 text-center transition-colors cursor-pointer"
      style={{
        borderColor: dragOver
          ? "var(--color-brand)"
          : "var(--color-border-tertiary)",
        background: dragOver
          ? "var(--color-background-success)"
          : "var(--color-background-secondary)",
      }}
    >
      <span
        className="inline-flex h-7 w-7 items-center justify-center rounded-full"
        style={{
          background: "var(--color-background-primary)",
          color: dragOver
            ? "var(--color-brand)"
            : "var(--color-text-secondary)",
        }}
      >
        {uploading ? (
          <IconLoader2 size={14} className="animate-spin" />
        ) : (
          <IconCloudUpload size={14} />
        )}
      </span>
      <p className="text-[12px] text-[var(--color-text-primary)]">
        {uploading ? "Uploading…" : "Drop files here or click to upload"}
      </p>
      <p className="text-[11px] text-[var(--color-text-tertiary)]">{acceptHint}</p>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept=".pdf,.txt,.md,application/pdf,text/plain,text/markdown"
        className="sr-only"
        onChange={(e) => {
          const files = Array.from(e.target.files ?? []);
          if (files.length > 0) onFiles(files);
          e.target.value = ""; // allow re-selecting the same file
        }}
      />
    </div>
  );
}

// ---- panel ----------------------------------------------------------------

/**
 * Documents attached to a loan, with a drop zone for adding more.
 *
 * Sits above the case file timeline on the activity tab. The pattern
 * is "first-class object list + inline upload" — same as Linear's
 * attachments, Notion's database views, the Anthropic Console's file
 * uploader. Two reasons:
 *
 * 1. The whole loan workflow revolves around the document packet. If
 *    you have to leave this view to add a file, the agent run that
 *    needs the file feels unrelated.
 * 2. Per-file metadata (OCR status, page count, extracted-text size)
 *    has to be visible so the underwriter understands what the agents
 *    will actually see. A "5 pages need OCR" chip is honest about the
 *    quality of the input the AI is reasoning over.
 */
export function DocsPanel({ loanId }: Props) {
  const queryClient = useQueryClient();
  const docsQuery = useQuery<LoanDocument[], Error>({
    queryKey: ["loan", loanId, "documents"],
    queryFn: () => api.listDocuments(loanId),
  });

  const [uploadError, setUploadError] = useState<string | null>(null);

  const upload = useMutation({
    mutationFn: async (file: File) => api.uploadDocument(loanId, file),
    onSuccess: async (result, file) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["loan", loanId, "documents"] }),
        queryClient.invalidateQueries({ queryKey: ["loan", loanId, "audit"] }),
      ]);
      // Per-file confirmation; surfaces extraction stats so the upload
      // feels honest about what the agents will actually see.
      const stats = result.extract;
      const detail =
        stats.method === "pypdf" && stats.pages_needing_ocr
          ? `${stats.pages_with_text}/${stats.page_count} pages extracted · ${stats.pages_needing_ocr} need OCR`
          : stats.method === "pypdf"
            ? `${stats.page_count} page${stats.page_count === 1 ? "" : "s"} extracted`
            : stats.method === "decode"
              ? `${(stats.char_count ?? 0).toLocaleString()} chars indexed`
              : "Stored (no text extraction)";
      toast.success(`Uploaded ${file.name}`, { description: detail });
    },
    onError: (e, file) => {
      const msg = e instanceof Error ? e.message : String(e);
      setUploadError(msg);
      toast.error(`Upload failed: ${file.name}`, { description: msg });
    },
  });

  const onFiles = async (files: File[]) => {
    setUploadError(null);
    // Upload sequentially to keep the per-file progress understandable.
    // Parallel uploads would race for the same loan_id partition on the
    // chunk-embed step (OK functionally; just noisier in logs).
    for (const f of files) {
      try {
        await upload.mutateAsync(f);
      } catch {
        return; // onError already captured the message
      }
    }
  };

  const docs = docsQuery.data ?? [];

  // The viewer-open state is just an id reference — DocumentViewer
  // fetches its own URL via the callback, so we don't cache anything
  // here. Clearing on close + escape is handled inside the modal.
  const [viewing, setViewing] = useState<LoanDocument | null>(null);
  const fetchUrl = useCallback(async () => {
    if (!viewing) throw new Error("No document selected");
    return api.getDocumentDownloadUrl(loanId, viewing.id);
  }, [loanId, viewing]);

  return (
    <div className="rounded-lg border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3">
      <SectionLabel
        Icon={IconFile}
        trailing={
          docs.length > 0
            ? `${docs.length} document${docs.length === 1 ? "" : "s"}`
            : undefined
        }
      >
        Documents
      </SectionLabel>

      {docs.length === 0 ? (
        <p className="mb-2 px-1 text-[12px] text-[var(--color-text-tertiary)]">
          Upload the loan packet to get the intake agent going. PDFs and plain
          text are extracted automatically.
        </p>
      ) : (
        <div className="mb-3 divide-y-[0.5px] divide-[var(--color-border-tertiary)]">
          {docs.map((d) => (
            <DocRow key={d.id} doc={d} onOpen={setViewing} />
          ))}
        </div>
      )}

      {viewing && (
        <DocumentViewer
          fetchUrl={fetchUrl}
          filename={viewing.filename}
          onClose={() => setViewing(null)}
        />
      )}

      <Dropzone
        onFiles={onFiles}
        uploading={upload.isPending}
        acceptHint="PDF, plain text up to ~20MB each."
      />

      {uploadError && (
        <p className="mt-2 rounded bg-[var(--color-background-danger)] px-3 py-2 text-[11px] text-[var(--color-text-danger)]">
          {uploadError}
        </p>
      )}
    </div>
  );
}
