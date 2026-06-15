from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

from assetclaw_matting.skills.frame_skills import default_automation_paths
from assetclaw_matting.skills.security import validate_path


DEFAULT_UNITY_PROJECT = "D:/Spark/Client"
DEFAULT_MCP_URL = "http://127.0.0.1:8080/mcp"
RUNNER_RELATIVE = "Assets/Editor/CodexAnimImportApiRunner.cs"
REQUEST_DIR_RELATIVE = "Temp/CodexAnimImportApi"
REQUEST_RELATIVE = "Temp/CodexAnimImportApiRequest.json"
RESULT_RELATIVE = "Temp/CodexAnimImportApiResult.json"


def preview(
    unity_ready: str,
    unity_project: str | None = None,
    package: str = "both",
    mode: str = "import",
    import_mode: str | None = None,
    mcp_url: str = DEFAULT_MCP_URL,
    **_: Any,
) -> dict[str, Any]:
    ready_root = validate_path(unity_ready, must_exist=True)
    project_root = validate_path(unity_project or DEFAULT_UNITY_PROJECT, must_exist=True)
    selected_mode = _normalize_mode(import_mode or mode)
    packages = [_package_summary(ready_root, item) for item in _selected_packages(package)]
    api = _probe_mcp(mcp_url)
    return {
        "ok": True,
        "operation": "preview",
        "mode": selected_mode,
        "unity_ready": str(ready_root),
        "unity_project": str(project_root),
        "package": package,
        "packages": packages,
        "api": api,
        "can_import_now": bool(api.get("available")),
        "message": "Unity MCP is reachable." if api.get("available") else "Unity MCP is not reachable; no UI click fallback will be used.",
    }


