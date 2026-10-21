import { defineConfig } from "vite";

export default defineConfig({
  root: "tauri/frontend",
  clearScreen: false,
  server: {
    strictPort: true,
  },
  build: {
    outDir: "../frontend-dist",
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: "tauri/frontend/index.html",
        logs: "tauri/frontend/logs.html",
      },
    },
  },
});
