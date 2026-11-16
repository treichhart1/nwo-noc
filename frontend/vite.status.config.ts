import { defineConfig } from "vite";
export default defineConfig({
  base: "./",
  build: {
    outDir: "dist-status",
    rollupOptions: {
      input: "network-status.html",
      output: {
        entryFileNames: "assets/network-status.js",
        assetFileNames: "assets/network-status.[ext]",
      },
    },
  },
});
