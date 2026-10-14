import { readdirSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const output = join(root, "tauri", "frontend-dist", "index.html");
const inputs = [
  join(root, "tauri", "frontend"),
  join(root, "vite.config.js"),
  join(root, "package.json"),
  join(root, "package-lock.json"),
];

function newestModification(path) {
  const stat = statSync(path);
  if (!stat.isDirectory()) return stat.mtimeMs;
  return Math.max(
    stat.mtimeMs,
    ...readdirSync(path).map((entry) => newestModification(join(path, entry))),
  );
}

const outputTime = statSync(output, { throwIfNoEntry: false })?.mtimeMs ?? 0;
if (outputTime >= Math.max(...inputs.map(newestModification))) {
  console.log("Frontend is up to date.");
} else {
  const { build } = await import("vite");
  await build();
}
