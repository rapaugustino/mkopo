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
  /** Per-step status:
   *  - "pending" — predecessor hasn't completed yet
   *  - "active"  — node is currently running
   *  - "done"    — node_complete event landed
   *  - "skipped" — bypassed at run-end (e.g. intake's `approve`/`send`
   *                when the packet is already complete)
   *  - "failed"  — the run errored while this node was active.
   *                The spinner stops and the icon flips to a red X.
   */
  status: "pending" | "active" | "done" | "skipped" | "failed";
  summary?: string;
  /** Wall-clock when this node transitioned to "active". Used to derive
   *  elapsedMs on completion. Set by the reducer when its predecessor
   *  completes; null for never-active nodes. */
  startedAt?: number;
  /** Milliseconds the node ran. Set when ``status`` flips to "done" or
   *  "failed" so the UI can show how long the work took before it died. */
  elapsedMs?: number;
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
  /** Plain user-facing summary of why the run failed, when it failed.
   *  The deeper technical text (stack trace gist, raw API message)
   *  lives in ``errorDetail`` so the UI can collapse it. */
  error: string | null;
  errorDetail: string | null;
  /** Set when the run short-circuited at a pre-flight gate (e.g. no
   *  documents). Distinct from ``error`` because it's not a failure —
   *  it's the system politely declining to spend tokens. */
  skipReason: string | null;
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
    errorDetail: null,
    skipReason: null,
    threadId: null,
    lastResult: null,
  });

  const run = useCallback(async ({ path, body, onInterrupt, onDone }: RunArgs) => {
    setState({
      isRunning: true,
      nodes: [],
      error: null,
      errorDetail: null,
      skipReason: null,
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
        errorDetail: null,
        skipReason: null,
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
    case "started": {
      const now = Date.now();
      // Paint the full checkmark trail up front so the user sees what
      // the agent intends to do, not a growing list of mystery nodes.
      // The first node gets its ``startedAt`` immediately; the rest
      // get theirs as their predecessors complete (see node_complete).
      setState((s) => ({
        ...s,
        threadId: ev.thread_id,
        nodes: ev.nodes.map((n, i) => ({
          ...n,
          status: i === 0 ? "active" : "pending",
          startedAt: i === 0 ? now : undefined,
        })),
      }));
      return;
    }
    case "node_complete": {
      const now = Date.now();
      setState((s) => {
        const idx = s.nodes.findIndex((n) => n.key === ev.node);
        if (idx === -1) return s;
        const nodes = s.nodes.map((n, i) => {
          if (i < idx)
            return n.status === "pending" ? { ...n, status: "skipped" as const } : n;
          if (i === idx) {
            return {
              ...n,
              status: "done" as const,
              summary: ev.summary,
              elapsedMs: n.startedAt ? now - n.startedAt : undefined,
            };
          }
          // The node immediately after the just-completed one becomes
          // active and gets its start timestamp; further-out nodes
          // stay pending until their own predecessor lands.
          if (i === idx + 1) {
            return { ...n, status: "active" as const, startedAt: now };
          }
          return n;
        });
        return { ...s, nodes };
      });
      return;
    }
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
    case "skipped":
      // Pre-flight gate fired. Mark the currently-active node as
      // skipped (it didn't really run; it just decided not to) and
      // park the friendly reason on state for the UI banner.
      setState((s) => ({
        ...s,
        isRunning: false,
        skipReason: ev.reason,
        nodes: s.nodes.map((n) =>
          n.status === "active" || n.status === "pending"
            ? { ...n, status: "skipped" as const }
            : n,
        ),
      }));
      return;
    case "done":
      setState((s) => ({
        ...s,
        isRunning: false,
        lastResult: ev,
        // The "done" payload may carry a skip_reason that the
        // upstream "skipped" event already stashed. If our state
        // doesn't have one yet (e.g. a backend without the skipped
        // event), pick it up from done too.
        skipReason: s.skipReason ?? ev.skip_reason ?? null,
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
    case "error": {
      const now = Date.now();
      // An error event from the stream means the graph crashed mid-run.
      // Whichever node was active when that happened needs to flip to
      // "failed" — otherwise the spinner keeps spinning on a node that
      // will never complete. Subsequent pending nodes become "skipped"
      // because the run is over.
      //
      // Prefer the backend's structured ``node`` attribution when it
      // arrived; fall back to "whichever node was active when the
      // error landed". The first is more reliable when the LangGraph
      // exception note is available; the second covers older errors.
      setState((s) => {
        // Resolve the failing node from the freshest state — the
        // backend's ``node`` field is authoritative when present, with
        // a fallback to "whichever step was active when the error
        // landed."
        const failingNode =
          ev.node ?? s.nodes.find((n) => n.status === "active")?.key ?? null;
        return {
          ...s,
          isRunning: false,
          error: ev.reason ?? ev.message,
          errorDetail: ev.detail ?? null,
          nodes: s.nodes.map((n) => {
            const isFailingNode =
              failingNode != null
                ? n.key === failingNode
                : n.status === "active";
            if (isFailingNode) {
              return {
                ...n,
                status: "failed" as const,
                elapsedMs: n.startedAt ? now - n.startedAt : undefined,
                summary: ev.reason ?? ev.message,
              };
            }
            if (n.status === "pending" || n.status === "active") {
              return { ...n, status: "skipped" as const };
            }
            return n;
          }),
        };
      });
      return;
    }
  }
}
