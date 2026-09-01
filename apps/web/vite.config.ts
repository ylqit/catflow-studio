import vue from "@vitejs/plugin-vue";
import { defineConfig } from "vite";

const apiOrigin = `http://127.0.0.1:${process.env.CATFLOW_PORT ?? "8877"}`;

export default defineConfig({
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": apiOrigin,
    },
  },
  build: {
    outDir: "dist",
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
  },
});
