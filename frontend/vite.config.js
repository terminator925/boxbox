import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  define: {
    "import.meta.env.VITE_BOXBOX_BUILD_STAMP": JSON.stringify(process.env.VITE_BOXBOX_BUILD_STAMP || "dev"),
  },
  server: {
    host: "0.0.0.0",
    port: 5173,
  },
});
