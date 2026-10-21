import { emitTo, listen } from "@tauri-apps/api/event";
import { dirname } from "@tauri-apps/api/path";
import { WebviewWindow } from "@tauri-apps/api/webviewWindow";
import { open } from "@tauri-apps/plugin-dialog";
import { Command } from "@tauri-apps/plugin-shell";

const WORKER_LOG_WINDOW = "worker-logs";

let audioFile = null;
let outputDirectory = null;
let outputDirectoryWasChosen = false;
let running = false;
let receivedError = false;
let workerLogText = "";
let activeWorker = null;
let cancellationRequested = false;

const audioPath = document.querySelector("#audio-path");
const outputPath = document.querySelector("#output-path");
const formatInput = document.querySelector("#format");
const progress = document.querySelector("#progress");
const status = document.querySelector("#status");
const outputs = document.querySelector("#outputs");
const cancelButton = document.querySelector("#cancel-extraction");
const actionButtons = [...document.querySelectorAll("button[data-mode]")];

await listen("worker-logs-ready", publishWorkerLogs);

document.querySelector("#choose-audio").addEventListener("click", async () => {
  const selected = await open({
    multiple: false,
    directory: false,
    filters: [{
      name: "Audio",
      extensions: ["mp3", "wav", "flac", "ogg"],
    }],
  });
  if (selected) {
    audioFile = selected;
    audioPath.textContent = selected;
    if (!outputDirectoryWasChosen) {
      outputDirectory = await dirname(selected);
      outputPath.textContent = outputDirectory;
    }
  }
});

document.querySelector("#choose-output").addEventListener("click", async () => {
  const selected = await open({ multiple: false, directory: true });
  if (selected) {
    outputDirectory = selected;
    outputDirectoryWasChosen = true;
    outputPath.textContent = selected;
  }
});

actionButtons.forEach((button) => {
  button.addEventListener("click", () => startExtraction(button.dataset.mode));
});

document.querySelector("#show-worker-logs").addEventListener("click", () => {
  void showWorkerLogs();
});

cancelButton.addEventListener("click", async () => {
  if (!activeWorker) return;

  cancellationRequested = true;
  cancelButton.disabled = true;
  status.textContent = "Canceling extraction…";
  try {
    await activeWorker.kill();
  } catch (error) {
    if (!running) return;
    cancellationRequested = false;
    appendWorkerLog("error", `Could not cancel extraction: ${error}`);
    void showWorkerLogs();
    status.textContent = `Could not cancel extraction: ${error}`;
    cancelButton.disabled = false;
  }
});

async function startExtraction(mode) {
  if (running) return;
  if (!audioFile || !outputDirectory) {
    status.textContent = "Choose both an audio file and an output folder.";
    return;
  }

  const selectedStems = [
    ...document.querySelectorAll(`input[name="${mode}-stem"]:checked`),
  ].map((checkbox) => checkbox.value);
  if (!selectedStems.length) {
    status.textContent = "Select at least one instrument.";
    return;
  }
  if (mode === "backing" && selectedStems.length === 4) {
    status.textContent = "A backing track must leave at least one instrument.";
    return;
  }

  const args = [
    audioFile,
    outputDirectory,
    "--format",
    formatInput.value,
    "--mode",
    mode,
  ];
  selectedStems.forEach((stem) => args.push("--stem", stem));

  activeWorker = null;
  cancellationRequested = false;
  setRunning(true);
  receivedError = false;
  outputs.replaceChildren();
  outputs.hidden = true;
  workerLogText = "";
  publishWorkerLogs();
  progress.hidden = false;
  progress.removeAttribute("value");
  status.textContent = "Loading the separation model…";

  const command = Command.sidecar("binaries/stemlift-worker", args, {
    processGroup: true,
  });
  command.stdout.on("data", (line) => {
    appendWorkerLog("stdout", line);
    handleWorkerEvent(line);
  });
  command.stderr.on("data", (line) => {
    if (line.trim()) appendWorkerLog("stderr", line);
  });
  command.on("error", (error) => {
    activeWorker = null;
    receivedError = true;
    progress.value = 0;
    appendWorkerLog("error", error);
    void showWorkerLogs();
    status.textContent = `Could not start extraction: ${error}`;
    setRunning(false);
  });
  command.on("close", ({ code, signal }) => {
    activeWorker = null;
    appendWorkerLog(
      "worker",
      `Exited with status ${code}${signal === null ? "" : ` (signal ${signal})`}`,
    );
    if (cancellationRequested) {
      progress.value = 0;
      status.textContent = "Extraction canceled.";
    } else if (code !== 0 && !receivedError) {
      progress.value = 0;
      void showWorkerLogs();
      status.textContent = signal === null
        ? `Extraction stopped with status ${code}.`
        : `Extraction stopped by signal ${signal}.`;
    }
    cancellationRequested = false;
    setRunning(false);
  });

  try {
    const worker = await command.spawn();
    if (running) {
      activeWorker = worker;
      cancelButton.disabled = false;
    }
  } catch (error) {
    activeWorker = null;
    receivedError = true;
    progress.value = 0;
    appendWorkerLog("error", error);
    void showWorkerLogs();
    status.textContent = `Could not start extraction: ${error}`;
    setRunning(false);
  }
}

function appendWorkerLog(source, message) {
  workerLogText += `[${source}] ${message}\n`;
  publishWorkerLogs();
}

function publishWorkerLogs() {
  void emitTo(
    WORKER_LOG_WINDOW,
    "worker-logs-updated",
    workerLogText,
  ).catch(() => {});
}

async function showWorkerLogs() {
  const existingWindow = await WebviewWindow.getByLabel(WORKER_LOG_WINDOW);
  if (existingWindow) {
    await existingWindow.setFocus();
    return;
  }

  const logWindow = new WebviewWindow(WORKER_LOG_WINDOW, {
    url: "logs.html",
    title: "StemLift Worker Logs",
    width: 760,
    height: 520,
    minWidth: 480,
    minHeight: 320,
  });
  logWindow.once("tauri://error", ({ payload }) => {
    console.error(`Could not open worker logs: ${payload}`);
  });
}

function handleWorkerEvent(line) {
  let event;
  try {
    event = JSON.parse(line);
  } catch (_) {
    return;
  }

  if (event.type === "progress") {
    progress.value = event.progress;
    const eta = event.eta ? ` · ETA ${event.eta}` : "";
    status.textContent = `Separating… ${event.progress}%${eta}`;
  } else if (event.type === "error") {
    receivedError = true;
    progress.value = 0;
    cancelButton.disabled = true;
    void showWorkerLogs();
    status.textContent = `Error: ${event.message}`;
  } else if (event.type === "complete") {
    progress.value = 100;
    cancelButton.disabled = true;
    outputs.replaceChildren(
      ...event.outputs.map((path) => {
        const item = document.createElement("li");
        item.textContent = path;
        return item;
      }),
    );
    outputs.hidden = false;
    status.textContent = "Done. Your files are ready.";
  }
}

function setRunning(value) {
  running = value;
  actionButtons.forEach((button) => { button.disabled = value; });
  document.querySelector("#choose-audio").disabled = value;
  document.querySelector("#choose-output").disabled = value;
  formatInput.disabled = value;
  cancelButton.hidden = !value;
  cancelButton.disabled = !value || activeWorker === null;
}
