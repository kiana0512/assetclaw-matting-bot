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

function animationFlowSnapshot(limit = 20, includeFinished = true) {
  const root = path.resolve(process.cwd(), "..", "storage", "animation_flow_runs");
  if (!fs.existsSync(root)) return { ok: true, source: "local_files", count: 0, items: [] };
  const finished = new Set(["DONE", "FAILED", "CANCELED", "BLOCKED"]);
  const files = fs.readdirSync(root)
    .filter((name) => /^AFLOW_.*\.json$/i.test(name))
    .map((name) => {
      const full = path.join(root, name);
      return { full, mtimeMs: fs.statSync(full).mtimeMs };
    })
    .sort((a, b) => b.mtimeMs - a.mtimeMs);
  const items = [];
  for (const file of files) {
    try {
      const item = JSON.parse(fs.readFileSync(file.full, "utf8"));
      if (!item || typeof item !== "object" || Array.isArray(item)) continue;
      if (String(item.workflow_path || "").replaceAll("\\", "/").includes("/storage/debug/current_animation_workflow.json")) continue;
      const sanitized = sanitizeAnimationFlowRun(item);
      if (!includeFinished && finished.has(String(sanitized.status || "").toUpperCase())) continue;
      items.push(sanitized);
      if (items.length >= limit) break;
    } catch {
      // Ignore partial files while a writer is updating the JSON snapshot.
    }
  }
  const current = items.find((item) => !finished.has(String(item.status || "").toUpperCase())) || items[0] || null;
  return { ok: true, source: "local_files", count: items.length, current, items };
}

function workspaceSummary(rootRaw = "") {
  const root = path.resolve(String(rootRaw || "E:/animation_automation").replaceAll("\\", "/"));
  const routed = (stage) => [path.join(root, "scene", stage), path.join(root, "emoji", stage)];
  const specs = [
    ["videos", "视频", routed("videos"), new Set([".mp4", ".mov", ".avi", ".mkv", ".webm"])],
    ["frames", "帧", routed("frames"), new Set([".png", ".jpg", ".jpeg", ".webp"])],
    ["matte", "抠图", routed("matte"), new Set([".png", ".jpg", ".jpeg", ".webp"])],
    ["smooth", "后处理", routed("smooth"), new Set([".png", ".jpg", ".jpeg", ".webp"])],
    ["unity_ready", "Unity Ready", [path.join(root, "unity_ready")], new Set([".png", ".json", ".bytes", ".asset"])],
  ];
  return {
    ok: true,
    source: "local_files",
    root,
    items: specs.map(([key, label, targets, exts]) => {
      const results = targets.map((target) => countTree(target, exts));
      return {
        key,
        label,
        path: targets.join(" ; "),
        exists: targets.some((target) => fs.existsSync(target)),
        count: results.reduce((sum, item) => sum + item.count, 0),
        folders: results.reduce((sum, item) => sum + item.folders, 0),
      };
    }),
  };
}

