/**
 * Downloads the MediaPipe face-landmarker weights so they
 * can be served from this origin instead of Google's.
 */
import { mkdir, writeFile, stat } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const MODEL_URL =
  "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const target = join(root, "public", "mediapipe", "face_landmarker.task");

try {
  const existing = await stat(target);
  console.log(`Already present: ${target} (${(existing.size / 1e6).toFixed(1)} MB)`);
  console.log("Delete it and re-run to force a fresh download.");
  process.exit(0);
} catch {
  // Not there yet -- carry on and download.
}

console.log(`Downloading ${MODEL_URL}`);
const response = await fetch(MODEL_URL);
if (!response.ok) {
  console.error(`Download failed: HTTP ${response.status} ${response.statusText}`);
  process.exit(1);
}

const bytes = Buffer.from(await response.arrayBuffer());
// A truncated or error-page response would otherwise be written out and then
// fail at model-init time with something that looks like a MediaPipe bug.
if (bytes.length < 1_000_000) {
  console.error(`Refusing to write a suspiciously small file (${bytes.length} bytes).`);
  process.exit(1);
}

await mkdir(dirname(target), { recursive: true });
await writeFile(target, bytes);
console.log(`Wrote ${target} (${(bytes.length / 1e6).toFixed(1)} MB)`);
console.log("faceMesh.js will now load it from this origin instead of Google's.");
