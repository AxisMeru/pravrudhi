import nextCoreWebVitals from "eslint-config-next/core-web-vitals";
import nextTypescript from "eslint-config-next/typescript";

const eslintConfig = [
  ...nextCoreWebVitals,
  ...nextTypescript,
  {
    ignores: [".next/**", "out/**", "node_modules/**"],
  },
  {
    // A leading underscore is this codebase's own marker for "unused on purpose" (e.g. a stand-in function that
    // must match another branch's signature) — recognized here rather than disabled outright.
    rules: {
      "@typescript-eslint/no-unused-vars": [
        "warn",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_", caughtErrorsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // .cjs names a file as CommonJS on purpose (record-walkthrough.cjs is a standalone script run directly by
    // node, not part of the Next.js build), so require() there is correct, not a lint violation.
    files: ["**/*.cjs"],
    rules: {
      "@typescript-eslint/no-require-imports": "off",
    },
  },
];

export default eslintConfig;