def run_import(
    unity_ready: str,
    unity_project: str | None = None,
    package: str = "both",
    mode: str = "import",
    import_mode: str | None = None,
    mcp_url: str = DEFAULT_MCP_URL,
    timeout_seconds: int = 900,
    **_: Any,
) -> dict[str, Any]:
    selected_mode = _normalize_mode(import_mode or mode)
    plan = preview(unity_ready=unity_ready, unity_project=unity_project, package=package, mode=selected_mode, mcp_url=mcp_url)
    if not plan.get("api", {}).get("available"):
        return {
            **plan,
            "ok": False,
            "operation": "import",
            "error": "unity_mcp_off",
            "message": "Unity MCP/API is not reachable; import is refused instead of using UI clicking or unmanaged fallbacks.",
        }
    project_root = Path(plan["unity_project"])
    request_id = "REQ_" + uuid.uuid4().hex[:12].upper()
    request_dir = project_root / REQUEST_DIR_RELATIVE
    request_path = request_dir / f"{request_id}_request.json"
    result_path = request_dir / f"{request_id}_result.json"
    status_path = request_dir / f"{request_id}_status.json"
    legacy_request_path = project_root / REQUEST_RELATIVE
    legacy_result_path = project_root / RESULT_RELATIVE
    runner_path = project_root / RUNNER_RELATIVE
    meta_path = Path(str(runner_path) + ".meta")
    cleanup_runner = False
    runnable_packages = [item["package"] for item in plan["packages"] if not item.get("skipped")]
    if not runnable_packages:
        return {
            **plan,
            "ok": True,
            "operation": "import",
            "runner": "",
            "result": {
                "ok": True,
                "mode": selected_mode,
                "packages": [
                    {
                        "package": item["package"],
                        "mode": selected_mode,
                        "tasksProcessed": 0,
                        "textures": 0,
                        "replacedTextures": 0,
                        "skippedTextures": 0,
                        "skipped": True,
                        "skipReason": item.get("skip_reason") or "empty manifest",
                    }
                    for item in plan["packages"]
                ],
                "message": "No Unity import packages have tasks; skipped Unity runner.",
            },
        }
    request = {
        "requestId": request_id,
        "unityReady": plan["unity_ready"],
        "projectRoot": plan["unity_project"],
        "packages": runnable_packages,
        "mode": selected_mode,
        "createdAt": time.time(),
    }
    try:
        legacy_result_path.unlink(missing_ok=True)
        result_path.unlink(missing_ok=True)
        status_path.unlink(missing_ok=True)
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
        legacy_request_path.parent.mkdir(parents=True, exist_ok=True)
        legacy_request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
        runner_path.parent.mkdir(parents=True, exist_ok=True)
        runner_path.write_text(_runner_source(request_path, result_path, status_path), encoding="utf-8")

        timeout = max(30, int(timeout_seconds))
        start_time = time.time()
        deadline = start_time + timeout
        hard_deadline = start_time + max(timeout, min(7200, timeout * 3))
        last_status_mtime = 0.0
        latest_status: dict[str, Any] = {}
        latest_disk_progress: dict[str, Any] = {}
        disk_complete_at = 0.0
        while time.time() < hard_deadline:
            if result_path.is_file():
                payload = json.loads(result_path.read_text(encoding="utf-8-sig"))
                legacy_result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                cleanup_runner = True
                return {
                    **plan,
                    "ok": bool(payload.get("ok")),
                    "error": payload.get("error") or "",
                    "operation": "import",
                    "runner": str(runner_path),
                    "result": payload,
                    "request": str(request_path),
                    "result_path": str(result_path),
                    "status_path": str(status_path),
                    "disk_progress": latest_disk_progress,
                }
            if status_path.is_file():
                try:
                    mtime = status_path.stat().st_mtime
                    if mtime > last_status_mtime:
                        last_status_mtime = mtime
                        latest_status = json.loads(status_path.read_text(encoding="utf-8-sig"))
                        deadline = max(deadline, time.time() + 300)
                except (OSError, json.JSONDecodeError):
                    pass
            disk_progress = _infer_unity_disk_progress(plan, project_root, selected_mode, start_time)
            if disk_progress.get("supported"):
                latest_disk_progress = disk_progress
                if disk_progress.get("complete"):
                    if not disk_complete_at:
                        disk_complete_at = time.time()
                    if time.time() - disk_complete_at >= 60:
                        payload = _inferred_success_payload(selected_mode, disk_progress)
                        legacy_result_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
                        cleanup_runner = True
                        return {
                            **plan,
                            "ok": True,
                            "error": "",
                            "operation": "import",
                            "runner": str(runner_path),
                            "result": payload,
                            "request": str(request_path),
                            "result_path": str(result_path),
                            "status_path": str(status_path),
                            "latest_status": latest_status,
                            "disk_progress": disk_progress,
                            "message": "Unity result file was late/missing; disk polling confirmed the import outputs.",
                        }
                else:
                    disk_complete_at = 0.0
            if time.time() >= deadline:
                break
            time.sleep(1.0)
        return {
            **plan,
            "ok": False,
            "error": "unity_runner_timeout",
            "operation": "import",
            "runner": str(runner_path),
            "request": str(request_path),
            "result_path": str(result_path),
            "status_path": str(status_path),
            "latest_status": latest_status,
            "disk_progress": latest_disk_progress,
            "message": (
                f"Unity did not produce {result_path} within {timeout_seconds}s. "
                "Runner/request files were kept in place so Unity can still finish and write the result."
            ),
        }
    finally:
        if cleanup_runner:
            for path in (runner_path, meta_path, request_path, status_path, legacy_request_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass


def status(
    unity_ready: str | None = None,
    unity_project: str | None = None,
    package: str = "both",
    mode: str = "import",
    import_mode: str | None = None,
    mcp_url: str = DEFAULT_MCP_URL,
    **kwargs: Any,
) -> dict[str, Any]:
    ready = unity_ready or str(Path(default_automation_paths()["workspace_root"]) / "unity_ready")
    selected_mode = _normalize_mode(import_mode or mode)
    try:
        return preview(unity_ready=ready, unity_project=unity_project, package=package, mode=selected_mode, mcp_url=mcp_url, **kwargs)
    except (FileNotFoundError, ValueError) as exc:
        return {
            "ok": True,
            "mode": selected_mode,
            "unity_ready": ready,
            "unity_project": str(validate_path(unity_project or DEFAULT_UNITY_PROJECT, must_exist=False)),
            "package": package,
            "can_import_now": False,
            "error": str(exc),
            "message": "unity_ready is not ready yet.",
        }


def _normalize_mode(value: str | None) -> str:
    raw = (value or "import").strip().lower()
    if raw in {"import", "new", "batch", "导入", "新导入", "批量导入"}:
        return "import"
    if raw in {"iteration", "iterate", "replace", "replacement", "update", "iter", "迭代", "资源迭代", "替换", "贴图迭代", "高清化"}:
        return "iteration"
    raise ValueError("mode/import_mode must be import or iteration")


def _selected_packages(package: str) -> list[str]:
    normalized = (package or "both").lower()
    if normalized == "both":
        return ["scene", "emoji"]
    if normalized not in {"scene", "emoji"}:
        raise ValueError("package must be scene, emoji, or both")
    return [normalized]


def _package_summary(ready_root: Path, package: str) -> dict[str, Any]:
    json_path = ready_root / package / "animation_resource_manifest.json"
    frames_root = ready_root / package / "frames"
    if not json_path.is_file():
        raise FileNotFoundError(str(json_path))
    data = json.loads(json_path.read_text(encoding="utf-8"))
    items = data.get("items") or {}
    if not frames_root.is_dir() and items:
        raise FileNotFoundError(str(frames_root))
    tasks = []
    frame_count = 0
    for character, animations in items.items():
        if not isinstance(animations, dict):
            continue
        for animation, meta in animations.items():
            source_dir = frames_root / f"{character}-{animation}"
            png_count = len(list(source_dir.glob("*.png"))) if source_dir.is_dir() else 0
            frame_count += png_count
            tasks.append(
                {
                    "character": character,
                    "animation": animation,
                    "types": list((meta or {}).get("types") or []),
                    "source_dir": str(source_dir),
                    "frame_count": png_count,
                    "source_exists": source_dir.is_dir(),
                }
            )
    return {
        "package": package,
        "json": str(json_path),
        "frames_root": str(frames_root),
        "frames_root_exists": frames_root.is_dir(),
        "task_count": len(tasks),
        "frame_count": frame_count,
        "skipped": len(tasks) == 0,
        "skip_reason": "empty manifest" if len(tasks) == 0 else "",
        "tasks": tasks,
    }


def _infer_unity_disk_progress(plan: dict[str, Any], project_root: Path, mode: str, started_at: float) -> dict[str, Any]:
    if mode != "iteration":
        return {"supported": False, "reason": "disk inference is only enabled for iteration mode"}
    packages = []
    totals = {
        "sourceTextures": 0,
        "replaceableTextures": 0,
        "replacedTextures": 0,
        "skippedTextures": 0,
    }
    for package in plan.get("packages") or []:
        if package.get("skipped"):
            packages.append(
                {
                    "package": package.get("package"),
                    "skipped": True,
                    "skipReason": package.get("skip_reason") or "empty manifest",
                    "task_count": 0,
                    "sourceTextures": 0,
                    "replaceableTextures": 0,
                    "replacedTextures": 0,
                    "skippedTextures": 0,
                    "complete": True,
                }
            )
            continue
        task_items = []
        package_totals = {
            "sourceTextures": 0,
            "replaceableTextures": 0,
            "replacedTextures": 0,
            "skippedTextures": 0,
        }
        for task in package.get("tasks") or []:
            progress = _infer_iteration_task_progress(project_root, task, started_at)
            task_items.append(progress)
            for key in package_totals:
                package_totals[key] += int(progress.get(key) or 0)
        package_complete = package_totals["replaceableTextures"] > 0 and package_totals["replacedTextures"] >= package_totals["replaceableTextures"]
        packages.append(
            {
                "package": package.get("package"),
                "task_count": len(task_items),
                **package_totals,
                "complete": package_complete,
                "tasks": task_items,
            }
        )
        for key in totals:
            totals[key] += package_totals[key]
    runnable = [item for item in packages if not item.get("skipped")]
    complete = bool(runnable) and all(item.get("complete") for item in runnable)
    return {
        "supported": True,
        **totals,
        "complete": complete,
        "packages": packages,
    }


def _infer_iteration_task_progress(project_root: Path, task: dict[str, Any], started_at: float) -> dict[str, Any]:
    source_dir = Path(str(task.get("source_dir") or ""))
    source_files = _source_texture_files(source_dir)
    targets = [_iteration_target_path(project_root, task, source) for source in source_files]
    replaceable = [target for target in targets if target is not None and target.is_file()]
    replaced = [target for target in replaceable if _mtime_after(target, started_at)]
    return {
        "character": task.get("character") or "",
        "animation": task.get("animation") or "",
        "sourceDir": str(source_dir),
        "targetDir": str(_iteration_target_dir(project_root, task)),
        "sourceTextures": len(source_files),
        "replaceableTextures": len(replaceable),
        "replacedTextures": len(replaced),
        "skippedTextures": max(0, len(source_files) - len(replaceable)),
        "complete": bool(replaceable) and len(replaced) >= len(replaceable),
    }


def _source_texture_files(source_dir: Path) -> list[Path]:
    if not source_dir.is_dir():
        return []
    return [path for path in sorted(source_dir.iterdir()) if path.is_file() and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".tga", ".psd", ".bmp", ".gif"}]


