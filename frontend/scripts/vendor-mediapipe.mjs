/**
 * Copies MediaPipe Tasks Vision's wasm out of node_modules into public/.
 *
 * The JS half of the library is bundled by Vite (see src/lib/faceMesh.js), but
 * its wasm is loaded at runtime by FilesetResolver from a URL, so it has to be
 * a real file served from this origin rather than a module import. Copying it
 * from the installed package -- instead of downloading it -- is what guarantees
 * the JS and the wasm can never end up on different versions, which fails in
 * confusing ways at model-init time rather than at build time.
 *
 * Idempotent. Run after `npm install` and whenever @mediapipe/tasks-vision is
 * upgraded:  npm run vendor:mediapipe
 */
import { cp, mkdir, readdir, readFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const source = join(root, "node_modules", "@mediapipe", "tasks-vision", "wasm");
const target = join(root, "public", "mediapipe", "wasm");

if (!existsSync(source)) {
  console.error(
    `Could not find ${source}\n` +
    "Run `npm install` first -- @mediapipe/tasks-vision must be installed for this to copy anything."
  );
  process.exit(1);
}

await mkdir(target, { recursive: true });
await cp(source, target, { recursive: true });

const files = await readdir(target);
const pkg = JSON.parse(
  await readFile(join(root, "node_modules", "@mediapipe", "tasks-vision", "package.json"), "utf8")
);
console.log(`Vendored @mediapipe/tasks-vision@${pkg.version} wasm -> public/mediapipe/wasm`);
for (const file of files) console.log(`  ${file}`);
