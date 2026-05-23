// ESLint flat config.
//
// Next.js 16 removed `next lint` — `eslint-config-next` is now a
// regular shared config that you import into your own eslint
// config file. The default export is a flat-config array; spread
// it here so we get React + Next + TypeScript + a11y rules out of
// the box, then layer project-specific tweaks on top.
import nextConfig from "eslint-config-next";

const config = [
  ...nextConfig,
  {
    // Project-wide rule overrides on top of Next's defaults.
    rules: {
      // We use straight quotes and apostrophes in JSX text on purpose
      // — they render fine, and the alternative of writing
      // `&apos;` / `&quot;` everywhere hurts readability. Disable the
      // (stylistic, not correctness) rule rather than mass-escape.
      "react/no-unescaped-entities": "off",
    },
  },
  {
    // Project-wide ignores beyond the Next defaults.
    ignores: [
      ".next/**",
      "out/**",
      "build/**",
      "node_modules/**",
      "next-env.d.ts",
    ],
  },
];

export default config;
