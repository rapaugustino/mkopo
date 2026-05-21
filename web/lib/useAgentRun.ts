"use client";

import { useCallback, useState } from "react";
import {
  streamAgent,
  type AgentEvent,
  type DoneEvent,
  type InterruptEvent,
} from "./agentStream";

export interface AgentNode {
  key: string;
  label: string;
  /** "pending" until the prior node completes; "active" while running;
   *  "done" once the matching node_complete event arrives. */
  status: "pending" | "active" | "done" | "skipped";
  summary?: string;
}

interface RunArgs {
  path: string;
  body?: unknown;
  onInterrupt?: (payload: InterruptEvent["payload"]) => void;
  onDone?: (ev: DoneEvent) => void;
}

interface RunState {
  isRunning: boolean;
  nodes: AgentNode[];
  error: string | null;
  threadId: string | null;
  /** Last-emitted "done" payload from the stream — handy when the
   *  caller wants the result without setting up an onDone callback. */
  lastResult: DoneEvent | null;
}

/**
 * Hook that runs an agent via SSE and tracks node progress.
 *
 * The reducer is small enough that we inline it — three event kinds
 * (started, node_complete, done/error) update three fields. The
 * frontend reads ``nodes`` to paint the checkmark trail; the next node
 * after the most-recent-completed one is the implicit "active" step.
 *
 * Two callbacks bypass React state because they're side effects:
 * ``onInterrupt`` opens the approval modal (which has its own state
 * and needs the draft payload synchronously), and ``onDone`` is where
 * the loan detail page invalidates queries to refresh the audit log.
 */
export function useAgentRun() {
  const [state, setState] = useState<RunState>({
    isRunning: false,
    nodes: [],
    error: null,
    threadId: null,
    lastResult: null,
  });

  const run = useCallback(async ({ path, body, onInterrupt, onDone }: RunArgs) => {
    setState({
      isRunning: true,
      nodes: [],
      error: null,
      threadId: null,
      lastResult: null,
    });

    try {
      for await (const ev of streamAgent(path, body)) {
        applyEvent(ev, setState, { onInterrupt, onDone });
      }
    } catch (e) {
      // Network-level failure (CORS, refused, etc.) — distinct from a
      // graph error, which the backend would serialise as an "error"
      // SSE event before closing.
      setState((s) => ({
        ...s,
        isRunning: false,
        error: e instanceof Error ? e.message : String(e),
      }));
    }
  }, []);

  const reset = useCallback(
    () =>
      setState({
        isRunning: false,
        nodes: [],
        error: null,
        threadId: null,
        lastResult: null,
      }),
    [],
  );

  return { ...state, run, reset };
}

function applyEvent(
  ev: AgentEvent,
  setState: React.Dispatch<React.SetStateAction<RunState>>,
  callbacks: {
    onInterrupt?: (payload: InterruptEvent["payload"]) => void;
    onDone?: (ev: DoneEvent) => void;
  },
) {
  switch (ev.type) {
    case "started":
      // Paint the full checkmark trail up front so the user sees what
      // the agent intends to do, not a growing list of mystery nodes.
      setState((s) => ({
        ...s,
        threadId: ev.thread_id,
        nodes: ev.nodes.map((n, i) => ({
          ...n,
          status: i === 0 ? "active" : "pending",
        })),
      }));
      return;
    case "node_complete":
      setState((s) => {
        const idx = s.nodes.findIndex((n) => n.key === ev.node);
        if (idx === -1) return s;
        const nodes = s.nodes.map((n, i) => {
          if (i < idx) return n.status === "pending" ? { ...n, status: "skipped" as const } : n;
          if (i === idx)
            return { ...n, status: "done" as const, summary: ev.summary };
          // The node immediately after the just-completed one becomes
          // active; further-out nodes stay pending.
          if (i === idx + 1) return { ...n, status: "active" as const };
          return n;
        });
        return { ...s, nodes };
      });
      return;
    case "interrupt":
      setState((s) => ({
        ...s,
        // Mark all remaining nodes as pending — the run is paused, not
        // finished. The "active" lamp on the current node stays on so
        // the user sees that work is suspended pending their input.
        isRunning: false,
      }));
      callbacks.onInterrupt?.(ev.payload);
      return;
    case "done":
      setState((s) => ({
        ...s,
        isRunning: false,
        lastResult: ev,
        // Any node still marked "pending" at the end was bypassed
        // (e.g. intake's `approve`/`send` when the packet is complete).
        nodes: s.nodes.map((n) =>
          n.status === "pending" || n.status === "active"
            ? { ...n, status: "skipped" as const }
            : n,
        ),
      }));
      callbacks.onDone?.(ev);
      return;
    case "error":
      setState((s) => ({ ...s, isRunning: false, error: ev.message }));
      return;
  }
}
