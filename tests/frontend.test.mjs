import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";

class FakeElement {
  constructor({ checked = false, dataset = {}, hidden = false, value = "" } = {}) {
    this.checked = checked;
    this.children = [];
    this.dataset = dataset;
    this.disabled = false;
    this.hidden = hidden;
    this.listeners = new Map();
    this.scrollHeight = 100;
    this.scrollTop = 0;
    this.textContent = "";
    this.value = value;
  }

  addEventListener(event, listener) {
    const listeners = this.listeners.get(event) ?? [];
    listeners.push(listener);
    this.listeners.set(event, listeners);
  }

  async click() {
    for (const listener of this.listeners.get("click") ?? []) {
      await listener({ currentTarget: this });
    }
  }

  removeAttribute(name) {
    if (name === "value") this.value = undefined;
  }

  replaceChildren(...children) {
    this.children = children;
  }
}

test("desktop extraction defaults, logs, and cancellation", async () => {
  const elements = new Map([
    ["#audio-path", new FakeElement()],
    ["#output-path", new FakeElement()],
    ["#format", new FakeElement({ value: "mp3" })],
    ["#progress", new FakeElement({ hidden: true })],
    ["#status", new FakeElement()],
    ["#outputs", new FakeElement({ hidden: true })],
    ["#cancel-extraction", new FakeElement({ hidden: true })],
    ["#choose-audio", new FakeElement()],
    ["#choose-output", new FakeElement()],
    ["#show-worker-logs", new FakeElement()],
  ]);
  const extractButton = new FakeElement({ dataset: { mode: "stems" } });
  const backingButton = new FakeElement({ dataset: { mode: "backing" } });
  const bassStem = new FakeElement({ checked: true, value: "bass" });
  const callbacks = new Map();
  const eventListeners = new Map();
  const dialogResults = [
    "/music/song.mp3",
    "/exports",
    "/other/next.mp3",
  ];
  const emittedEvents = [];
  const killedPids = [];
  const spawnedCommands = [];
  const webviewWindows = [];
  let nextCallback = 1;
  let shellChannel;

  globalThis.document = {
    createElement: () => new FakeElement(),
    querySelector: (selector) => elements.get(selector) ?? null,
    querySelectorAll: (selector) => {
      if (selector === "button[data-mode]") {
        return [extractButton, backingButton];
      }
      if (selector === 'input[name="stems-stem"]:checked') {
        return bassStem.checked ? [bassStem] : [];
      }
      if (selector === 'input[name="backing-stem"]:checked') return [];
      return [];
    },
  };
  globalThis.window = {
    __TAURI_EVENT_PLUGIN_INTERNALS__: {
      unregisterListener() {},
    },
    __TAURI_INTERNALS__: {
      invoke: async (command, args = {}) => {
        if (command === "plugin:event|listen") {
          eventListeners.set(args.event, callbacks.get(args.handler));
          return eventListeners.size;
        }
        if (command === "plugin:event|emit" || command === "plugin:event|emit_to") {
          emittedEvents.push({ command, args });
          return undefined;
        }
        if (command === "plugin:dialog|open") return dialogResults.shift() ?? null;
        if (command === "plugin:path|dirname") {
          return args.path.slice(0, args.path.lastIndexOf("/"));
        }
        if (command === "plugin:shell|spawn") {
          spawnedCommands.push(args);
          shellChannel = args.onEvent;
          return 42;
        }
        if (command === "plugin:shell|kill") {
          killedPids.push(args.pid);
          queueMicrotask(() => {
            shellChannel.onmessage({
              event: "Terminated",
              payload: { code: null, signal: 9 },
            });
          });
          return undefined;
        }
        if (command === "plugin:window|get_all_windows") return [];
        if (command === "plugin:webview|create_webview_window") {
          webviewWindows.push(args.options);
          return undefined;
        }
        throw new Error(`Unexpected Tauri command: ${command}`);
      },
      transformCallback(callback) {
        const id = nextCallback++;
        callbacks.set(id, callback);
        return id;
      },
      unregisterCallback(id) {
        callbacks.delete(id);
      },
    },
  };

  const mainUrl = `${pathToFileURL(resolve("tauri/frontend/main.js")).href}?main-test`;
  await import(mainUrl);

  await elements.get("#choose-audio").click();
  assert.equal(elements.get("#output-path").textContent, "/music");

  await elements.get("#choose-output").click();
  await elements.get("#choose-audio").click();
  assert.equal(elements.get("#audio-path").textContent, "/other/next.mp3");
  assert.equal(elements.get("#output-path").textContent, "/exports");

  await extractButton.click();
  assert.equal(spawnedCommands.length, 1);
  assert.equal(spawnedCommands[0].options.processGroup, true);
  assert.equal(elements.get("#cancel-extraction").hidden, false);
  assert.equal(elements.get("#cancel-extraction").disabled, false);

  shellChannel.onmessage({
    event: "Stdout",
    payload: JSON.stringify({ type: "progress", progress: 61, eta: "00:12" }),
  });
  assert.equal(elements.get("#progress").value, 61);

  await elements.get("#cancel-extraction").click();
  assert.deepEqual(killedPids, [42]);
  assert.equal(elements.get("#progress").value, 0);
  assert.equal(elements.get("#status").textContent, "Extraction canceled.");
  assert.equal(elements.get("#cancel-extraction").hidden, true);

  await extractButton.click();
  shellChannel.onmessage({
    event: "Stdout",
    payload: JSON.stringify({ type: "progress", progress: 23, eta: null }),
  });
  shellChannel.onmessage({
    event: "Stdout",
    payload: JSON.stringify({ type: "error", message: "worker failed" }),
  });
  assert.equal(elements.get("#progress").value, 0);
  assert.equal(elements.get("#status").textContent, "Error: worker failed");
  shellChannel.onmessage({
    event: "Terminated",
    payload: { code: 1, signal: null },
  });

  await elements.get("#show-worker-logs").click();
  await new Promise((resolvePromise) => setImmediate(resolvePromise));
  assert.ok(webviewWindows.length >= 1);
  assert.equal(webviewWindows.at(-1).label, "worker-logs");
  assert.equal(webviewWindows.at(-1).url, "logs.html");

  const workerLogs = new FakeElement();
  elements.set("#worker-logs", workerLogs);
  const logsUrl = `${pathToFileURL(resolve("tauri/frontend/logs.js")).href}?logs-test`;
  await import(logsUrl);
  assert.ok(emittedEvents.some(({ args }) => args.event === "worker-logs-ready"));

  eventListeners.get("worker-logs-updated")({ payload: "[stderr] sample" });
  assert.equal(workerLogs.textContent, "[stderr] sample");
  assert.equal(workerLogs.scrollTop, workerLogs.scrollHeight);
});

test("desktop markup and window defaults stay compact", async () => {
  const html = await readFile("tauri/frontend/index.html", "utf8");
  const config = JSON.parse(await readFile("tauri/tauri.conf.json", "utf8"));
  const mainWindow = config.app.windows.find(({ label }) => label === "main");

  assert.doesNotMatch(html, /<header>/);
  assert.match(html, /id="cancel-extraction"/);
  assert.equal(mainWindow.height, 580);
  assert.equal(mainWindow.minHeight, 480);
});
