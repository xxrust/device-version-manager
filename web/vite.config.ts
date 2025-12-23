import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const backend = env.VITE_VM_BACKEND || "http://127.0.0.1:8080";

  return {
    plugins: [vue()],
    server: {
      proxy: {
        "/api": backend,
        "/login": backend,
        "/logout": backend,
        "/setup": backend,
      },
    },
  };
});

