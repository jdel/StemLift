import { execFileSync } from "node:child_process";
import {
  chmodSync,
  copyFileSync,
  mkdirSync,
  readFileSync,
  rmSync,
} from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const pythonVersion = readFileSync(join(root, ".python-version"), "utf8").trim();
const runUv = (arguments_, options) =>
  execFileSync(
    "uvx",
    ["--from", "uv==0.13.0", "uv", ...arguments_],
    options,
  );
runUv(["python", "install", pythonVersion], {
  cwd: root,
  stdio: "inherit",
});
const target = execFileSync("rustc", ["--print", "host-tuple"], {
  encoding: "utf8",
}).trim();
const executableSuffix = process.platform === "win32" ? ".exe" : "";
const cacheRoot = join(root, ".desktop-cache");
const environment = {
  ...process.env,
  CMAKE_POLICY_VERSION_MINIMUM: "3.5",
  HF_HOME: join(cacheRoot, "huggingface"),
  TORCH_HOME: join(cacheRoot, "torch"),
  UV_LINK_MODE: "copy",
};

mkdirSync(cacheRoot, { recursive: true });
runUv(
  [
    "run",
    "--locked",
    "--group",
    "desktop",
    "python",
    "-c",
    "from demucs.pretrained import get_model; get_model('htdemucs')",
  ],
  { cwd: root, env: environment, stdio: "inherit" },
);
runUv(
  [
    "run",
    "--locked",
    "--group",
    "desktop",
    "pyinstaller",
    "--clean",
    "--noconfirm",
    "stemlift.spec",
  ],
  { cwd: root, env: environment, stdio: "inherit" },
);

const source = join(root, "dist", `stemlift${executableSuffix}`);
const destination = join(
  root,
  "tauri",
  "binaries",
  `stemlift-worker-${target}${executableSuffix}`,
);
mkdirSync(dirname(destination), { recursive: true });
rmSync(destination, { force: true });
copyFileSync(source, destination);
if (process.platform !== "win32") chmodSync(destination, 0o755);
console.log(`Built CLI: ${source}`);
console.log(`Staged desktop sidecar: ${destination}`);
