/**
 * SSE reader for `/loans/{id}/agents/{name}/run` endpoints.
 *
 * Why a hand-rolled reader instead of the browser's `EventSource`:
 * EventSource is GET-only and authentication-header-hostile (you can't
 * set `Authorization`). Our agent runs are POSTs with a bearer token,
 * so we use `fetch` and pull bytes off the `ReadableStream` ourselves.
 * Parsing SSE is small enough (~30 lines) that taking the dependency
 * isn't justified.
 *
 * The contract this module exposes is one async generator that yields
 * typed events. Components consume it via `useAgentRun` (in agent-run/
 * page-level hooks) — they don't poke at the wire format.
 */
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const DEV_TOKEN = process.env.NEXT_PUBLIC_DEV_TOKEN || "dev-token-replace-me";

// ---- event shapes --------------------------------------------------------

/** Emitted once at the start of every run. ``nodes`` is the ordered
 *  list of LangGraph nodes the agent will execute, so the UI can paint
 *  the full checkmark trail before any node completes. */
export interface StartedEvent {
  type: "started";
  thread_id: string;
  nodes: { key: string; label: string }[];
}

/** One per node, in execution order. `summary` is a short human blurb
 *  the streaming helper derives from the node's state delta. */
export interface NodeCompleteEvent {
  type: "node_complete";
  node: string;
  label: string;
  summary: string;
}

/** Fired when the graph pauses on `interrupt()`. The payload mirrors
 *  the LangGraph interrupt value — for intake that's
 *  `{type: "approve_email", draft: {...}, missing_fields: [...]}`. */
export interface InterruptEvent {
  type: "interrupt";
  payload: Record<string, unknown>;
}

/** Final event of every run. `result` carries whatever the synchronous
 *  endpoint used to return so existing call sites keep working.
 *  ``skip_reason`` is set when the run short-circuited at a pre-flight
 *  gate (e.g. no documents uploaded yet). */
export interface DoneEvent {
  type: "done";
  thread_id: string;
  status: string;
  interrupt: Record<string, unknown> | null;
  result: unknown;
  skip_reason?: string | null;
}

/** Emitted right before ``done`` when the run short-circuited at a
 *  pre-flight gate. Carries the friendly reason the frontend should
 *  render in place of a generic completion summary. */
export interface SkippedEvent {
  type: "skipped";
  status: string;
  reason: string;
}

export interface ErrorEvent {
  type: "error";
  /** Backwards-compatible single-line message. */
  message: string;
  /** New richer fields — backend can now attribute the failure to a
   *  specific node and split the user-facing reason from the gory
   *  technical detail. */
  node?: string | null;
  reason?: string;
  detail?: string;
}

export type AgentEvent =
  | StartedEvent
  | NodeCompleteEvent
  | InterruptEvent
  | DoneEvent
  | SkippedEvent
  | ErrorEvent;

// ---- SSE parser ---------------------------------------------------------

/**
 * Walk a single SSE frame ("event: ...\ndata: ...\n\n") into a typed
 * AgentEvent. SSE frames are double-newline-delimited; the caller
 * passes us one frame at a time.
 */
function parseFrame(frame: string): AgentEvent | null {
  let eventName = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
    // Other SSE fields (id:, retry:, comments) — ignored, we don't use them.
  }
  if (dataLines.length === 0) return null;
  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
  }
  switch (eventName) {
    case "started":
      return { type: "started", ...(payload as Omit<StartedEvent, "type">) };
    case "node_complete":
      return {
        type: "node_complete",
        ...(payload as Omit<NodeCompleteEvent, "type">),
      };
    case "interrupt":
      return { type: "interrupt", payload: payload as Record<string, unknown> };
    case "done":
      return { type: "done", ...(payload as Omit<DoneEvent, "type">) };
    case "skipped":
      return { type: "skipped", ...(payload as Omit<SkippedEvent, "type">) };
    case "error": {
      const data = payload as {
        message?: string;
        reason?: string;
        detail?: string;
        node?: string | null;
      };
      return {
        type: "error",
        // Prefer the structured reason; fall back to message for
        // backwards compatibility with older backend builds.
        message: data.reason ?? data.message ?? "unknown error",
        reason: data.reason,
        detail: data.detail,
        node: data.node ?? null,
      };
    }
    default:
      return null;
  }
}

/**
 * Open an authenticated POST to an SSE endpoint and yield typed
 * events. The generator completes when the stream closes (the
 * backend's `done` or `error` is the last frame).
 *
 *     for await (const ev of streamAgent("/loans/abc/agents/intake/run")) {
 *       switch (ev.type) { … }
 *     }
 */
export async function* streamAgent(
  path: string,
  body?: unknown,
): AsyncGenerator<AgentEvent, void, unknown> {
  const res = await fetch(`${API_URL}/api/v1${path}`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${DEV_TOKEN}`,
      "Content-Type": "application/json",
      Accept: "text/event-stream",
    },
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok || !res.body) {
    throw new Error(`Agent stream ${res.status}: ${await res.text()}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      // Drain anything still buffered (servers occasionally flush the
      // last frame without a trailing blank line).
      const remainder = buffer.trim();
      if (remainder) {
        const ev = parseFrame(remainder);
        if (ev) yield ev;
      }
      return;
    }
    buffer += decoder.decode(value, { stream: true });

    // SSE frames are separated by a blank line ("\n\n"). Pull complete
    // frames off the buffer one at a time.
    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const ev = parseFrame(frame);
      if (ev) yield ev;
    }
  }
}
