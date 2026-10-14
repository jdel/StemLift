import { open } from "@tauri-apps/plugin-dialog";
import { Command } from "@tauri-apps/plugin-shell";

let audioFile = null;
let outputDirectory = null;
let running = false;
let receivedError = false;

const audioPath = document.querySelector("#audio-path");
const outputPath = document.querySelector("#output-path");
const formatInput = document.querySelector("#format");
const progress = document.querySelector("#progress");
const status = document.querySelector("#status");
const outputs = document.querySelector("#outputs");
const actionButtons = [...document.querySelectorAll("button[data-mode]")];

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
  }
});

document.querySelector("#choose-output").addEventListener("click", async () => {
  const selected = await open({ multiple: false, directory: true });
  if (selected) {
    outputDirectory = selected;
    outputPath.textContent = selected;
  }
});

actionButtons.forEach((button) => {
  button.addEventListener("click", () => startExtraction(button.dataset.mode));
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

  setRunning(true);
  receivedError = false;
  outputs.replaceChildren();
  outputs.hidden = true;
  progress.hidden = false;
  progress.removeAttribute("value");
  status.textContent = "Loading the separation model…";

  const command = Command.sidecar("binaries/stemlift-worker", args);
  command.stdout.on("data", handleWorkerEvent);
  command.stderr.on("data", (line) => {
    if (line.trim()) console.error(line);
  });
  command.on("error", (error) => {
    receivedError = true;
    status.textContent = `Could not start extraction: ${error}`;
    setRunning(false);
  });
  command.on("close", ({ code }) => {
    if (code !== 0 && !receivedError) {
      status.textContent = `Extraction stopped with status ${code}.`;
    }
    setRunning(false);
  });

  try {
    await command.spawn();
  } catch (error) {
    receivedError = true;
    status.textContent = `Could not start extraction: ${error}`;
    setRunning(false);
  }
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
    status.textContent = `Error: ${event.message}`;
  } else if (event.type === "complete") {
    progress.value = 100;
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
}
