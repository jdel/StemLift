import { execFileSync } from "node:child_process";
import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
if (process.platform === "win32") {
  execFileSync(process.execPath, [join(root, "scripts", "build-sidecar.mjs")], {
    cwd: root,
    stdio: "inherit",
  });
  process.exit(0);
}

const target = execFileSync("rustc", ["--print", "host-tuple"], {
  encoding: "utf8",
}).trim();
const destination = join(
  root,
  "tauri",
  "binaries",
  `stemlift-worker-${target}`,
);
const launcher = [
  "#!/bin/sh",
  "export CMAKE_POLICY_VERSION_MINIMUM=3.5",
  'worker_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)',
  'project_root=$(CDPATH= cd -- "$worker_dir/../.." && pwd)',
  'exec uv run --project "$project_root" --locked python -m stemlift.desktop_worker "$@"',
  "",
].join("\n");

mkdirSync(dirname(destination), { recursive: true });
writeFileSync(destination, launcher);
chmodSync(destination, 0o755);
console.log(`Prepared development sidecar: ${destination}`);
