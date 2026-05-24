import type { Metadata } from "next";

import { BorrowerShell } from "@/app/components/BorrowerShell";

export const metadata: Metadata = {
  title: "Apply — Mkopo Lens",
  description: "Apply for a loan.",
};

/**
 * Layout for the application wizard + status pages.
 *
 * Delegates chrome to :func:`BorrowerShell` so every borrower-facing
 * route (``/apply``, ``/account``, ``/auth``) renders the same brand
 * bar and auth chip without each route reimplementing it.
 */
export default function ApplyLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return <BorrowerShell>{children}</BorrowerShell>;
}
