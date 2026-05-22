"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ReactQueryDevtools } from "@tanstack/react-query-devtools";
import { Toaster } from "sonner";

/**
 * App-wide TanStack Query provider. The QueryClient is created lazily in
 * useState so it's stable across renders — important because Next.js dev mode
 * can re-execute the surrounding component during HMR.
 */
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            staleTime: 30_000,
            refetchOnWindowFocus: false,
            retry: 1,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>
      {children}
      {/* Toasts are routed through sonner. We render the Toaster once at
          the app root so any mutation, hook, or component can call
          `toast.success(...)` / `toast.error(...)` and get a consistent
          visual treatment. The custom CSS-variable mapping below keeps
          toasts on the same warm palette as the rest of the UI rather
          than sonner's default white-on-dark. */}
      <Toaster
        position="bottom-right"
        theme="light"
        toastOptions={{
          style: {
            background: "var(--color-background-primary)",
            color: "var(--color-text-primary)",
            border: "0.5px solid var(--color-border-tertiary)",
            fontSize: "12.5px",
          },
        }}
      />
      <ReactQueryDevtools initialIsOpen={false} buttonPosition="bottom-right" />
    </QueryClientProvider>
  );
}
