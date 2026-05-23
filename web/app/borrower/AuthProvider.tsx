"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import {
  borrowerAuthApi,
  type ApiError,
  type BorrowerUser,
} from "@/lib/borrowerApi";

interface AuthState {
  /** ``"loading"`` only on the very first render while we resolve
   *  the cookie. After that it's ``"authed"`` or ``"anonymous"``. */
  status: "loading" | "authed" | "anonymous";
  user: BorrowerUser | null;
}

interface AuthContextValue extends AuthState {
  /** Re-fetch ``/me``. Use after login/signup to refresh the
   *  context immediately, or after a server-side mutation that
   *  could have changed the user (email verification, password
   *  reset, etc.). */
  refresh: () => Promise<void>;
  /** Convenience: sets the in-memory user directly. Call this
   *  after a login/signup that already returned the user, instead
   *  of round-tripping ``/me``. */
  setUser: (user: BorrowerUser | null) => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

/**
 * Borrower auth state for the React tree.
 *
 * On mount, the provider calls ``GET /borrower-auth/me``. If the
 * session cookie is valid, ``status`` flips to ``"authed"`` with
 * the user. If not (401), ``"anonymous"``. Either way it lands
 * before the first child render that branches on auth state.
 *
 * Wraps the borrower-facing route segments (``/apply``, ``/login``,
 * ``/account``, etc.). Not used on staff/internal routes — those
 * keep their bearer-token model.
 *
 * Hooks consumers should treat ``status === "loading"`` as
 * "render the loading state", NOT as "render the logged-out
 * state" — otherwise the page flashes a login redirect for a
 * tick before the cookie resolves, which is the canonical "I
 * hate Single Page Apps" experience.
 */
export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    status: "loading",
    user: null,
  });

  const refresh = useCallback(async () => {
    try {
      const user = await borrowerAuthApi.me();
      setState({ status: "authed", user });
    } catch (e) {
      const err = e as ApiError;
      if (err.status === 401) {
        setState({ status: "anonymous", user: null });
      } else {
        // Surface other errors (500, network) as anonymous — the
        // UI shouldn't lock to "loading" forever on a transient
        // failure. Real diagnostics live in the network panel.
        setState({ status: "anonymous", user: null });
      }
    }
  }, []);

  const setUser = useCallback((user: BorrowerUser | null) => {
    setState({
      status: user ? "authed" : "anonymous",
      user,
    });
  }, []);

  const logout = useCallback(async () => {
    try {
      await borrowerAuthApi.logout();
    } finally {
      setState({ status: "anonymous", user: null });
    }
  }, []);

  // Resolve cookie → user on mount. No dependencies — fires once.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  const value = useMemo<AuthContextValue>(
    () => ({
      ...state,
      refresh,
      setUser,
      logout,
    }),
    [state, refresh, setUser, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

/**
 * Hook that returns the borrower auth state + actions.
 *
 * Throws if used outside ``<AuthProvider>`` because that's almost
 * certainly a bug (a borrower page rendering without the provider).
 */
export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (ctx === null) {
    throw new Error("useAuth must be used inside <AuthProvider>");
  }
  return ctx;
}
