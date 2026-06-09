from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from assetclaw_matting.skills.frame_skills import default_automation_paths
from assetclaw_matting.skills.security import validate_path


DEFAULT_UNITY_PROJECT = "D:/Spark/Client"
DEFAULT_MCP_URL = "http://127.0.0.1:8080/mcp"
RUNNER_RELATIVE = "Assets/Editor/CodexAnimImportApiRunner.cs"
REQUEST_RELATIVE = "Temp/CodexAnimImportApiRequest.json"
RESULT_RELATIVE = "Temp/CodexAnimImportApiResult.json"


def preview(
    unity_ready: str,
    unity_project: str | None = None,
    package: str = "both",
    mcp_url: str = DEFAULT_MCP_URL,
    **_: Any,
) -> dict[str, Any]:
    ready_root = validate_path(unity_ready, must_exist=True)
    project_root = validate_path(unity_project or DEFAULT_UNITY_PROJECT, must_exist=True)
    packages = [_package_summary(ready_root, item) for item in _selected_packages(package)]
    api = _probe_mcp(mcp_url)
    return {
        "ok": True,
        "operation": "preview",
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
    mcp_url: str = DEFAULT_MCP_URL,
    timeout_seconds: int = 180,
    **_: Any,
) -> dict[str, Any]:
    plan = preview(unity_ready=unity_ready, unity_project=unity_project, package=package, mcp_url=mcp_url)
    if not plan.get("api", {}).get("available"):
        return {
            **plan,
            "ok": False,
            "operation": "import",
            "error": "unity_mcp_off",
            "message": "Unity MCP/API is not reachable; import is refused instead of using UI clicking or unmanaged fallbacks.",
        }
    project_root = Path(plan["unity_project"])
    request_path = project_root / REQUEST_RELATIVE
    result_path = project_root / RESULT_RELATIVE
    runner_path = project_root / RUNNER_RELATIVE
    meta_path = Path(str(runner_path) + ".meta")
    request = {
        "unityReady": plan["unity_ready"],
        "projectRoot": plan["unity_project"],
        "packages": [item["package"] for item in plan["packages"]],
        "createdAt": time.time(),
    }
    try:
        result_path.unlink(missing_ok=True)
        request_path.parent.mkdir(parents=True, exist_ok=True)
        request_path.write_text(json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8")
        runner_path.parent.mkdir(parents=True, exist_ok=True)
        runner_path.write_text(_runner_source(request_path, result_path), encoding="utf-8")

        deadline = time.time() + max(30, int(timeout_seconds))
        while time.time() < deadline:
            if result_path.is_file():
                payload = json.loads(result_path.read_text(encoding="utf-8-sig"))
                return {
                    **plan,
                    "ok": bool(payload.get("ok")),
                    "error": payload.get("error") or "",
                    "operation": "import",
                    "runner": str(runner_path),
                    "result": payload,
                }
            time.sleep(1.0)
        return {
            **plan,
            "ok": False,
            "error": "unity_runner_timeout",
            "operation": "import",
            "runner": str(runner_path),
            "message": f"Unity did not produce {result_path} within {timeout_seconds}s. Check Unity compile state.",
        }
    finally:
        for path in (runner_path, meta_path, request_path):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass


def status(
    unity_ready: str | None = None,
    unity_project: str | None = None,
    package: str = "both",
    mcp_url: str = DEFAULT_MCP_URL,
    **kwargs: Any,
) -> dict[str, Any]:
    ready = unity_ready or str(Path(default_automation_paths()["workspace_root"]) / "unity_ready")
    try:
        return preview(unity_ready=ready, unity_project=unity_project, package=package, mcp_url=mcp_url, **kwargs)
    except (FileNotFoundError, ValueError) as exc:
        return {
            "ok": True,
            "unity_ready": ready,
            "unity_project": str(validate_path(unity_project or DEFAULT_UNITY_PROJECT, must_exist=False)),
            "package": package,
            "can_import_now": False,
            "error": str(exc),
            "message": "unity_ready is not ready yet.",
        }


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
    if not frames_root.is_dir():
        raise FileNotFoundError(str(frames_root))
    data = json.loads(json_path.read_text(encoding="utf-8"))
    tasks = []
    frame_count = 0
    for character, animations in (data.get("items") or {}).items():
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
        "task_count": len(tasks),
        "frame_count": frame_count,
        "tasks": tasks,
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


def _runner_source(request_path: Path, result_path: Path) -> str:
    request = str(request_path).replace("\\", "\\\\")
    result = str(result_path).replace("\\", "\\\\")
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
            var unityReady = req.Value<string>("unityReady");
            var packages = (req["packages"] as JArray)?.Select(x => x.ToString()).ToList() ?? new List<string>();
            var totals = new JArray();
            foreach (var pkg in packages)
            {{
                totals.Add(ImportPackage(unityReady, pkg));
            }}
            WriteResult(new JObject
            {{
                ["ok"] = true,
                ["packages"] = totals,
                ["finishedAt"] = DateTime.Now.ToString("o")
            }});
        }}
        catch (Exception ex)
        {{
            WriteResult(new JObject
            {{
                ["ok"] = false,
                ["error"] = ex.ToString(),
                ["finishedAt"] = DateTime.Now.ToString("o")
            }});
        }}
    }}

    private static JObject ImportPackage(string unityReady, string pkg)
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
            ["tasksProcessed"] = taskOk,
            ["textures"] = fileCount,
            ["createdOverrideControllers"] = createdOc.Count,
            ["createdAnimationClips"] = createdAnim.Count
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

    private static string ToFullPath(string assetPath)
    {{
        return Path.GetFullPath(Path.Combine(Application.dataPath, "..", assetPath));
    }}

    private static void WriteResult(JObject payload)
    {{
        Directory.CreateDirectory(Path.GetDirectoryName(ResultPath));
        File.WriteAllText(ResultPath, payload.ToString(), new System.Text.UTF8Encoding(false));
    }}
}}
'''
