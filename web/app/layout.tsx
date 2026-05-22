import type { Metadata } from "next";
import { Inter, JetBrains_Mono, Source_Serif_4 } from "next/font/google";
import "./globals.css";
import { AppShell } from "./components/AppShell";
import { Providers } from "./providers";

/**
 * The type stack is deliberate.
 *
 * - **Inter** for UI — a workhorse sans with strong tabular numerals,
 *   the right vertical metrics for dense dashboards, and OpenType
 *   features (ss01, cv01) that line up the digits in our money
 *   columns. Variable, so we pay one network round-trip and get every
 *   weight.
 * - **Source Serif 4** for "moments of authority" — the AI verdict
 *   text, the adverse-action letter body, quote blocks in the case
 *   file. We want those to read as if a person signed them.
 * - **JetBrains Mono** for any code-flavoured surface — currently the
 *   intake email body composer. Used sparingly.
 *
 * Each is loaded via `next/font` with `display: "swap"` so the page
 * paints immediately and the custom face replaces system fallback when
 * it lands. The CSS variables here feed straight into the `--font-*`
 * stacks declared in `globals.css`.
 */
const inter = Inter({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-inter",
});

const sourceSerif = Source_Serif_4({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-source-serif",
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  display: "swap",
  variable: "--font-jetbrains-mono",
});

export const metadata: Metadata = {
  title: "Mkopo Lens",
  description: "AI-first loan origination for private lenders",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html
      lang="en"
      className={`${inter.variable} ${sourceSerif.variable} ${jetbrains.variable}`}
      suppressHydrationWarning
    >
      {/* `suppressHydrationWarning` on <body> is the canonical fix for
          browser extensions (Grammarly, LanguageTool, Honey, Dashlane,
          1Password) that inject attributes like `data-new-gr-c-s-check-loaded`
          before React hydrates. The suppression is scoped to *this
          element's own attributes* — children still hydrate and validate
          normally. See https://react.dev/link/hydration-mismatch */}
      <body suppressHydrationWarning>
        <Providers>
          {/* AppShell renders the internal navigation chrome on all
              routes except the borrower portal (/apply/*), which has
              its own minimal layout in app/apply/layout.tsx. */}
          <AppShell>{children}</AppShell>
        </Providers>
      </body>
    </html>
  );
}
