import type { Metadata } from "next";
import { Inter, JetBrains_Mono, Source_Serif_4 } from "next/font/google";
import "./globals.css";
import { GlobalNav } from "./components/GlobalNav";
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
    >
      <body>
        <Providers>
          {/* Brand bar. The 1px brand-light strip below the nav is the
              app's quietest brand cue: visible only when you look for
              it, but consistent on every screen — that's the kind of
              detail that separates a real product from a vibe. */}
          <nav
            className="border-b-[0.5px] border-[var(--color-border-tertiary)] bg-[var(--color-background-primary)] px-4 py-3"
            style={{
              boxShadow: "inset 0 -1px 0 var(--color-brand-light)",
            }}
          >
            <div className="mx-auto flex max-w-7xl items-center gap-6">
              <div className="flex items-center gap-3">
                <div
                  className="flex h-8 w-8 items-center justify-center rounded-md text-[13px] font-semibold"
                  style={{
                    background: "var(--color-brand)",
                    color: "var(--color-brand-light)",
                    letterSpacing: "-0.04em",
                  }}
                >
                  MK
                </div>
                <div className="flex items-baseline gap-2">
                  <span className="brand-wordmark text-[15px] font-medium">
                    Mkopo Lens
                  </span>
                  <span className="text-[11px] text-[var(--color-text-tertiary)]">
                    AI-first origination
                  </span>
                </div>
              </div>
              <GlobalNav />
            </div>
          </nav>
          <main className="mx-auto max-w-7xl px-4 py-6">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
