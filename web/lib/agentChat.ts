/**
 * Shared SSE streaming primitive for the agent chat surfaces.
 *
 * Both the borrower-facing chat (Phase 3) and the staff-facing
 * chat (Phase 4) speak the same on-the-wire protocol — same event
 * names, same payload shapes, same confirmation-interrupt dance.
 * They differ only in:
 *
 *   - **endpoint URL** — ``/borrower-auth/me/chat/stream`` vs
 *     ``/staff/chat/stream``
 *   - **auth mode**    — borrower side rides on the session
 *     cookie (``credentials: include``); staff side rides on the
 *     bearer-token from ``NEXT_PUBLIC_DEV_TOKEN`` until real auth
 *     replaces it.
 *
 * Everything else is identical: same event protocol, same client-
 * owned history (the server is stateless across turns + across
 * confirmation interrupts). Putting the streamer in one place
 * means a protocol change lands in two files (the routers) not in
 * three (routers + two readers).
 */
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const DEV_TOKEN = process.env.NEXT_PUBLIC_DEV_TOKEN || "dev-token-replace-me";

// ---- event shapes --------------------------------------------------------

export interface ToolCallEvent {
  type: "tool_call";
  id: string;
  name: string;
  args: Record<string, unknown>;
  human_action: string;
}

export interface ToolResultEvent {
  type: "tool_result";
  id: string;
  ok: boolean;
  result?: unknown;
  error?: string;
}

export interface MessageEvent {
  type: "message";
  role: "assistant";
  text: string;
}

export interface ThinkingEvent {
  type: "thinking";
}

export interface ConfirmRequiredEvent {
  type: "confirm_required";
  id: string;
  name: string;
  args: Record<string, unknown>;
  human_action: string;
  summary: string;
  /** The conversation history up to and including the assistant turn
   *  that proposed the destructive tool call. */
  messages: ChatMessage[];
}

export interface DoneEvent {
  type: "done";
  messages: ChatMessage[];
}

export interface ErrorEvent {
  type: "error";
  reason: string;
  detail?: string;
}

export type ChatEvent =
  | ThinkingEvent
  | MessageEvent
  | ToolCallEvent
  | ToolResultEvent
  | ConfirmRequiredEvent
  | DoneEvent
  | ErrorEvent;

/** Anthropic-shaped chat history block. Kept opaque on the client. */
export type ChatMessage = {
  role: "user" | "assistant";
  content: string | unknown[];
};

export interface ToolResume {
  tool_use_id: string;
  name: string;
  input: Record<string, unknown>;
  action: "confirm" | "cancel";
}

// ---- args ---------------------------------------------------------------

interface BaseArgs {
  messages: ChatMessage[];
  userMessage?: string;
  toolResume?: ToolResume;
  /** Surface-specific extras: ``{loan_id: …}`` for the borrower
   *  endpoint; ``{loan_id: …}`` for staff too. Whatever the route
   *  expects in its request body. */
  extras?: Record<string, unknown>;
}

export interface StreamArgs extends BaseArgs {
  /** API path (without the ``/api/v1`` prefix). */
  path: string;
  /** ``"cookie"`` rides ``credentials: include``; ``"bearer"`` adds
   *  ``Authorization: Bearer NEXT_PUBLIC_DEV_TOKEN``. */
  auth: "cookie" | "bearer";
}

// ---- the streamer -------------------------------------------------------

/**
 * Open a POST to an agent-chat SSE endpoint, parse frames, yield events.
 *
 * Used by both ``streamBorrowerChat`` and ``streamStaffChat`` —
 * those are thin wrappers that pre-set the path + auth mode.
 */
export async function* streamAgentChat(
  args: StreamArgs,
): AsyncGenerator<ChatEvent, void, unknown> {
  const body: Record<string, unknown> = {
    ...(args.extras ?? {}),
    messages: args.messages,
  };
  if (args.userMessage) body.user_message = args.userMessage;
  if (args.toolResume) body.tool_resume = args.toolResume;

  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    Accept: "text/event-stream",
  };
  if (args.auth === "bearer") {
    headers.Authorization = `Bearer ${DEV_TOKEN}`;
  }

  const init: RequestInit = {
    method: "POST",
    headers,
    body: JSON.stringify(body),
  };
  if (args.auth === "cookie") {
    init.credentials = "include";
  }

  const res = await fetch(`${API_URL}/api/v1${args.path}`, init);
  if (!res.ok || !res.body) {
    throw new Error(`Chat stream ${res.status}: ${await res.text()}`);
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    if (done) {
      const remainder = buffer.trim();
      if (remainder) {
        const ev = parseFrame(remainder);
        if (ev) yield ev;
      }
      return;
    }
    buffer += decoder.decode(value, { stream: true });

    let sep: number;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      const ev = parseFrame(frame);
      if (ev) yield ev;
    }
  }
}

function parseFrame(frame: string): ChatEvent | null {
  let eventName = "message";
  const dataLines: string[] = [];
  for (const line of frame.split("\n")) {
    if (line.startsWith("event:")) {
      eventName = line.slice("event:".length).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trim());
    }
  }
  if (dataLines.length === 0) return null;
  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
  }
  const data = (payload || {}) as Record<string, unknown>;
  switch (eventName) {
    case "thinking":
      return { type: "thinking" };
    case "message":
      return {
        type: "message",
        role: "assistant",
        text: String(data.text ?? ""),
      };
    case "tool_call":
      return {
        type: "tool_call",
        id: String(data.id ?? ""),
        name: String(data.name ?? ""),
        args: (data.args as Record<string, unknown>) ?? {},
        human_action: String(data.human_action ?? ""),
      };
    case "tool_result":
      return {
        type: "tool_result",
        id: String(data.id ?? ""),
        ok: Boolean(data.ok),
        result: data.result,
        error: data.error ? String(data.error) : undefined,
      };
    case "confirm_required":
      return {
        type: "confirm_required",
        id: String(data.id ?? ""),
        name: String(data.name ?? ""),
        args: (data.args as Record<string, unknown>) ?? {},
        human_action: String(data.human_action ?? ""),
        summary: String(data.summary ?? ""),
        messages: (data.messages as ChatMessage[]) ?? [],
      };
    case "done":
      return {
        type: "done",
        messages: (data.messages as ChatMessage[]) ?? [],
      };
    case "error":
      return {
        type: "error",
        reason: String(data.reason ?? "Unknown error"),
        detail: data.detail ? String(data.detail) : undefined,
      };
    default:
      return null;
  }
}
