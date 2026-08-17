/**
 * Copies MediaPipe Tasks Vision's wasm out of node_modules into public/.
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
