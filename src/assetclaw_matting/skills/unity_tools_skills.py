from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from assetclaw_matting.skills.security import validate_path
from assetclaw_matting.skills.unity_import_skills import DEFAULT_MCP_URL, DEFAULT_UNITY_PROJECT, _probe_mcp


RUNNER_RELATIVE = "Assets/Editor/CodexUnityToolsApiRunner.cs"
REQUEST_RELATIVE = "Temp/CodexUnityToolsApiRequest.json"
RESULT_RELATIVE = "Temp/CodexUnityToolsApiResult.json"
ATLAS_REPORT_RELATIVE = "Assets/TATest/AtlasSizeReport.json"


def atlas_status(
    unity_project: str | None = None,
    mcp_url: str = DEFAULT_MCP_URL,
    **_: Any,
) -> dict[str, Any]:
    project = validate_path(unity_project or DEFAULT_UNITY_PROJECT, must_exist=False)
    report_path = project / ATLAS_REPORT_RELATIVE
    payload: dict[str, Any] = {
        "ok": True,
        "operation": "atlas_status",
        "unity_project": str(project),
        "report_path": str(report_path),
        "report_exists": report_path.is_file(),
        "api": _probe_mcp(mcp_url),
    }
    if report_path.is_file():
        try:
            report = json.loads(report_path.read_text(encoding="utf-8-sig"))
            payload["report"] = _summarize_atlas_report(report)
        except Exception as exc:
            payload["ok"] = False
            payload["error"] = f"failed to read report: {exc}"
    return payload


def atlas_report(
    unity_project: str | None = None,
    mcp_url: str = DEFAULT_MCP_URL,
    timeout_seconds: int = 900,
    **_: Any,
) -> dict[str, Any]:
    project = validate_path(unity_project or DEFAULT_UNITY_PROJECT, must_exist=True)
    api = _probe_mcp(mcp_url)
    if not api.get("available"):
        return {
            "ok": False,
            "operation": "atlas_report",
            "unity_project": str(project),
            "api": api,
            "error": "unity_mcp_off",
            "message": "Unity MCP/API is not reachable; atlas report generation refused.",
        }
    return _run_unity_tool(
        project,
        {
            "operation": "atlas_report",
            "createdAt": time.time(),
        },
        timeout_seconds=timeout_seconds,
    )


def rename_preview(
    texture_folder: str,
    animation_folder: str,
    unity_project: str | None = None,
    mcp_url: str = DEFAULT_MCP_URL,
    timeout_seconds: int = 600,
    **_: Any,
) -> dict[str, Any]:
    return _rename_tool(
        "rename_preview",
        texture_folder=texture_folder,
        animation_folder=animation_folder,
        unity_project=unity_project,
        mcp_url=mcp_url,
        timeout_seconds=timeout_seconds,
    )


def rename_run(
    texture_folder: str,
    animation_folder: str,
    unity_project: str | None = None,
    mcp_url: str = DEFAULT_MCP_URL,
    timeout_seconds: int = 900,
    **_: Any,
) -> dict[str, Any]:
    return _rename_tool(
        "rename_run",
        texture_folder=texture_folder,
        animation_folder=animation_folder,
        unity_project=unity_project,
        mcp_url=mcp_url,
        timeout_seconds=timeout_seconds,
    )


