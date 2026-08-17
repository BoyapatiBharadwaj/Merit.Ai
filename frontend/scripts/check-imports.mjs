/**
 * Finds identifiers used but never imported or defined, with no dependencies.
 */
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOT = "src";

// Helpers that travel between modules in this codebase. JSX element names are
// detected structurally; these are not, so they are listed.
const SHARED = [
  "useState", "useEffect", "useRef", "useCallback", "useMemo", "useContext",
  "useNavigate", "useParams", "useSearchParams", "useLocation",
  "Api", "ApiError", "setAttemptToken", "createAutosaveQueue", "SaveState",
  "btnPrimary", "btnGhost", "btnPrimaryOnDark", "btnGhostOnDark",
  "fieldInput", "fieldLabel", "fieldInputCompact", "fieldLabelCompact", "sectionEyebrow",
  "isLoggedIn", "getRole", "getName", "getToken", "setSession", "clearSession",
];

function walk(dir) {
  return readdirSync(dir).flatMap((entry) => {
    const full = join(dir, entry);
    return statSync(full).isDirectory() ? walk(full) : full.endsWith(".jsx") || full.endsWith(".js") ? [full] : [];
  });
}

let failures = 0;
for (const file of walk(ROOT)) {
  // Comments are stripped first.
  const source = readFileSync(file, "utf8")
    .replace(/\/\*[\s\S]*?\*\//g, "")
    .replace(/^\s*\/\/.*$/gm, "");
  // Remove the import statements from the body rather than slicing at an offset.
  const importStatements = source.match(/^import[^;]*?;$/gm) || [];
  const body = source.replace(/^import[^;]*?;$/gm, "");

  const declared = new Set([
    // `export default function Foo`, `export function Foo`, `function Foo`
    ...[...source.matchAll(/(?:export\s+(?:default\s+)?)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)/g)].map((m) => m[1]),
    // Any const/let/var binding, at any indentation -- component-local helpers
    // are declared inside a function body, not at column zero.
    ...[...source.matchAll(/(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=/g)].map((m) => m[1]),
    // Destructured bindings: const { a, b } = ... and function ({ a, b })
    ...[...source.matchAll(/[{,]\s*([A-Za-z_$][\w$]*)\s*[,}=:]/g)].map((m) => m[1]),
    ...importStatements.join("\n").matchAll(/[A-Za-z_$][\w$]*/g),
  ].map((v) => (typeof v === "string" ? v : v[0])));

  const missing = new Set();
  for (const [, name] of body.matchAll(/<([A-Z][A-Za-z0-9_]*)/g)) {
    if (!declared.has(name)) missing.add(name);
  }
  for (const name of SHARED) {
    if (new RegExp(`\\b${name}\\b`).test(body) && !declared.has(name)) missing.add(name);
  }

  if (missing.size) {
    failures += 1;
    console.error(`${file}: used but not imported or defined -> ${[...missing].sort().join(", ")}`);
  }
}

if (failures) {
  console.error(`\n${failures} file(s) with undefined identifiers. These build cleanly and crash at runtime.`);
  process.exit(1);
}
console.log("check-imports: no undefined identifiers.");
