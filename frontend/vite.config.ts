import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// Canonicalise the project root so Vite, Node, and Windows subst/junction
// paths all agree on one location.
const ROOT = fs.realpathSync.native(path.resolve(__dirname));
const CACHE_DIR = path.join(os.tmpdir(), "kb-frontend-vite-cache");

export default defineConfig({
  plugins: [react()],
  root: ROOT,
  cacheDir: CACHE_DIR,
  resolve: {
    preserveSymlinks: true,
  },
  optimizeDeps: {
    holdUntilCrawlEnd: false,
  },
  server: {
    port: 5173,
    fs: {
      strict: false,
      allow: [ROOT, path.resolve(ROOT, ".."), "/"],
    },
    proxy: {
      "/api": "http://127.0.0.1:8088",
    },
  },
});
