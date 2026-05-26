"use client";

/**
 * Logged-in staff user pill in the brand bar.
 *
 * Renders avatar + initials + name; on click reveals a small popup
 * with Sign out. Cookie auth means "sign out" is one POST to
 * /staff/auth/logout + a redirect to the staff login page; the
 * server-side JTI revocation makes it real (not just a cookie
 * clear) so a leaked token stops working immediately.
 *
 * Hidden entirely when the user query is loading or unauthenticated
 * — the AppShell mounts this on every staff page, but pages that
 * legitimately render anonymously (e.g. observability page during
 * the session-expired bounce) don't get a half-rendered menu.
 *
 * On 401 the API helper itself bounces to /staff/login; this
 * component does NOT need to handle that path — useQuery throws,
 * the helper redirects, the menu unmounts.
 */

import { useState, useRef, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { IconChevronDown, IconLogout, IconUser } from "@tabler/icons-react";
import { api, type StaffMe } from "@/lib/api";

function initialsOf(name: string): string {
  const parts = name.trim().split(/\s+/);
  if (!parts.length || !parts[0]) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function UserMenu() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  const meQuery = useQuery<StaffMe, Error>({
    queryKey: ["staff", "me"],
    queryFn: () => api.getStaffMe(),
    // /me is the auth-check on every page; refetch on focus so a
    // session that died in another tab is caught quickly.
    refetchOnWindowFocus: true,
    // Don't retry on 401 — the request helper handles the redirect.
    retry: false,
    staleTime: 60_000,
  });

  // Close popover on click-outside. Cheap event listener since the
  // menu is global (one per page).
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (!menuRef.current?.contains(e.target as Node)) {
        setOpen(false);
      }
    };
    window.addEventListener("mousedown", onClick);
    return () => window.removeEventListener("mousedown", onClick);
  }, [open]);

  if (meQuery.isLoading || !meQuery.data) return null;
  const user = meQuery.data;
  const initials = initialsOf(user.name || user.email);

  async function handleSignOut() {
    try {
      await api.staffLogout();
    } catch {
      // Even if logout fails server-side (Redis down, etc), the
      // cookie is server-cleared too; the next /me will 401 and
      // we'll bounce anyway. Either way clear local cache so any
      // pages that re-render see the unauthenticated state.
    }
    queryClient.clear();
    router.push("/staff/login");
  }

  return (
    <div className="relative" ref={menuRef}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex items-center gap-1.5 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2 py-1 text-[11.5px] text-[var(--color-text-primary)] hover:bg-[var(--color-background-secondary)]"
        aria-label="User menu"
        title={`${user.name || user.email} (${user.role})`}
      >
        <span
          className="flex h-5 w-5 items-center justify-center rounded-full text-[9px] font-semibold text-white"
          style={{ background: "var(--color-brand)" }}
        >
          {initials}
        </span>
        <span className="hidden sm:inline">
          {user.name || user.email}
        </span>
        <IconChevronDown size={11} />
      </button>

      {open && (
        <div
          className="absolute right-0 top-full z-30 mt-1 w-[240px] rounded-md border-[0.5px] bg-[var(--color-background-primary)] py-1 shadow-md"
          style={{ borderColor: "var(--color-border-tertiary)" }}
        >
          <div
            className="px-3 py-2 text-[11px]"
            style={{ borderBottom: "0.5px solid var(--color-border-tertiary)" }}
          >
            <p className="font-medium text-[var(--color-text-primary)]">
              {user.name}
            </p>
            <p className="text-[var(--color-text-tertiary)]">{user.email}</p>
            <p className="mt-0.5 text-[10px] uppercase tracking-[0.04em] text-[var(--color-text-tertiary)]">
              {user.role}
            </p>
          </div>
          <button
            type="button"
            onClick={handleSignOut}
            className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-[12px] text-[var(--color-text-primary)] hover:bg-[var(--color-background-secondary)]"
          >
            <IconLogout size={13} />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}

/** Standalone "Sign in" link for shells where the user is anonymous.
 *  Used when /me is unauthenticated and the page is renderable
 *  without auth (today: never — kept for future split). */
export function SignInChip() {
  return (
    <a
      href="/staff/login"
      className="flex items-center gap-1.5 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2 py-1 text-[11.5px] text-[var(--color-text-primary)] hover:bg-[var(--color-background-secondary)]"
    >
      <IconUser size={12} />
      Sign in
    </a>
  );
}
