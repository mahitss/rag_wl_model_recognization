import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

declare const process: { env: Record<string, string | undefined> };

const apiEnv = process.env.VITE_API_URL;

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: apiEnv ? {} : { "/api": "http://127.0.0.1:8000" },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