def _iteration_target_path(project_root: Path, task: dict[str, Any], source: Path) -> Path | None:
    seq = _normalized_sequence(source.stem)
    if seq is None:
        return None
    character = str(task.get("character") or "")
    animation = str(task.get("animation") or "")
    prefix = _texture_prefix(task)
    filename = f"{prefix}{character.lower()}_{animation.lower()}_{seq}.png"
    return _iteration_target_dir(project_root, task) / filename


def _iteration_target_dir(project_root: Path, task: dict[str, Any]) -> Path:
    character = str(task.get("character") or "")
    folder = character[:1].upper() + character[1:] if character else character
    category = _category(task)
    if category == "character":
        return project_root / "Assets" / "Art" / "UI" / "SpritesAnim" / "CharacterAnim" / folder / "Common"
    subdir = "Chat" if category == "story" else "Common"
    return project_root / "Assets" / "Art" / "UI" / "SpritesAnim" / "Emoji" / folder / subdir


def _category(task: dict[str, Any]) -> str:
    joined = " ".join(str(item).lower() for item in (task.get("types") or []))
    if "角色" in joined or "character" in joined:
        return "character"
    if "剧情" in joined or "story" in joined or "chat" in joined:
        return "story"
    return "order"


def _texture_prefix(task: dict[str, Any]) -> str:
    category = _category(task)
    if category == "character":
        return "spch_full_"
    if category == "story":
        return "spch_chat_"
    return "spch_common_"


