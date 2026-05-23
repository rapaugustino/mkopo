/**
 * Borrower-side chat stream. Thin wrapper over the shared
 * agent-chat primitive — cookie-authed, scoped to a specific loan.
 *
 * Kept as a separate module so the borrower call-site can stay
 * tight (no need to remember which auth mode to pass each time).
 * Re-exports the shared event types so existing callers don't
 * have to update imports.
 */
import {
  streamAgentChat,
  type ChatEvent,
  type ChatMessage,
  type ConfirmRequiredEvent,
  type DoneEvent,
  type ErrorEvent,
  type MessageEvent,
  type ThinkingEvent,
  type ToolCallEvent,
  type ToolResultEvent,
  type ToolResume,
} from "@/lib/agentChat";

export type {
  ChatEvent,
  ChatMessage,
  ConfirmRequiredEvent,
  DoneEvent,
  ErrorEvent,
  MessageEvent,
  ThinkingEvent,
  ToolCallEvent,
  ToolResultEvent,
  ToolResume,
};

export interface ChatStreamArgs {
  loanId: string;
  messages: ChatMessage[];
  userMessage?: string;
  toolResume?: ToolResume;
}

/**
 * Borrower-chat streamer. Always cookie-authed; loan_id is required
 * because the borrower-side endpoint scopes every tool call to the
 * loan in question.
 */
export async function* streamChat(
  args: ChatStreamArgs,
): AsyncGenerator<ChatEvent, void, unknown> {
  yield* streamAgentChat({
    path: "/borrower-auth/me/chat/stream",
    auth: "cookie",
    extras: { loan_id: args.loanId },
    messages: args.messages,
    userMessage: args.userMessage,
    toolResume: args.toolResume,
  });
}
