import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

// Flat config for the Vite + React + TS viewer. Type-aware linting stays off (no project service)
// to keep `npm run lint` fast; tsc -b already does the type checking. This catches the lint-class
// issues tsc misses — unused vars, hook deps, bad refresh boundaries.
export default tseslint.config(
  { ignores: ["dist", "dist-review", "node_modules"] },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      // The two battle-tested hook rules. NOT the full recommended set — its newer React-Compiler
      // rules (set-state-in-effect, etc.) error on this codebase's intentional reset-on-change
      // effects; adopting those would be a separate refactor, not lint hygiene.
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "react-refresh/only-export-components": ["warn", { allowConstantExport: true }],
      // TS handles unused-var errors; allow leading-underscore intentional discards.
      "@typescript-eslint/no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
    },
  },
);
