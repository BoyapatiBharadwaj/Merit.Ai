/**
 * ESLint, added because its absence had already cost something concrete.
 *
 * Splitting the 2,080-line ExaminerDashboard into modules left four identifiers
 * used but never imported -- `EmptyState` in two files, `useCallback` and two
 * style constants in a third. `vite build` reported success every time, because
 * a bundler resolves module imports and does not care about undefined
 * identifiers inside JSX. They would have been runtime crashes: a blank exam
 * builder the moment a section list was empty.
 *
 * `no-undef` catches exactly that class of mistake in the time it takes to run.
 */
import js from "@eslint/js";
import globals from "globals";
import react from "eslint-plugin-react";
import reactHooks from "eslint-plugin-react-hooks";

export default [
  { ignores: ["dist/**", "node_modules/**", "public/vendor/**"] },
  {
    files: ["src/**/*.{js,jsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: { ...globals.browser, ...globals.es2021 },
      parserOptions: { ecmaFeatures: { jsx: true } },
    },
    settings: { react: { version: "detect" } },
    plugins: { react, "react-hooks": reactHooks },
    rules: {
      ...js.configs.recommended.rules,
      ...react.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,

      // The JSX transform means React need not be in scope, and prop-types is
      // not the validation strategy here -- the API contract is.
      "react/react-in-jsx-scope": "off",
      "react/prop-types": "off",

      // The rule that would have caught the four missing imports.
      "no-undef": "error",
      // Unused variables are usually a half-finished edit. Warn rather than
      // error so a work-in-progress does not fail the build, and allow the
      // `_ignored` convention for deliberately-unused arguments.
      "no-unused-vars": ["warn", { argsIgnorePattern: "^_", varsIgnorePattern: "^_" }],
      // Catches an effect reading state it does not list -- the shape of the
      // stale-closure bug that made flushCodeSave silently discard code.
      "react-hooks/exhaustive-deps": "warn",
    },
  },
];
