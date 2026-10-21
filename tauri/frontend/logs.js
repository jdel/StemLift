import { emit, listen } from "@tauri-apps/api/event";

const workerLogs = document.querySelector("#worker-logs");

await listen("worker-logs-updated", ({ payload }) => {
  workerLogs.textContent = payload || "No worker logs yet.";
  workerLogs.scrollTop = workerLogs.scrollHeight;
});

await emit("worker-logs-ready");
