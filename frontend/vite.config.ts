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
  build: {
    rollupOptions: {
      output: {
        // Split stable third-party libs into long-cacheable vendor chunks so
        // app-code changes don't bust the browser cache. The function form is
        // used (instead of package-name keys) because @vitejs/plugin-react
        // resolves `react`/`react-dom` through several aliased entry files;
        // matching on the node_modules path reliably captures every module
        // belonging to each package. Heavy page-specific libs (recharts,
        // react-markdown) are pulled into separate chunks automatically by the
        // lazy() route imports in main.tsx.
        manualChunks(id) {
          if (id.includes("node_modules")) {
            if (id.includes("react-dom")) return "react-dom";
            if (id.includes(path.sep + "react" + path.sep) || id.includes("/react/")) return "react";
            if (id.includes("react-router")) return "router";
            // Heavy page-specific libs → their own cacheable vendor chunks so
            // the page chunk itself stays small and these only download when
            // the route that needs them is visited.
            if (id.includes("recharts")) return "recharts";
            if (id.includes("react-markdown") || id.includes("remark") ||
                id.includes("micromark") || id.includes("mdast") ||
                id.includes("hast") || id.includes("unist") ||
                id.includes("trim-lines") || id.includes("decode-named-character-reference")) {
              return "markdown";
            }
          }
        },
      },
    },
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
