import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Port bewusst fix auf 5173 (Vite-Standard) -- das Backend hat
// http://localhost:5173 bereits in CORS_ALLOWED_ORIGINS eingetragen
// (siehe dienstplan_saas/config/settings.py).
export default defineConfig({
  plugins: [react()],
  server: { port: 5173 },
});
