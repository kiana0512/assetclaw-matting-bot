import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
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

function windowsPathDialog(mode = "dir", current = "") {
  const initial = String(current || "").replaceAll("/", "\\").replace(/'/g, "''");
  const isFile = mode === "file";
  const owner = `$owner = New-Object System.Windows.Forms.Form; $owner.TopMost = $true; $owner.ShowInTaskbar = $false; $owner.StartPosition = 'CenterScreen'; $owner.Width = 1; $owner.Height = 1; $owner.Opacity = 0; $owner.Show(); $owner.Activate();`;
  const script = isFile
    ? `Add-Type -AssemblyName System.Windows.Forms; ${owner} $dialog = New-Object System.Windows.Forms.OpenFileDialog; $dialog.Title = '选择本机文件'; if ('${initial}') { if (Test-Path '${initial}') { $item = Get-Item '${initial}' -ErrorAction SilentlyContinue; if ($item -and $item.PSIsContainer) { $dialog.InitialDirectory = $item.FullName } elseif ($item) { $dialog.InitialDirectory = $item.DirectoryName } } }; if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); Write-Output $dialog.FileName }; $owner.Close()`
    : `Add-Type -AssemblyName System.Windows.Forms; ${owner} $dialog = New-Object System.Windows.Forms.FolderBrowserDialog; $dialog.Description = '选择本机目录'; $dialog.ShowNewFolderButton = $true; if ('${initial}' -and (Test-Path '${initial}')) { $item = Get-Item '${initial}' -ErrorAction SilentlyContinue; if ($item -and $item.PSIsContainer) { $dialog.SelectedPath = $item.FullName } elseif ($item) { $dialog.SelectedPath = $item.DirectoryName } }; if ($dialog.ShowDialog($owner) -eq [System.Windows.Forms.DialogResult]::OK) { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); Write-Output $dialog.SelectedPath }; $owner.Close()`;
  return new Promise((resolve) => {
    const child = spawn("powershell.exe", ["-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-Command", script], {
      windowsHide: false,
    });
    let stdout = "";
    let stderr = "";
    const timer = setTimeout(() => {
      child.kill();
      resolve({ ok: false, error: "系统选择器超过 5 分钟没有返回，已自动取消。" });
    }, 5 * 60 * 1000);
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (error) => {
      clearTimeout(timer);
      resolve({ ok: false, error: String(error.message || error) });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      if (code !== 0) {
        resolve({ ok: false, error: stderr || `PowerShell exited with ${code}` });
        return;
      }
      const selected = String(stdout || "").trim();
      resolve(selected ? { ok: true, path: selected } : { ok: false, canceled: true });
    });
  });
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
      strictPort: true,
      configureServer(server) {
        server.middlewares.use("/api/local/path-dialog", async (req, res) => {
          const url = new URL(req.url || "", "http://127.0.0.1");
          const payload = await windowsPathDialog(url.searchParams.get("mode") || "dir", url.searchParams.get("current") || "");
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(JSON.stringify(payload));
        });
      },
      proxy: {
        "/api": {
          target,
          changeOrigin: false,
          rewrite: (rawPath) => {
            const [pathname, query = ""] = rawPath.split("?");
            const mapped = apiRewrite[pathname] || pathname.replace(/^\/api/, "");
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
