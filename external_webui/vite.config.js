import fs from "node:fs";
import path from "node:path";
import { defineConfig, loadEnv } from "vite";
import vue from "@vitejs/plugin-vue";

const apiRewrite = {
  "/api/health": "/health",
  "/api/admin/queue": "/admin/queue",
  "/api/admin/brain-messages": "/admin/brain-messages",
  "/api/admin/memory": "/admin/memory",
  "/api/admin/skill-calls": "/admin/skill-calls",
  "/api/brain/test": "/brain/test",
  "/api/skills/manifest": "/skills/v1/manifest",
  "/api/skills/call": "/skills/v1/call",
};

function readParentEnv(name) {
  const envPath = path.resolve(process.cwd(), "..", ".env");
  if (!fs.existsSync(envPath)) return "";
  const lines = fs.readFileSync(envPath, "utf8").split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#") || !trimmed.includes("=")) continue;
    const [key, ...rest] = trimmed.split("=");
    if (key.trim().toUpperCase() === name.toUpperCase()) {
      return rest.join("=").trim().replace(/^['"]|['"]$/g, "");
    }
  }
  return "";
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = (env.ASSETCLAW_AGENT_URL || env.GATEWAY_BASE_URL || readParentEnv("GATEWAY_BASE_URL") || `http://127.0.0.1:${env.GATEWAY_PORT || readParentEnv("GATEWAY_PORT") || "7865"}`).replace(/\/$/, "");
  const skillToken = env.ASSETCLAW_SKILL_TOKEN || env.SKILL_API_TOKEN || readParentEnv("ASSETCLAW_SKILL_TOKEN") || readParentEnv("SKILL_API_TOKEN");

  return {
    plugins: [vue()],
    server: {
      host: "127.0.0.1",
      port: 5180,
      strictPort: false,
      proxy: {
        "/api": {
          target,
          changeOrigin: false,
          rewrite: (rawPath) => {
            const [pathname, query = ""] = rawPath.split("?");
            const mapped = apiRewrite[pathname] || pathname;
            return query ? `${mapped}?${query}` : mapped;
          },
          configure: (proxy) => {
            proxy.on("proxyReq", (proxyReq, req) => {
              if (skillToken && (req.url?.startsWith("/api/skills/") || req.url?.startsWith("/skills/"))) {
                proxyReq.setHeader("X-Skill-Token", skillToken);
              }
            });
          },
        },
      },
    },
    preview: {
      host: "127.0.0.1",
      port: 5181,
    },
  };
});
