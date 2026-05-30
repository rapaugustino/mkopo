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
 *   - **cookie name** — borrower side carries ``mkopo_session``,
 *     staff side carries ``mkopo_staff_session``. Same JWT signing
 *     key, different audience. Both ride on ``credentials: include``.
 *
 * Everything else is identical: same event protocol, same client-
 * owned history (the server is stateless across turns + across
 * confirmation interrupts). Putting the streamer in one place
 * means a protocol change lands in two files (the routers) not in
 * three (routers + two readers).
 */
const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
// Both surfaces are cookie-based now. Borrower side: mkopo_session
// (set by /borrower-auth/login). Staff side: mkopo_staff_session
// (set by /staff/auth/login). Always include credentials so the
// browser carries whichever cookie applies.

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
  /** When true, the tool is irreversible (terminal stage transition,
   *  account erasure) and the client MUST mint a fresh password
   *  challenge token via ``/borrower-auth/me/challenge`` and include
   *  it on the ``ToolResume`` payload — same threat model as the REST
   *  endpoints' ``_require_challenge`` gate (#169). Default false. */
  requires_reauth?: boolean;
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
  /** One-shot password-challenge token. Required by the backend
   *  when the original ``confirm_required`` event had
   *  ``requires_reauth: true``. Minted by
   *  ``borrowerAuthApi.mintChallenge(password)``. */
  challenge_token?: string;
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
  /** Kept on the signature for back-compat with existing call sites
   *  (borrower vs staff). Both surfaces are cookie-based now —
   *  whichever cookie the browser holds is sent automatically via
   *  ``credentials: include``. The string here no longer drives any
   *  behaviour. Safe to drop on next call-site sweep. */
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

  const init: RequestInit = {
    method: "POST",
    credentials: "include",
    headers,
    body: JSON.stringify(body),
  };
  // ``auth`` is kept on the type signature for back-compat with
  // existing call sites (borrower vs staff), but both surfaces are
  // cookie-based now so the only thing it determines is which
  // cookie name the browser ends up sending — the request shape
  // is identical.

  const res = await fetch(`${API_URL}/api/v1${args.path}`, init);
  if (res.status === 401 && typeof window !== "undefined") {
    // Session expired — bounce. Borrower-portal pages stay on
    // /login (the borrower login flow lives there); staff pages
    // go to /staff/login.
    const onBorrowerSurface =
      window.location.pathname.startsWith("/account") ||
      window.location.pathname.startsWith("/apply") ||
      window.location.pathname === "/login" ||
      window.location.pathname.startsWith("/signup");
    const target = onBorrowerSurface ? "/login" : "/staff/login";
    const next = encodeURIComponent(
      window.location.pathname + window.location.search,
    );
    window.location.href = `${target}?next=${next}`;
    throw new Error("Not authenticated");
  }
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
        requires_reauth: Boolean(data.requires_reauth),
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
