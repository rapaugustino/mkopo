"use client";

import { useRouter } from "next/navigation";
import Link from "next/link";
import { IconLogout } from "@tabler/icons-react";

import { useAuth } from "@/app/borrower/AuthProvider";

/**
 * Right-side chip in the borrower portal header.
 *
 * Renders one of three states:
 *   - **Authed**: shows the signed-in email + a sign-out button.
 *     The email is hidden on narrow viewports so the sign-out
 *     button stays reachable.
 *   - **Anonymous**: a quiet "Sign in" link pointing at /login.
 *     Visible even on the fresh /apply page so a returning user
 *     who lost their session has a one-click way back in.
 *   - **Loading**: renders nothing (auth provider resolves the
 *     cookie on mount; we'd rather show empty than a flicker).
 *
 * Lifted out of /apply/[id]/page.tsx so it can live in the
 * borrower portal layout — that way every borrower page gets the
 * same chrome without each page rendering its own auth row.
 */
export function BorrowerHeaderChip() {
  const router = useRouter();
  const auth = useAuth();

  if (auth.status === "loading") return null;

  if (auth.status === "anonymous") {
    return (
      <Link
        href="/login"
        className="inline-flex items-center gap-1 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2.5 py-1 text-[11.5px] text-[var(--color-text-primary)] hover:bg-[var(--color-background-secondary)]"
      >
        Sign in
      </Link>
    );
  }

  return (
    <div className="flex items-center gap-2 text-[11.5px] text-[var(--color-text-secondary)]">
      <span className="hidden sm:inline truncate max-w-[220px]">
        {auth.user?.email}
      </span>
      <button
        type="button"
        onClick={() => {
          void auth.logout().then(() => router.push("/login"));
        }}
        title="Sign out"
        className="inline-flex items-center gap-1 rounded-md border-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-2 py-1 text-[11.5px] hover:bg-[var(--color-background-secondary)]"
      >
        <IconLogout size={11} />
        Sign out
      </button>
    </div>
  );
}