def _rename_tool(
    operation: str,
    *,
    texture_folder: str,
    animation_folder: str,
    unity_project: str | None,
    mcp_url: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    project = validate_path(unity_project or DEFAULT_UNITY_PROJECT, must_exist=True)
    texture = _asset_folder(texture_folder)
    animation = _asset_folder(animation_folder)
    api = _probe_mcp(mcp_url)
    if not api.get("available"):
        return {
            "ok": False,
            "operation": operation,
            "unity_project": str(project),
            "texture_folder": texture,
            "animation_folder": animation,
            "api": api,
            "error": "unity_mcp_off",
            "message": "Unity MCP/API is not reachable; rename tool refused.",
        }
    return _run_unity_tool(
        project,
        {
            "operation": operation,
            "textureFolder": texture,
            "animationFolder": animation,
            "createdAt": time.time(),
        },
        timeout_seconds=timeout_seconds,
    )


def preview_rename_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    return "\n".join(
        [
            "请确认是否执行 Unity 动画贴图批量命名整理：",
            f"贴图目录：{arguments.get('texture_folder') or arguments.get('textureFolder')}",
            f"动画目录：{arguments.get('animation_folder') or arguments.get('animationFolder')}",
            "动作：扫描预览无错误后才会应用重命名，并生成 RenameManifest。",
            "主流程：不会触发完整 7 步动画自动化流程。",
            f"回复：确认执行 {confirmation_id}",
        ]
    )


def preview_atlas_report_confirmation(arguments: dict[str, Any], confirmation_id: str) -> str:
    return "\n".join(
        [
            "请确认是否执行 Unity 图集大小检查：",
            f"Unity Project：{arguments.get('unity_project') or DEFAULT_UNITY_PROJECT}",
            f"报告输出：{ATLAS_REPORT_RELATIVE}",
            "动作：临时生成 SpriteAtlas、统计 ASTC_6x6 大小、清理临时图集，仅保留 JSON 报告。",
            "主流程：不会触发完整 7 步动画自动化流程。",
            f"回复：确认执行 {confirmation_id}",
        ]
    )


def _run_unity_tool(project_root: Path, request: dict[str, Any], *, timeout_seconds: int) -> dict[str, Any]:
    request_path = project_root / REQUEST_RELATIVE
    result_path = project_root / RESULT_RELATIVE
    runner_path = project_root / RUNNER_RELATIVE
    meta_path = Path(str(runner_path) + ".meta")
    cleanup_runner = False
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
                cleanup_runner = True
                report = payload.get("atlasReport")
                if isinstance(report, dict):
                    payload["atlasReport"] = _summarize_atlas_report(report)
                return {
                    "ok": bool(payload.get("ok")),
                    "operation": request.get("operation"),
                    "unity_project": str(project_root),
                    "runner": str(runner_path),
                    "result": payload,
                    "error": payload.get("error") or "",
                }
            time.sleep(1.0)
        return {
            "ok": False,
            "operation": request.get("operation"),
            "unity_project": str(project_root),
            "runner": str(runner_path),
            "request": str(request_path),
            "result_path": str(result_path),
            "error": "unity_runner_timeout",
            "message": f"Unity did not produce {result_path} within {timeout_seconds}s; runner/request kept for late completion.",
        }
    finally:
        if cleanup_runner:
            for path in (runner_path, meta_path, request_path):
                try:
                    path.unlink(missing_ok=True)
                except OSError:
                    pass


def _asset_folder(value: str) -> str:
    raw = str(value or "").replace("\\", "/").strip()
    if not raw:
        raise ValueError("asset folder is required")
    if raw.startswith("Assets/"):
        return raw.rstrip("/")
    marker = "/Assets/"
    if marker in raw:
        return ("Assets/" + raw.split(marker, 1)[1]).rstrip("/")
    raise ValueError("Unity folder must be under Assets/: " + value)


def _summarize_atlas_report(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("categorySummary") or {}
    return {
        "generatedAt": report.get("generatedAt") or "",
        "compressionFormat": report.get("compressionFormat") or "",
        "totalAtlases": report.get("totalAtlases", 0),
        "totalSprites": report.get("totalSprites", 0),
        "totalEstimatedSizeKB": report.get("totalEstimatedSizeKB", 0),
        "totalEstimatedSizeMB": report.get("totalEstimatedSizeMB", 0),
        "categorySummary": summary,
        "atlasPreview": (report.get("atlases") or [])[:10],
    }


def _runner_source(request_path: Path, result_path: Path) -> str:
    request = str(request_path).replace("\\", "\\\\")
    result = str(result_path).replace("\\", "\\\\")
    return f'''// Auto-generated by assetclaw_matting unity_tools.*. Do not commit.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using Newtonsoft.Json.Linq;
using UnityEditor;
using UnityEngine;

[InitializeOnLoad]
public static class CodexUnityToolsApiRunner
{{
    private const string RequestPath = "{request}";
    private const string ResultPath = "{result}";

    static CodexUnityToolsApiRunner()
    {{
        EditorApplication.delayCall += RunOnce;
    }}

    private static void RunOnce()
    {{
        try
        {{
            if (!File.Exists(RequestPath)) return;
            var req = JObject.Parse(File.ReadAllText(RequestPath));
            var op = req.Value<string>("operation") ?? "";
            JObject result;
            if (op == "atlas_report") result = RunAtlasReport();
            else if (op == "rename_preview") result = RunRename(req, false);
            else if (op == "rename_run") result = RunRename(req, true);
            else throw new Exception("Unknown operation: " + op);
            result["ok"] = true;
            result["operation"] = op;
            result["finishedAt"] = DateTime.Now.ToString("o");
            WriteResult(result);
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

    private static JObject RunAtlasReport()
    {{
        var type = FindType("SpriteAtlasGeneratorTool");
        if (type == null) throw new Exception("SpriteAtlasGeneratorTool not found.");
        var logs = new JArray();
        var logAction = new Action<string>(msg => logs.Add(msg));
        var method = type.GetMethod("DoGenerate", BindingFlags.Public | BindingFlags.Static);
        if (method == null) throw new Exception("SpriteAtlasGeneratorTool.DoGenerate not found.");
        method.Invoke(null, new object[] {{ logAction, null }});
        var reportPath = "Assets/TATest/AtlasSizeReport.json";
        var full = Path.GetFullPath(reportPath);
        JObject report = File.Exists(full) ? JObject.Parse(File.ReadAllText(full)) : new JObject();
        return new JObject
        {{
            ["reportPath"] = reportPath,
            ["reportFullPath"] = full,
            ["logs"] = logs,
            ["atlasReport"] = report
        }};
    }}

    private static JObject RunRename(JObject req, bool apply)
    {{
        var texPath = req.Value<string>("textureFolder") ?? "";
        var animPath = req.Value<string>("animationFolder") ?? "";
        if (!AssetDatabase.IsValidFolder(texPath)) throw new Exception("贴图文件夹无效: " + texPath);
        if (!AssetDatabase.IsValidFolder(animPath)) throw new Exception("动画文件夹无效: " + animPath);
        var type = FindType("Uep.Utility.AnimTextureBatchRename");
        if (type == null) throw new Exception("Uep.Utility.AnimTextureBatchRename not found.");
        var w = EditorWindow.GetWindow(type);
        const BindingFlags F = BindingFlags.NonPublic | BindingFlags.Instance;
        type.GetField("_textureFolder", F).SetValue(w, AssetDatabase.LoadAssetAtPath<DefaultAsset>(texPath));
        type.GetField("_animationFolder", F).SetValue(w, AssetDatabase.LoadAssetAtPath<DefaultAsset>(animPath));
        type.GetMethod("ScanAndBuildPreview", F).Invoke(w, null);

        var rowType = type.GetNestedType("RenamePreviewRow", BindingFlags.NonPublic);
        var errorField = rowType.GetField("Error");
        var oldField = rowType.GetField("OldPath");
        var newField = rowType.GetField("NewPath");
        var keywordField = rowType.GetField("Keyword");
        var seqField = rowType.GetField("Sequence");
        var listType = typeof(List<>).MakeGenericType(rowType);
        var addMethod = listType.GetMethod("Add");
        int errorCount = 0;
        var errors = new JArray();
        Func<string, object> collectValid = (fieldName) =>
        {{
            var src = type.GetField(fieldName, F).GetValue(w) as System.Collections.IEnumerable;
            var dst = Activator.CreateInstance(listType);
            if (src != null)
                foreach (var row in src)
                {{
                    var ev = errorField.GetValue(row) as string;
                    if (string.IsNullOrEmpty(ev)) addMethod.Invoke(dst, new[] {{ row }});
                    else {{ errorCount++; if (errors.Count < 20) errors.Add(ev); }}
                }}
            return dst;
        }};
        var displacement = collectValid("_unreferencedRenamePreview");
        var animRows = collectValid("_renamePreview");
        int displacementCount = (int)listType.GetProperty("Count").GetValue(displacement);
        int animCount = (int)listType.GetProperty("Count").GetValue(animRows);

        var preview = new JArray();
        Action<object> addPreview = row =>
        {{
            if (preview.Count >= 30) return;
            preview.Add(new JObject
            {{
                ["oldPath"] = oldField.GetValue(row)?.ToString() ?? "",
                ["newPath"] = newField.GetValue(row)?.ToString() ?? "",
                ["keyword"] = keywordField?.GetValue(row)?.ToString() ?? "",
                ["sequence"] = seqField?.GetValue(row)?.ToString() ?? ""
            }});
        }};
        foreach (var row in (System.Collections.IEnumerable)displacement) addPreview(row);
        foreach (var row in (System.Collections.IEnumerable)animRows) addPreview(row);

        if (!apply)
        {{
            return new JObject
            {{
                ["textureFolder"] = texPath,
                ["animationFolder"] = animPath,
                ["apply"] = false,
                ["errorCount"] = errorCount,
                ["displacementCount"] = displacementCount,
                ["animationRenameCount"] = animCount,
                ["preview"] = preview,
                ["errors"] = errors
            }};
        }}
        if (errorCount > 0) throw new Exception("扫描发现错误项，已中止: " + string.Join("; ", errors.Select(x => x.ToString()).Take(5)));
        if (displacementCount + animCount <= 0)
        {{
            return new JObject
            {{
                ["textureFolder"] = texPath,
                ["animationFolder"] = animPath,
                ["apply"] = true,
                ["message"] = "无可重命名项",
                ["displacementCount"] = 0,
                ["animationRenameCount"] = 0
            }};
        }}
        var targets = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var lst in new[] {{ displacement, animRows }})
            foreach (var row in (System.Collections.IEnumerable)lst)
            {{
                var np = newField.GetValue(row) as string;
                if (!targets.Add(np)) throw new Exception("目标路径重复: " + np);
            }}
        var preflight = (bool)type.GetMethod("PreflightCheckApplyRenames", F).Invoke(w, new object[] {{ displacement, animRows }});
        if (!preflight) throw new Exception("应用前体检未通过");
        var moveArgs = new object[] {{ "阶段 1 占位回退", displacement, 0 }};
        if (!(bool)type.GetMethod("RunMoveBatch", F).Invoke(w, moveArgs)) throw new Exception("阶段 1 占位回退失败");
        var animArgs = new object[] {{ "阶段 2 动画引用", animRows, 0 }};
        if (!(bool)type.GetMethod("RunAnimRenameInDependencyOrder", F).Invoke(w, animArgs)) throw new Exception("阶段 2 动画引用重命名失败");
        AssetDatabase.Refresh();
        var manifestPath = (string)type.GetMethod("WriteManifest", F).Invoke(w, new object[] {{ displacement, animRows }});
        AssetDatabase.SaveAssets();
        AssetDatabase.Refresh();
        return new JObject
        {{
            ["textureFolder"] = texPath,
            ["animationFolder"] = animPath,
            ["apply"] = true,
            ["displacementDone"] = (int)moveArgs[2],
            ["animationRenameDone"] = (int)animArgs[2],
            ["manifestPath"] = manifestPath ?? "",
            ["preview"] = preview
        }};
    }}

    private static Type FindType(string fullName)
    {{
        return AppDomain.CurrentDomain.GetAssemblies()
            .SelectMany(a => {{ try {{ return a.GetTypes(); }} catch {{ return new Type[0]; }} }})
            .FirstOrDefault(t => t.FullName == fullName || t.Name == fullName);
    }}

    private static void WriteResult(JObject payload)
    {{
        Directory.CreateDirectory(Path.GetDirectoryName(ResultPath));
        File.WriteAllText(ResultPath, payload.ToString(), System.Text.Encoding.UTF8);
        AssetDatabase.Refresh();
    }}
}}
'''