def _normalized_sequence(name_without_ext: str) -> str | None:
    if not name_without_ext:
        return None
    if name_without_ext.isdigit():
        value = int(name_without_ext)
        return f"0{value}" if value < 10 else str(value)
    tail = name_without_ext.rsplit("_", 1)[-1]
    if tail.isdigit():
        value = int(tail)
        return f"0{value}" if value < 10 else str(value)
    return None


def _mtime_after(path: Path, started_at: float) -> bool:
    try:
        return path.stat().st_mtime >= started_at - 5
    except OSError:
        return False


def _inferred_success_payload(mode: str, disk_progress: dict[str, Any]) -> dict[str, Any]:
    packages = []
    for package in disk_progress.get("packages") or []:
        packages.append(
            {
                "package": package.get("package"),
                "mode": mode,
                "tasksProcessed": package.get("task_count") or 0,
                "textures": package.get("replacedTextures") or 0,
                "replacedTextures": package.get("replacedTextures") or 0,
                "skippedTextures": package.get("skippedTextures") or 0,
                "createdOverrideControllers": 0,
                "createdAnimationClips": 0,
                "inferredFromDisk": True,
            }
        )
    return {
        "ok": True,
        "mode": mode,
        "packages": packages,
        "inferredFromDisk": True,
        "finishedAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def _probe_mcp(mcp_url: str) -> dict[str, Any]:
    try:
        req = urllib.request.Request(mcp_url, method="GET")
        with urllib.request.urlopen(req, timeout=1.0) as response:
            return {"available": True, "url": mcp_url, "status": response.status}
    except urllib.error.HTTPError as exc:
        return {"available": exc.code in {405, 406}, "url": mcp_url, "status": exc.code, "detail": str(exc)}
    except OSError as exc:
        return {"available": False, "url": mcp_url, "error": str(exc)}


def _runner_source(request_path: Path, result_path: Path, status_path: Path) -> str:
    request = str(request_path).replace("\\", "\\\\")
    result = str(result_path).replace("\\", "\\\\")
    status = str(status_path).replace("\\", "\\\\")
    return f'''// Auto-generated by assetclaw_matting unity_import.run. Do not commit.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using SparkTools.Editor.AnimTextureImporter;
using UnityEditor;
using UnityEngine;

[InitializeOnLoad]
public static class CodexAnimImportApiRunner
{{
    private const string RequestPath = "{request}";
    private const string ResultPath = "{result}";
    private const string StatusPath = "{status}";

    static CodexAnimImportApiRunner()
    {{
        EditorApplication.delayCall += RunOnce;
    }}

    private static void RunOnce()
    {{
        try
        {{
            if (!File.Exists(RequestPath)) return;
            var req = JObject.Parse(File.ReadAllText(RequestPath));
            WriteStatus(new JObject
            {{
                ["phase"] = "started",
                ["requestId"] = req.Value<string>("requestId") ?? "",
                ["updatedAt"] = DateTime.Now.ToString("o")
            }});
            var unityReady = req.Value<string>("unityReady");
            var mode = (req.Value<string>("mode") ?? "import").ToLowerInvariant();
            var packages = (req["packages"] as JArray)?.Select(x => x.ToString()).ToList() ?? new List<string>();
            var totals = new JArray();
            foreach (var pkg in packages)
            {{
                WriteStatus(new JObject
                {{
                    ["phase"] = "package_started",
                    ["package"] = pkg,
                    ["mode"] = mode,
                    ["updatedAt"] = DateTime.Now.ToString("o")
                }});
                totals.Add(ImportPackage(unityReady, pkg, mode));
                WriteStatus(new JObject
                {{
                    ["phase"] = "package_finished",
                    ["package"] = pkg,
                    ["mode"] = mode,
                    ["updatedAt"] = DateTime.Now.ToString("o")
                }});
            }}
            WriteResult(new JObject
            {{
                ["ok"] = true,
                ["mode"] = mode,
                ["packages"] = totals,
                ["finishedAt"] = DateTime.Now.ToString("o")
            }});
        }}
        catch (Exception ex)
        {{
            WriteStatus(new JObject
            {{
                ["phase"] = "failed",
                ["error"] = ex.ToString(),
                ["updatedAt"] = DateTime.Now.ToString("o")
            }});
            WriteResult(new JObject
            {{
                ["ok"] = false,
                ["error"] = ex.ToString(),
                ["finishedAt"] = DateTime.Now.ToString("o")
            }});
        }}
    }}

    private static JObject ImportPackage(string unityReady, string pkg, string mode)
    {{
        var jsonPath = Path.Combine(unityReady, pkg, "animation_resource_manifest.json");
        var framesRoot = Path.Combine(unityReady, pkg, "frames");
        var module = new AnimTextureImportModule();
        module.jsonFilePath = jsonPath;
        module.sourceRoot = framesRoot;
        module.overwriteExisting = true;
        module.setSpriteType = true;
        module.defaultMaxSize = 256;
        module.overrideAndroid = true;
        module.androidMaxSize = 256;
        LoadTasks(module, jsonPath, framesRoot);
        if (mode == "iteration")
            return IterationPackage(module, pkg);

        var resolveSource = typeof(AnimTextureImportModule).GetMethod("ResolveSourceDir", BindingFlags.Instance | BindingFlags.NonPublic);
        var resolveTarget = typeof(AnimTextureImportModule).GetMethod("ResolveTargetDir", BindingFlags.Instance | BindingFlags.NonPublic);
        var applySettings = typeof(AnimTextureImportModule).GetMethod("ApplyTextureSettings", BindingFlags.Instance | BindingFlags.NonPublic);
        var checkAssets = typeof(AnimTextureImportModule).GetMethod("CheckAndCreateAnimationAssets", BindingFlags.Instance | BindingFlags.NonPublic);
        var writeManifest = typeof(AnimTextureImportModule).GetMethod("WriteImportManifest", BindingFlags.Instance | BindingFlags.NonPublic);
        if (resolveSource == null || resolveTarget == null || applySettings == null || checkAssets == null || writeManifest == null)
            throw new Exception("AnimTextureImportModule private API not found.");

        var imported = new List<string>();
        int taskOk = 0;
        int fileCount = 0;
        AssetDatabase.StartAssetEditing();
        try
        {{
            foreach (var task in module.tasks)
            {{
                var src = (string)resolveSource.Invoke(module, new object[] {{ task }});
                if (!Directory.Exists(src)) continue;
                WriteStatus(new JObject
                {{
                    ["phase"] = "import_task",
                    ["package"] = pkg,
                    ["character"] = task.character,
                    ["sourceDir"] = src,
                    ["textures"] = fileCount,
                    ["updatedAt"] = DateTime.Now.ToString("o")
                }});
                var targetAssetDir = (string)resolveTarget.Invoke(module, new object[] {{ task }});
                var targetFullDir = ToFullPath(targetAssetDir);
                Directory.CreateDirectory(targetFullDir);
                foreach (var file in Directory.EnumerateFiles(src).Where(IsTexture))
                {{
                    var destFull = Path.Combine(targetFullDir, Path.GetFileName(file));
                    File.Copy(file, destFull, true);
                    imported.Add(targetAssetDir.TrimEnd('/') + "/" + Path.GetFileName(file));
                    fileCount++;
                }}
                taskOk++;
            }}
        }}
        finally
        {{
            AssetDatabase.StopAssetEditing();
            AssetDatabase.Refresh();
        }}

        applySettings.Invoke(module, new object[] {{ imported }});
        object[] outArgs = new object[] {{ null, null }};
        checkAssets.Invoke(module, outArgs);
        var createdOc = (List<string>)outArgs[0];
        var createdAnim = (List<string>)outArgs[1];
        writeManifest.Invoke(module, new object[] {{ imported, createdOc, createdAnim }});
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();
        Debug.Log($"[CodexAnimImportApiRunner] {{pkg}} imported tasks={{taskOk}} textures={{fileCount}} oc={{createdOc.Count}} anim={{createdAnim.Count}}");
        return new JObject
        {{
            ["package"] = pkg,
            ["mode"] = "import",
            ["tasksProcessed"] = taskOk,
            ["textures"] = fileCount,
            ["createdOverrideControllers"] = createdOc.Count,
            ["createdAnimationClips"] = createdAnim.Count
        }};
    }}

    private static JObject IterationPackage(AnimTextureImportModule module, string pkg)
    {{
        var resolveSource = typeof(AnimTextureImportModule).GetMethod("ResolveSourceDir", BindingFlags.Instance | BindingFlags.NonPublic);
        var applySettings = typeof(AnimTextureImportModule).GetMethod("ApplyTextureSettings", BindingFlags.Instance | BindingFlags.NonPublic);
        var writeManifest = typeof(AnimTextureImportModule).GetMethod("WriteImportManifest", BindingFlags.Instance | BindingFlags.NonPublic);
        if (resolveSource == null || applySettings == null || writeManifest == null)
            throw new Exception("AnimTextureImportModule iteration private API not found.");

        var imported = new List<string>();
        int taskOk = 0;
        int replacedFiles = 0;
        int skippedFiles = 0;
        AssetDatabase.StartAssetEditing();
        try
        {{
            foreach (var task in module.tasks)
            {{
                var src = (string)resolveSource.Invoke(module, new object[] {{ task }});
                if (!Directory.Exists(src)) continue;
                WriteStatus(new JObject
                {{
                    ["phase"] = "iteration_task",
                    ["package"] = pkg,
                    ["character"] = task.character,
                    ["sourceDir"] = src,
                    ["replacedTextures"] = replacedFiles,
                    ["skippedTextures"] = skippedFiles,
                    ["updatedAt"] = DateTime.Now.ToString("o")
                }});
                var sourceDirName = Path.GetFileName(src.TrimEnd(Path.DirectorySeparatorChar, '/'));
                int dash = sourceDirName.IndexOf('-');
                string charKey = dash >= 0 ? sourceDirName.Substring(0, dash) : task.character;
                string animKey = dash >= 0 ? sourceDirName.Substring(dash + 1) : "";
                string charLower = charKey.ToLowerInvariant();
                string animLower = animKey.ToLowerInvariant();
                string charFolder = charKey.Length > 0 ? char.ToUpperInvariant(charKey[0]) + charKey.Substring(1) : charKey;
                string prefix = GetTexturePrefix(task.category);
                string iterSubDir = task.category == AnimTextureImportModule.ECategory.Story ? "Chat" : "Common";
                string targetAssetDir = task.category == AnimTextureImportModule.ECategory.CharacterAnim
                    ? Combine(module.characterAnimBasePath, charFolder, iterSubDir)
                    : Combine(module.emojiBasePath, charFolder, iterSubDir);
                string targetFullDir = ToFullPath(targetAssetDir);
                foreach (var file in Directory.EnumerateFiles(src).Where(IsTexture).OrderBy(f => f))
                {{
                    var seq = ExtractNormalizedSequence(Path.GetFileNameWithoutExtension(file));
                    if (seq == null)
                    {{
                        skippedFiles++;
                        continue;
                    }}
                    var targetFileName = $"{{prefix}}{{charLower}}_{{animLower}}_{{seq}}.png";
                    var destFull = Path.Combine(targetFullDir, targetFileName);
                    if (!File.Exists(destFull))
                    {{
                        skippedFiles++;
                        continue;
                    }}
                    File.Copy(file, destFull, true);
                    imported.Add(targetAssetDir.TrimEnd('/') + "/" + targetFileName);
                    replacedFiles++;
                }}
                taskOk++;
            }}
        }}
        finally
        {{
            AssetDatabase.StopAssetEditing();
            AssetDatabase.Refresh();
        }}

        applySettings.Invoke(module, new object[] {{ imported }});
        writeManifest.Invoke(module, new object[] {{ imported, new List<string>(), new List<string>() }});
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();
        Debug.Log($"[CodexAnimImportApiRunner] {{pkg}} iteration tasks={{taskOk}} replaced={{replacedFiles}} skipped={{skippedFiles}}");
        return new JObject
        {{
            ["package"] = pkg,
            ["mode"] = "iteration",
            ["tasksProcessed"] = taskOk,
            ["textures"] = replacedFiles,
            ["replacedTextures"] = replacedFiles,
            ["skippedTextures"] = skippedFiles,
            ["createdOverrideControllers"] = 0,
            ["createdAnimationClips"] = 0
        }};
    }}

    private static void LoadTasks(AnimTextureImportModule module, string jsonPath, string framesRoot)
    {{
        var token = JToken.Parse(File.ReadAllText(jsonPath));
        var items = token["items"] as JObject;
        if (items == null) throw new Exception("Missing items in " + jsonPath);
        foreach (var charProp in items.Properties())
        {{
            var anims = charProp.Value as JObject;
            if (anims == null) continue;
            foreach (var animProp in anims.Properties())
            {{
                var data = animProp.Value as JObject;
                var types = data?["types"] as JArray;
                if (types == null) continue;
                var rel = charProp.Name + "-" + animProp.Name;
                foreach (var typeToken in types)
                {{
                    module.tasks.Add(new AnimTextureImportModule.ImportTask
                    {{
                        character = charProp.Name,
                        category = ParseCategory(typeToken.ToString()),
                        sourceDir = rel
                    }});
                }}
            }}
        }}
    }}

    private static AnimTextureImportModule.ECategory ParseCategory(string raw)
    {{
        var text = (raw ?? "").ToLowerInvariant();
        if (text.Contains("角色") || text.Contains("character")) return AnimTextureImportModule.ECategory.CharacterAnim;
        if (text.Contains("剧情") || text.Contains("story") || text.Contains("chat")) return AnimTextureImportModule.ECategory.Story;
        return AnimTextureImportModule.ECategory.Order;
    }}

    private static bool IsTexture(string path)
    {{
        var ext = Path.GetExtension(path).ToLowerInvariant();
        return ext == ".png" || ext == ".jpg" || ext == ".jpeg" || ext == ".tga" || ext == ".psd" || ext == ".bmp" || ext == ".gif";
    }}

    private static string GetTexturePrefix(AnimTextureImportModule.ECategory cat)
    {{
        if (cat == AnimTextureImportModule.ECategory.CharacterAnim) return "spch_full_";
        if (cat == AnimTextureImportModule.ECategory.Story) return "spch_chat_";
        return "spch_common_";
    }}

    private static string ExtractNormalizedSequence(string nameWithoutExt)
    {{
        if (string.IsNullOrEmpty(nameWithoutExt)) return null;
        if (int.TryParse(nameWithoutExt, out int n)) return n < 10 ? $"0{{n}}" : n.ToString();
        int lastUnderscore = nameWithoutExt.LastIndexOf('_');
        if (lastUnderscore >= 0 && int.TryParse(nameWithoutExt.Substring(lastUnderscore + 1), out int m))
            return m < 10 ? $"0{{m}}" : m.ToString();
        return null;
    }}

    private static string Combine(params string[] parts)
    {{
        return string.Join("/", parts.Where(p => !string.IsNullOrWhiteSpace(p)).Select(p => p.Trim().Trim('/', '\\\\')));
    }}

    private static string ToFullPath(string assetPath)
    {{
        return Path.GetFullPath(Path.Combine(Application.dataPath, "..", assetPath));
    }}

    private static void WriteResult(JObject payload)
    {{
        Directory.CreateDirectory(Path.GetDirectoryName(ResultPath));
        File.WriteAllText(ResultPath, payload.ToString(), new System.Text.UTF8Encoding(false));
    }}

    private static void WriteStatus(JObject payload)
    {{
        Directory.CreateDirectory(Path.GetDirectoryName(StatusPath));
        File.WriteAllText(StatusPath, payload.ToString(), new System.Text.UTF8Encoding(false));
    }}
}}
'''