function countTree(root, exts) {
  let count = 0;
  const folders = new Set();
  if (!fs.existsSync(root)) return { count, folders: 0 };
  const stack = [root];
  while (stack.length) {
    const current = stack.pop();
    let entries = [];
    try {
      entries = fs.readdirSync(current, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      const full = path.join(current, entry.name);
      if (entry.isDirectory()) {
        stack.push(full);
      } else if (entry.isFile() && exts.has(path.extname(entry.name).toLowerCase())) {
        count += 1;
        folders.add(current);
      }
    }
  }
  return { count, folders: folders.size };
}

function sanitizeAnimationFlowRun(item) {
  const children = item.children && typeof item.children === "object" ? item.children : {};
  const unityImport = summarizeUnityImport(children.unity_import && typeof children.unity_import === "object" ? children.unity_import : null);
  const p4Summary = summarizeFlowP4(children.p4 && typeof children.p4 === "object" ? children.p4 : null);
  let status = String(item.status || "").toUpperCase() || "UNKNOWN";
  let stages = Array.isArray(item.stages) ? item.stages : [];
  if (p4Summary?.shelved && ["RUNNING", "DONE", "UNKNOWN"].includes(status)) {
    status = "DONE";
    stages = stages.map(markStageDone);
  }
  return {
    run_id: item.id,
    id: item.id,
    status,
    current_stage: status === "DONE" && p4Summary ? "p4_shelve" : item.current_stage,
    created_at: item.created_at,
    updated_at: item.updated_at,
    date_root: item.date_root,
    unity_ready: item.unity_ready,
    unity_project: item.unity_project,
    package: item.package,
    unity_import_mode: item.unity_import_mode,
    workflow_path: item.workflow_path,
    workflow_name: item.workflow_name,
    fps: item.fps,
    allow_p4_writes: item.allow_p4_writes,
    fake_matting_from_frames: item.fake_matting_from_frames,
    p4: item.p4 ? { stream: item.p4.stream, submit: item.p4.submit, unity_import_mode: item.p4.unity_import_mode } : {},
    stages,
    children: {
      pipeline_run_id: children.pipeline_run_id,
      unity_import: unityImport || undefined,
      p4: p4Summary || undefined,
    },
    error: item.error || "",
  };
}

function markStageDone(stage) {
  if (!stage || typeof stage !== "object" || Array.isArray(stage)) return stage;
  return { ...stage, status: "done" };
}

function summarizeFlowP4(payload) {
  if (!payload) return null;
  const createCl = payload.create_cl && typeof payload.create_cl === "object" ? payload.create_cl : {};
  const reconcile = payload.reconcile && typeof payload.reconcile === "object" ? payload.reconcile : {};
  const shelve = payload.shelve && typeof payload.shelve === "object" ? payload.shelve : {};
  const report = payload.report && typeof payload.report === "object" ? payload.report : {};
  const changelistId = payload.changelist_id || shelve.changelist_id || reconcile.changelist_id || createCl.changelist_id || "";
  const targetPaths = payload.target_paths || reconcile.paths;
  if (!changelistId && !targetPaths) return null;
  return {
    changelist_id: changelistId,
    target_paths: targetPaths,
    shelved: Boolean(shelve.ok),
    reported: Boolean(report.ok),
    reconciled: Boolean(reconcile.ok),
  };
}

function summarizeUnityImport(payload) {
  if (!payload) return null;
  let result = payload.result && typeof payload.result === "object" ? payload.result : {};
  const resultPath = String(payload.result_path || "");
  if (!result.ok && resultPath && fs.existsSync(resultPath)) {
    try {
      result = JSON.parse(fs.readFileSync(resultPath, "utf8"));
    } catch {
      result = payload.result && typeof payload.result === "object" ? payload.result : {};
    }
  }
  const packages = Array.isArray(result.packages) ? result.packages : [];
  const totals = { tasks: 0, textures: 0, replaced: 0, skipped: 0 };
  const compactPackages = packages.filter((item) => item && typeof item === "object").map((item) => {
    const compact = {
      package: item.package || item.name || "",
      mode: item.mode || result.mode || payload.mode || "",
      tasksProcessed: Number(item.tasksProcessed || item.task_count || 0),
      textures: Number(item.textures || item.importedTextures || 0),
      replacedTextures: Number(item.replacedTextures || 0),
      skippedTextures: Number(item.skippedTextures || 0),
      inferredFromDisk: Boolean(item.inferredFromDisk),
    };
    totals.tasks += compact.tasksProcessed;
    totals.textures += compact.textures;
    totals.replaced += compact.replacedTextures;
    totals.skipped += compact.skippedTextures;
    return compact;
  });
  const disk = payload.disk_progress && typeof payload.disk_progress === "object" ? payload.disk_progress : {};
  const latest = payload.latest_status && typeof payload.latest_status === "object" ? payload.latest_status : {};
  const recovered = String(payload.error || "") === "unity_runner_timeout" && Boolean(result.ok);
  const diskConfirmed = Boolean(result.inferredFromDisk || payload.message === "Unity result file was late/missing; disk polling confirmed the import outputs.");
  const displayStatus = diskConfirmed ? "CONFIRMED" : recovered ? "LATE_RESULT" : payload.ok ? "OK" : String(payload.error || "PENDING");
  return {
    ok: payload.ok,
    mode: payload.mode || result.mode,
    error: payload.error || "",
    message: payload.message || "",
    request: payload.request || "",
    result_path: resultPath,
    status_path: payload.status_path || "",
    recovered,
    disk_confirmed: diskConfirmed,
    display_status: displayStatus,
    result: {
      ok: result.ok,
      mode: result.mode || payload.mode,
      inferredFromDisk: Boolean(result.inferredFromDisk),
      packages: compactPackages,
      totals,
    },
    disk_progress: {
      supported: Boolean(disk.supported),
      complete: Boolean(disk.complete),
      sourceTextures: Number(disk.sourceTextures || 0),
      replaceableTextures: Number(disk.replaceableTextures || 0),
      replacedTextures: Number(disk.replacedTextures || 0),
      skippedTextures: Number(disk.skippedTextures || 0),
    },
    latest_status: {
      phase: latest.phase || "",
      package: latest.package || "",
      character: latest.character || "",
      updatedAt: latest.updatedAt || "",
    },
  };
}

function localAnimationFlowApiPlugin() {
  return {
    name: "assetclaw-local-animation-flow-api",
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith("/api/local/animation-flow-runs") && !req.url?.startsWith("/api/local/workspace-summary")) {
          next();
          return;
        }
        res.setHeader("Content-Type", "application/json; charset=utf-8");
        try {
          const url = new URL(req.url || "/", "http://127.0.0.1");
          if (url.pathname === "/api/local/workspace-summary") {
            res.end(JSON.stringify(workspaceSummary(url.searchParams.get("root") || "")));
            return;
          }
          const payload = animationFlowSnapshot(
            Number(url.searchParams.get("limit") || 20),
            url.searchParams.get("include_finished") !== "false",
          );
          res.end(JSON.stringify(payload));
        } catch (error) {
          res.end(JSON.stringify({ ok: false, error: String(error?.message || error), source: "local_files" }));
        }
      });
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const target = (env.ASSETCLAW_AGENT_URL || env.GATEWAY_BASE_URL || readParentEnv("GATEWAY_BASE_URL") || `http://127.0.0.1:${env.GATEWAY_PORT || readParentEnv("GATEWAY_PORT") || "7865"}`).replace(/\/$/, "");
  const skillToken = env.ASSETCLAW_SKILL_TOKEN || env.SKILL_API_TOKEN || readParentEnv("ASSETCLAW_SKILL_TOKEN") || readParentEnv("SKILL_API_TOKEN");

  return {
    plugins: [localAnimationFlowApiPlugin(), vue()],
    server: {
      host: "127.0.0.1",
      port: 5180,
      strictPort: true,
      allowedHosts: [".trycloudflare.com"],
      configureServer(server) {
        server.middlewares.use((req, res, next) => {
          if (!req.url?.startsWith("/api/local/animation-flow-runs") && !req.url?.startsWith("/api/local/workspace-summary")) {
            next();
            return;
          }
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          try {
            const url = new URL(req.url || "/", "http://127.0.0.1");
            if (url.pathname === "/api/local/workspace-summary") {
              res.end(JSON.stringify(workspaceSummary(url.searchParams.get("root") || "")));
              return;
            }
            const payload = animationFlowSnapshot(
              Number(url.searchParams.get("limit") || 20),
              url.searchParams.get("include_finished") !== "false",
            );
            res.end(JSON.stringify(payload));
          } catch (error) {
            res.end(JSON.stringify({ ok: false, error: String(error?.message || error), source: "local_files" }));
          }
        });
        server.middlewares.use("/api/local/path-dialog", async (req, res) => {
          const url = new URL(req.url || "", "http://127.0.0.1");
          const payload = await windowsPathDialog(url.searchParams.get("mode") || "dir", url.searchParams.get("current") || "");
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          res.end(JSON.stringify(payload));
        });
        server.middlewares.use("/api/local/animation-flow-runs", async (req, res) => {
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          try {
            const rawUrl = req.url?.startsWith("?") ? `/${req.url}` : (req.url || "/");
            const url = new URL(rawUrl, "http://127.0.0.1");
            const payload = animationFlowSnapshot(
              Number(url.searchParams.get("limit") || 20),
              url.searchParams.get("include_finished") !== "false",
            );
            res.end(JSON.stringify(payload));
          } catch (error) {
            res.end(JSON.stringify({ ok: false, error: String(error?.message || error), source: "local_files" }));
          }
        });
        server.middlewares.use("/api/local/workspace-summary", async (req, res) => {
          res.setHeader("Content-Type", "application/json; charset=utf-8");
          try {
            const rawUrl = req.url?.startsWith("?") ? `/${req.url}` : (req.url || "/");
            const url = new URL(rawUrl, "http://127.0.0.1");
            res.end(JSON.stringify(workspaceSummary(url.searchParams.get("root") || "")));
          } catch (error) {
            res.end(JSON.stringify({ ok: false, error: String(error?.message || error), source: "local_files" }));
          }
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
