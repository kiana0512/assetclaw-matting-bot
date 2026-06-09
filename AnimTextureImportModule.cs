using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using Newtonsoft.Json.Linq;
using Sirenix.OdinInspector;
using UnityEditor;
using UnityEngine;
  
namespace SparkTools.Editor.AnimTextureImporter
{
    /// <summary>
    /// 动画贴图批量导入核心模块：配置路径 → 加载任务(JSON/手动) → 计算目标目录 → 拷贝并设置贴图。
    /// </summary>
    public class AnimTextureImportModule : EditorBrickModule
    {
        private const string ManifestFolderRelative = "Assets/Modules/UepUtility/AnimTextureImporter/Editor/Manifests";

        /// <summary>任务类型，决定落到哪个引擎子目录。</summary>
        public enum ECategory
        {
            [LabelText("订单 (Emoji/Common)")] Order,
            [LabelText("剧情 (Emoji/Chat)")] Story,
            [LabelText("角色动画 (CharacterAnim)")] CharacterAnim,
        }

        /// <summary>单条导入任务。</summary>
        [Serializable]
        public class ImportTask
        {
            [TableColumnWidth(120)]
            [LabelText("角色名")]
            public string character = "";

            [TableColumnWidth(160)]
            [LabelText("类型")]
            public ECategory category = ECategory.Order;

            [LabelText("源贴图目录")]
            [Tooltip("绝对路径，或相对「源根目录」的子路径；留空则直接用「源根目录」。")]
            public string sourceDir = "";
        }

        // ───────────────────────── 路径配置（开放可改） ─────────────────────────

        [FoldoutGroup("① 目标路径配置", expanded: true)]
        [LabelText("Emoji 基础目录 (订单/剧情)")]
        [FolderPath]
        public string emojiBasePath = "Assets/Art/UI/SpritesAnim/Emoji";

        [FoldoutGroup("① 目标路径配置")]
        [LabelText("CharacterAnim 基础目录 (角色动画)")]
        [FolderPath]
        public string characterAnimBasePath = "Assets/Art/UI/SpritesAnim/CharacterAnim";

        [FoldoutGroup("① 目标路径配置")]
        [HorizontalGroup("① 目标路径配置/sub")]
        [LabelText("订单子目录")]
        public string orderSubFolder = "importing";

        [FoldoutGroup("① 目标路径配置")]
        [HorizontalGroup("① 目标路径配置/sub")]
        [LabelText("剧情子目录")]
        public string storySubFolder = "importing";

        [FoldoutGroup("① 目标路径配置")]
        [HorizontalGroup("① 目标路径配置/sub")]
        [LabelText("角色动画子目录")]
        public string charAnimSubFolder = "charImproting";

        // ───────────────────────── 贴图导入设置 ─────────────────────────

        [FoldoutGroup("② 贴图导入设置", expanded: true)]
        [LabelText("设为 Sprite (2D and UI)")]
        public bool setSpriteType = true;

        [FoldoutGroup("② 贴图导入设置")]
        [LabelText("Default MaxSize")]
        public int defaultMaxSize = 256;

        [FoldoutGroup("② 贴图导入设置")]
        [LabelText("Android 勾选 Override")]
        public bool overrideAndroid = true;

        [FoldoutGroup("② 贴图导入设置")]
        [LabelText("Android MaxSize")]
        [EnableIf(nameof(overrideAndroid))]
        public int androidMaxSize = 256;

        [FoldoutGroup("② 贴图导入设置")]
        [LabelText("覆盖目标目录同名贴图")]
        public bool overwriteExisting = true;

        [FoldoutGroup("② 贴图导入设置")]
        [LabelText("新建 Animation Clip 帧率")]
        [Tooltip("新建 AnimationClip 时自动填充关键帧所用帧率（fps）。")]
        public float animClipFrameRate = 12f;

        // ───────────────────────── JSON 导入 ─────────────────────────

        [FoldoutGroup("③ 从多维表格 JSON 加载", expanded: true)]
        [InfoBox("JSON 支持顶层数组或 {\"tasks\":[...]}。每条字段名兼容：\n" +
                 "角色：character / 角色 / 角色名 / name；\n" +
                 "类型：category / 类型 / 动画类型（值可为 订单 / 剧情 / 角色动画 / 表情动画 / 场景动画）；\n" +
                 "源目录：sourceDir / 源目录 / 源路径 / path / dir（可选，相对「源根目录」或绝对路径）。")]
        [LabelText("JSON 文件")]
        [Sirenix.OdinInspector.FilePath(Extensions = "json", AbsolutePath = true)]
        public string jsonFilePath = "";

        [FoldoutGroup("③ 从多维表格 JSON 加载")]
        [LabelText("源根目录 (源路径开放出来)")]
        [Tooltip("任务里 sourceDir 为相对路径或留空时，以此目录为基准定位贴图。")]
        [FolderPath(AbsolutePath = true)]
        public string sourceRoot = "";

        [FoldoutGroup("③ 从多维表格 JSON 加载")]
        [Button("从 JSON 加载任务", ButtonSizes.Medium), GUIColor(0.7f, 0.9f, 1f)]
        public void LoadFromJson()
        {
            if (string.IsNullOrEmpty(jsonFilePath) || !File.Exists(jsonFilePath))
            {
                EditorUtility.DisplayDialog("加载失败", "请先选择有效的 JSON 文件。", "知道了");
                return;
            }

            tasks.Clear();

            try
            {
                var text = File.ReadAllText(jsonFilePath, new System.Text.UTF8Encoding(false)).TrimStart('﻿').Trim();
                // 兼容缺外层花括号的导出格式（文件直接以 "key": 开头）
                if (text.StartsWith("\""))
                {
                    text = "{" + text + "}";
                }
                using var jsonReader = new Newtonsoft.Json.JsonTextReader(new System.IO.StringReader(text));
                var token = JToken.Load(jsonReader);

                int added = 0, skipped = 0;

                // 格式一：{ "items": { "角色": { "动画": { "name":"...", "types":["订单","剧情"] } } } }
                if (token is JObject root && root["items"] is JObject itemsObj)
                {
                    foreach (var charProp in itemsObj.Properties())
                    {
                        string charKey = charProp.Name;
                        if (charProp.Value is not JObject animDict) continue;

                        foreach (var animProp in animDict.Properties())
                        {
                            string animKey = animProp.Name;
                            if (animProp.Value is not JObject animData) continue;

                            var typesArr = animData["types"] as JArray;
                            if (typesArr == null || typesArr.Count == 0) { skipped++; continue; }

                            var relDir = $"{charKey}-{animKey}";
                            var absDir = FindDeepestDir(sourceRoot, relDir);
                            if (absDir == null) { skipped++; continue; }

                            foreach (var typeToken in typesArr)
                            {
                                var categoryRaw = typeToken.ToString();
                                if (!TryParseCategory(categoryRaw, out var category))
                                {
                                    Debug.LogWarning($"[动画贴图导入] 跳过 {charKey}-{animKey}：不支持的类型值 \"{categoryRaw}\"");
                                    skipped++;
                                    continue;
                                }

                                tasks.Add(new ImportTask
                                {
                                    character = charKey.Trim(),
                                    category = category,
                                    sourceDir = relDir,
                                });
                                added++;
                            }
                        }
                    }
                }
                else
                {
                    // 格式二：顶层数组 或 { "tasks": [...] }
                    var arr = token is JObject obj && obj["tasks"] != null ? obj["tasks"] as JArray : token as JArray;
                    if (arr == null)
                    {
                        EditorUtility.DisplayDialog("加载失败", "不支持的 JSON 格式。\n支持：① { \"items\":{...} }  ② 顶层数组  ③ { \"tasks\":[...] }", "知道了");
                        return;
                    }

                    foreach (var item in arr.OfType<JObject>())
                    {
                        var character = FirstString(item, "character", "角色", "角色名", "name");
                        var categoryRaw = FirstString(item, "category", "类型", "动画类型");
                        var src = FirstString(item, "sourceDir", "源目录", "源路径", "path", "dir");

                        if (string.IsNullOrWhiteSpace(character))
                        {
                            Debug.LogWarning($"[动画贴图导入] 跳过一条任务：缺少角色名（category={categoryRaw}）");
                            skipped++;
                            continue;
                        }
                        if (!TryParseCategory(categoryRaw, out var category))
                        {
                            Debug.LogWarning($"[动画贴图导入] 跳过 {character}：不支持的类型值 \"{categoryRaw}\"");
                            skipped++;
                            continue;
                        }

                        tasks.Add(new ImportTask
                        {
                            character = character.Trim(),
                            category = category,
                            sourceDir = src?.Trim() ?? "",
                        });
                        added++;
                    }
                }

                Center.RefreshGUI();
                EditorUtility.DisplayDialog("加载完成", $"新增 {added} 条任务，跳过 {skipped} 条（缺角色名或类型无法识别）。", "好的");
            }
            catch (Exception e)
            {
                Debug.LogException(e);
                EditorUtility.DisplayDialog("加载失败", $"解析 JSON 出错：\n{e.Message}", "知道了");
            }
        }

        // ───────────────────────── 手动添加 ─────────────────────────

        [FoldoutGroup("④ 手动添加任务", expanded: true)]
        [LabelText("角色名")]
        public string manualCharacter = "";

        [FoldoutGroup("④ 手动添加任务")]
        [LabelText("类型")]
        public ECategory manualCategory = ECategory.Order;

        [FoldoutGroup("④ 手动添加任务")]
        [LabelText("源贴图目录")]
        [FolderPath(AbsolutePath = true)]
        public string manualSourceDir = "";

        [FoldoutGroup("④ 手动添加任务")]
        [Button("添加到任务列表", ButtonSizes.Medium), GUIColor(0.7f, 1f, 0.7f)]
        public void AddManualTask()
        {
            if (string.IsNullOrWhiteSpace(manualCharacter))
            {
                EditorUtility.DisplayDialog("添加失败", "请填写角色名。", "知道了");
                return;
            }

            tasks.Add(new ImportTask
            {
                character = manualCharacter.Trim(),
                category = manualCategory,
                sourceDir = manualSourceDir?.Trim() ?? "",
            });
            Center.RefreshGUI();
        }

        // ───────────────────────── 任务列表 ─────────────────────────

        [FoldoutGroup("⑤ 任务列表", expanded: true)]
        [TableList(AlwaysExpanded = true, ShowIndexLabels = true)]
        [LabelText("待导入任务")]
        public List<ImportTask> tasks = new();

        [FoldoutGroup("⑤ 任务列表")]
        [HorizontalGroup("⑤ 任务列表/ops")]
        [Button("清空任务"), GUIColor(0.95f, 0.85f, 0.85f)]
        public void ClearTasks()
        {
            tasks.Clear();
            Center.RefreshGUI();
        }

        [FoldoutGroup("⑤ 任务列表")]
        [HorizontalGroup("⑤ 任务列表/ops")]
        [Button("预览目标路径"), GUIColor(0.85f, 0.9f, 1f)]
        public void PreviewTargets()
        {
            if (tasks.Count == 0)
            {
                EditorUtility.DisplayDialog("预览", "任务列表为空。", "知道了");
                return;
            }

            var sb = new System.Text.StringBuilder();
            foreach (var t in tasks)
            {
                var src = ResolveSourceDir(t);
                var target = ResolveTargetDir(t);
                var srcOk = Directory.Exists(src) ? "" : "  [源目录不存在!]";
                sb.AppendLine($"{t.character} / {t.category}");
                sb.AppendLine($"    源 : {src}{srcOk}");
                sb.AppendLine($"    目标: {target}");
            }

            Debug.Log($"[动画贴图导入] 目标路径预览：\n{sb}");
            EditorUtility.DisplayDialog("预览", "已把每条任务的源/目标路径输出到 Console。", "好的");
        }

        // ───────────────────────── 执行导入 ─────────────────────────

        [FoldoutGroup("⑥ 执行")]
        [Button("执行批量导入", ButtonSizes.Large), GUIColor(1f, 0.65f, 0.65f)]
        public void RunImport()
        {
            if (tasks.Count == 0)
            {
                EditorUtility.DisplayDialog("无法执行", "任务列表为空，请先加载或添加任务。", "知道了");
                return;
            }

            // 校验源目录
            var invalid = tasks.Where(t => !Directory.Exists(ResolveSourceDir(t))).ToList();
            if (invalid.Count > 0)
            {
                var names = string.Join("\n", invalid.Select(t => $"  {t.character}/{t.category} → {ResolveSourceDir(t)}"));
                if (!EditorUtility.DisplayDialog("部分源目录不存在",
                        $"以下任务的源目录不存在，将被跳过：\n{names}\n\n是否继续导入其余任务？", "继续", "取消"))
                {
                    return;
                }
            }

            var importedAssetPaths = new List<string>();
            int taskOk = 0, fileCount = 0;

            try
            {
                AssetDatabase.StartAssetEditing();
                for (int i = 0; i < tasks.Count; i++)
                {
                    var t = tasks[i];
                    var src = ResolveSourceDir(t);
                    if (!Directory.Exists(src)) continue;

                    var targetAssetDir = ResolveTargetDir(t);
                    var targetFullDir = ToFullPath(targetAssetDir);

                    var files = EnumerateTextureFiles(src);
                    bool folderEnsured = false;
                    foreach (var file in files)
                    {
                        EditorUtility.DisplayProgressBar("批量导入贴图",
                            $"{t.character}/{t.category} : {Path.GetFileName(file)}",
                            (i + 1f) / tasks.Count);

                        var destFull = Path.Combine(targetFullDir, Path.GetFileName(file));
                        if (File.Exists(destFull) && !overwriteExisting)
                        {
                            continue;
                        }

                        if (!folderEnsured)
                        {
                            EnsureAssetFolder(targetAssetDir);
                            folderEnsured = true;
                        }
                        File.Copy(file, destFull, true);
                        var destAsset = targetAssetDir.TrimEnd('/') + "/" + Path.GetFileName(file);
                        importedAssetPaths.Add(destAsset);
                        fileCount++;
                    }

                    taskOk++;
                }
            }
            finally
            {
                AssetDatabase.StopAssetEditing();
                EditorUtility.ClearProgressBar();
                AssetDatabase.Refresh();
            }

            // 拷贝完成、Refresh 后导入器已存在，统一设置贴图参数
            ApplyTextureSettings(importedAssetPaths);

            Debug.Log($"[动画贴图导入] 完成：处理任务 {taskOk}/{tasks.Count}，导入贴图 {fileCount} 张。");
            EditorUtility.DisplayDialog("导入完成",
                $"处理任务 {taskOk}/{tasks.Count}\n导入贴图 {fileCount} 张\n已统一应用贴图设置。", "好的");

            CheckAndCreateAnimationAssets(out var createdOcPaths, out var createdAnimPaths);
            WriteImportManifest(importedAssetPaths, createdOcPaths, createdAnimPaths);
        }

        // ─────────────────── 资源迭代（直接替换原贴图） ───────────────────

        [FoldoutGroup("⑥ 执行")]
        [Button("执行资源迭代（直接替换原贴图）", ButtonSizes.Large), GUIColor(0.6f, 1f, 0.7f)]
        public void RunIterationImport()
        {
            if (tasks.Count == 0)
            {
                EditorUtility.DisplayDialog("无法执行", "任务列表为空，请先加载或添加任务。", "知道了");
                return;
            }

            var invalid = tasks.Where(t => !Directory.Exists(ResolveSourceDir(t))).ToList();
            if (invalid.Count > 0)
            {
                var names = string.Join("\n", invalid.Select(t => $"  {t.character}/{t.category} → {ResolveSourceDir(t)}"));
                if (!EditorUtility.DisplayDialog("部分源目录不存在",
                        $"以下任务的源目录不存在，将被跳过：\n{names}\n\n是否继续？", "继续", "取消"))
                    return;
            }

            var importedAssetPaths = new List<string>();
            int replacedFiles = 0, skippedFiles = 0;

            try
            {
                AssetDatabase.StartAssetEditing();
                for (int i = 0; i < tasks.Count; i++)
                {
                    var t = tasks[i];
                    var src = ResolveSourceDir(t);
                    if (!Directory.Exists(src)) continue;

                    // 从源目录名（charKey-animKey）提取角色与动画标识
                    string sourceDirName = Path.GetFileName(src.TrimEnd(Path.DirectorySeparatorChar, '/'));
                    int dash = sourceDirName.IndexOf('-');
                    string charKey = dash >= 0 ? sourceDirName.Substring(0, dash) : t.character;
                    string animKey = dash >= 0 ? sourceDirName.Substring(dash + 1) : "";
                    string charLower = charKey.ToLowerInvariant();
                    string animLower = animKey.ToLowerInvariant();
                    string charFolder = charKey.Length > 0
                        ? char.ToUpperInvariant(charKey[0]) + charKey.Substring(1) : charKey;

                    string prefix = GetTexturePrefix(t.category);
                    // 迭代模式固定子目录：订单→Common，剧情→Chat，角色动画沿用配置
                    string iterSubDir = t.category == ECategory.Story ? "Chat"
                        : t.category == ECategory.CharacterAnim ? charAnimSubFolder
                        : "Common";
                    string targetAssetDir = t.category == ECategory.CharacterAnim
                        ? Combine(characterAnimBasePath, charFolder, iterSubDir)
                        : Combine(emojiBasePath, charFolder, iterSubDir);
                    string targetFullDir = ToFullPath(targetAssetDir);

                    var files = EnumerateTextureFiles(src).OrderBy(f => f).ToList();
                    foreach (var file in files)
                    {
                        EditorUtility.DisplayProgressBar("资源迭代导入",
                            $"{t.character}/{t.category} : {Path.GetFileName(file)}",
                            (i + 1f) / tasks.Count);

                        var seq = ExtractNormalizedSequence(Path.GetFileNameWithoutExtension(file));
                        if (seq == null)
                        {
                            Debug.LogWarning($"[动画贴图导入] 无法从文件名提取序号，跳过：{Path.GetFileName(file)}");
                            skippedFiles++;
                            continue;
                        }

                        var targetFileName = $"{prefix}{charLower}_{animLower}_{seq}.png";
                        var destFull = Path.Combine(targetFullDir, targetFileName);
                        if (!File.Exists(destFull))
                        {
                            skippedFiles++;
                            continue;
                        }

                        File.Copy(file, destFull, true);
                        importedAssetPaths.Add(targetAssetDir.TrimEnd('/') + "/" + targetFileName);
                        replacedFiles++;
                    }
                }
            }
            finally
            {
                AssetDatabase.StopAssetEditing();
                EditorUtility.ClearProgressBar();
                AssetDatabase.Refresh();
            }

            ApplyTextureSettings(importedAssetPaths);

            Debug.Log($"[动画贴图导入] 资源迭代完成：替换 {replacedFiles} 张，跳过 {skippedFiles} 张（目标不存在或序号无法解析）。");
            EditorUtility.DisplayDialog("资源迭代完成",
                $"替换贴图：{replacedFiles} 张\n跳过（目标不存在或序号无法解析）：{skippedFiles} 张\n已统一应用贴图设置。", "好的");

            WriteImportManifest(importedAssetPaths, new List<string>(), new List<string>());
        }

        private void ApplyTextureSettings(List<string> assetPaths)
        {
            if (assetPaths.Count == 0) return;

            try
            {
                // 批量修改并排队 reimport；StopAssetEditing 在主线程同步等待所有导入完成
                AssetDatabase.StartAssetEditing();
                for (int i = 0; i < assetPaths.Count; i++)
                {
                    var path = assetPaths[i];
                    EditorUtility.DisplayProgressBar("设置贴图参数",
                        Path.GetFileName(path), (i + 1f) / assetPaths.Count);

                    if (AssetImporter.GetAtPath(path) is not TextureImporter importer)
                        continue;

                    if (setSpriteType)
                    {
                        importer.textureType = TextureImporterType.Sprite;
                        importer.spriteImportMode = SpriteImportMode.Single;
                    }

                    var def = importer.GetDefaultPlatformTextureSettings();
                    def.maxTextureSize = defaultMaxSize;
                    importer.SetPlatformTextureSettings(def);

                    var android = importer.GetPlatformTextureSettings("Android");
                    android.overridden = overrideAndroid;
                    if (overrideAndroid)
                        android.maxTextureSize = androidMaxSize;
                    importer.SetPlatformTextureSettings(android);

                    importer.SaveAndReimport(); // 排入批量队列
                }
            }
            finally
            {
                AssetDatabase.StopAssetEditing(); // 批量处理所有排队的 import，同步等待完成
                EditorUtility.ClearProgressBar();
            }
        }

        // ───────────────────────── 动画资源补全 ─────────────────────────

        /// <summary>导入完成后检查每条任务对应角色的 Override Controller 和 Animation Clip，缺失则新建，并将 clip 挂接到 OC。</summary>
        private void CheckAndCreateAnimationAssets(out List<string> createdOcPaths, out List<string> createdAnimPaths)
        {
            createdOcPaths = new List<string>();
            createdAnimPaths = new List<string>();
            var overrideSeen = new HashSet<string>();
            int overrideCreated = 0, animCreated = 0;
            var log = new System.Text.StringBuilder();

            // ocPath → [(animKey, animPath)]，用于后续挂接（覆盖本次所有有效任务，不只是新建的）
            var ocToAnimClips = new Dictionary<string, List<(string animKey, string animPath)>>(StringComparer.OrdinalIgnoreCase);

            // 新建的 clip 需要在 Refresh 后填充关键帧：(animPath, 贴图目录AssetPath, category)
            var newClipPopulateList = new List<(string animPath, string texDir, ECategory category)>();

            foreach (var t in tasks)
            {
                if (!Directory.Exists(ResolveSourceDir(t))) continue;

                // sourceDir 格式 "{charKey}-{animKey}"，取第一个连字符前后
                int dash = t.sourceDir.IndexOf('-');
                string charKey = dash >= 0 ? t.sourceDir.Substring(0, dash) : t.character;
                string animKey = dash >= 0 ? t.sourceDir.Substring(dash + 1) : "";

                string charLower = charKey.ToLowerInvariant();
                string animLower = animKey.ToLowerInvariant();
                string charFolder = charKey.Length > 0
                    ? char.ToUpperInvariant(charKey[0]) + charKey.Substring(1)
                    : charKey;

                string ocPath = GetOverrideControllerPath(t.category, charLower);

                // Override Controller：同角色 + 类型只建一次
                string ocKey = $"{charLower}_{(int)t.category}";
                if (overrideSeen.Add(ocKey) && !string.IsNullOrEmpty(ocPath) && !AssetFileExists(ocPath))
                {
                    if (TryCreateOverrideController(ocPath, GetBaseControllerPath(t.category)))
                    {
                        createdOcPaths.Add(ocPath);
                        log.AppendLine($"  新建 OC  : {ocPath}");
                        overrideCreated++;
                    }
                }

                // Animation Clip：每条任务对应一个
                if (!string.IsNullOrWhiteSpace(animKey) && !string.IsNullOrEmpty(ocPath))
                {
                    string animPath = GetAnimClipPath(t.category, charFolder, charLower, animLower);
                    if (!string.IsNullOrEmpty(animPath))
                    {
                        if (!AssetFileExists(animPath))
                        {
                            EnsureAssetFolder(Path.GetDirectoryName(animPath).Replace('\\', '/'));
                            AssetDatabase.CreateAsset(new AnimationClip(), animPath);
                            createdAnimPaths.Add(animPath);
                            log.AppendLine($"  新建 Anim: {animPath}");
                            animCreated++;
                            // 仅新建的 clip 才自动填充关键帧
                            newClipPopulateList.Add((animPath, ResolveTargetDir(t), t.category));
                        }

                        // 登记到挂接表（去重，同 OC 同 animKey 只记一次）
                        if (!ocToAnimClips.TryGetValue(ocPath, out var clipList))
                        {
                            clipList = new List<(string, string)>();
                            ocToAnimClips[ocPath] = clipList;
                        }
                        if (!clipList.Any(c => string.Equals(c.animKey, animLower, StringComparison.OrdinalIgnoreCase)))
                            clipList.Add((animLower, animPath));
                    }
                }
            }

            if (overrideCreated > 0 || animCreated > 0)
            {
                AssetDatabase.SaveAssets();
                AssetDatabase.Refresh();

                // 为新建的 clip 自动填充关键帧
                int populatedClips = 0;
                foreach (var (aPath, texDir, cat) in newClipPopulateList)
                {
                    if (PopulateAnimClipKeyframes(aPath, texDir, cat))
                        populatedClips++;
                }
                if (populatedClips > 0)
                {
                    AssetDatabase.SaveAssets();
                    // 强制从磁盘重新加载 clip，确保 Animation 窗口显示最新关键帧
                    foreach (var (aPath, _, _) in newClipPopulateList)
                        AssetDatabase.ImportAsset(aPath, ImportAssetOptions.ForceUpdate);
                    AssetDatabase.Refresh();
                }

                Debug.Log($"[动画贴图导入] 资源补全：Override Controller +{overrideCreated}，Animation Clip +{animCreated}，关键帧填充 {populatedClips} 个\n{log}");
                EditorUtility.DisplayDialog("资源补全完成",
                    $"新建 Override Controller：{overrideCreated} 个\n新建 Animation Clip：{animCreated} 个\n自动填充关键帧：{populatedClips} 个", "好的");
            }

            // 将 Animation Clip 挂接到对应 Override Controller
            int wiredSlots = 0;
            foreach (var kvp in ocToAnimClips)
                wiredSlots += WireClipsToOverrideController(kvp.Key, kvp.Value);

            if (wiredSlots > 0)
            {
                AssetDatabase.SaveAssets();
                Debug.Log($"[动画贴图导入] 挂接完成：在 {ocToAnimClips.Count} 个 Override Controller 中更新了 {wiredSlots} 个 clip 槽位。");
            }
        }

        /// <summary>
        /// 将 textureDirAssetPath 目录下的贴图按文件名排序后，逐帧写入 Animation Clip 的 Sprite 关键帧。
        /// 订单/剧情绑定 UnityEngine.UI.Image.m_Sprite，角色动画绑定 SpriteRenderer.m_Sprite。
        /// </summary>
        private bool PopulateAnimClipKeyframes(string animPath, string textureDirAssetPath, ECategory category)
        {
            var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(animPath);
            if (clip == null)
            {
                Debug.LogWarning($"[动画贴图导入] 找不到 AnimationClip：{animPath}，跳过关键帧填充。");
                return false;
            }

            var fullDir = ToFullPath(textureDirAssetPath);
            if (!Directory.Exists(fullDir))
            {
                Debug.LogWarning($"[动画贴图导入] 贴图目录不存在：{textureDirAssetPath}，跳过关键帧填充：{animPath}");
                return false;
            }

            // 按末段整数序号排序，避免字符串排序导致 _10 排在 _2 前面
            var spritePaths = Directory.GetFiles(fullDir)
                .Where(f => TextureExtensions.Contains(Path.GetExtension(f).ToLowerInvariant()))
                .OrderBy(f => ExtractSequenceInt(Path.GetFileNameWithoutExtension(f)))
                .ThenBy(f => f)
                .Select(f => textureDirAssetPath.TrimEnd('/') + "/" + Path.GetFileName(f))
                .ToList();

            if (spritePaths.Count == 0)
            {
                Debug.LogWarning($"[动画贴图导入] 贴图目录为空：{textureDirAssetPath}，跳过关键帧填充：{animPath}");
                return false;
            }

            const float frameRate = 24f;
            const float interval = 1f / frameRate;

            // 按排序后的索引连续分配帧位置（frame 0, 1, 2, …），与手动在 Animation 窗口拖入贴图的结果一致
            var keyframeList = new List<ObjectReferenceKeyframe>();
            var diagSb = new System.Text.StringBuilder();
            diagSb.AppendLine($"[动画贴图导入] 关键帧诊断 {Path.GetFileName(animPath)}，贴图目录：{textureDirAssetPath}，共 {spritePaths.Count} 个文件");

            var seenGuids = new Dictionary<string, int>(); // guid → 首次出现帧号，用于检测重复引用
            for (int i = 0; i < spritePaths.Count; i++)
            {
                var sprite = AssetDatabase.LoadAssetAtPath<Sprite>(spritePaths[i]);
                if (sprite == null)
                {
                    // 贴图可能尚未以 Sprite 类型完成导入，强制重导一次
                    if (AssetImporter.GetAtPath(spritePaths[i]) is TextureImporter ti)
                    {
                        ti.textureType = TextureImporterType.Sprite;
                        ti.spriteImportMode = SpriteImportMode.Single;
                        ti.SaveAndReimport();
                        sprite = AssetDatabase.LoadAssetAtPath<Sprite>(spritePaths[i]);
                    }
                }

                if (sprite == null)
                {
                    diagSb.AppendLine($"  帧{i:D3}  NULL  {Path.GetFileName(spritePaths[i])}  ← 加载失败，跳过！");
                    Debug.LogWarning($"[动画贴图导入] Sprite 加载失败，跳过该帧（路径：{spritePaths[i]}）");
                    continue;
                }

                var guid = AssetDatabase.AssetPathToGUID(spritePaths[i]);
                string dupNote = seenGuids.TryGetValue(guid, out int firstFrame)
                    ? $"  ← ⚠️ 与帧{firstFrame:D3}相同 GUID！"
                    : "";
                if (!seenGuids.ContainsKey(guid)) seenGuids[guid] = i;

                diagSb.AppendLine($"  帧{i:D3}  {sprite.name,-40}  guid={guid}{dupNote}");
                keyframeList.Add(new ObjectReferenceKeyframe { time = i * interval, value = sprite });
            }

            Debug.Log(diagSb.ToString());

            if (keyframeList.Count == 0)
            {
                Debug.LogWarning($"[动画贴图导入] 所有 Sprite 均加载失败，跳过关键帧填充：{animPath}");
                return false;
            }

            var keyframes = keyframeList.ToArray();

            var bindingType = category == ECategory.CharacterAnim
                ? typeof(SpriteRenderer)
                : typeof(UnityEngine.UI.Image);
            string nodePath = category == ECategory.CharacterAnim ? "img_character" : "img_emoji";
            var binding = EditorCurveBinding.PPtrCurve(nodePath, bindingType, "m_Sprite");

            // 清除旧的 ObjectReference 曲线，防止旧绑定路径残留导致双轨
            foreach (var oldBinding in AnimationUtility.GetObjectReferenceCurveBindings(clip))
                AnimationUtility.SetObjectReferenceCurve(clip, oldBinding, null);

            clip.frameRate = frameRate;
            AnimationUtility.SetObjectReferenceCurve(clip, binding, keyframes);
            EditorUtility.SetDirty(clip);

            Debug.Log($"[动画贴图导入] 关键帧填充完成：{Path.GetFileName(animPath)}，写入 {keyframeList.Count}/{spritePaths.Count} 帧，绑定路径={nodePath}");
            return true;
        }

        /// <summary>
        /// 按关键词将 animClips 挂接到 Override Controller 的对应槽位。
        /// slot 名可能带前缀（如 anui_default_idle），用 EndsWith("_animKey") 或等值匹配。
        /// 返回实际更新的槽位数。
        /// </summary>
        private static int WireClipsToOverrideController(string ocPath, List<(string animKey, string animPath)> animClips)
        {
            var oc = AssetDatabase.LoadAssetAtPath<AnimatorOverrideController>(ocPath);
            if (oc == null)
            {
                Debug.LogWarning($"[动画贴图导入] 找不到 Override Controller：{ocPath}，跳过挂接。");
                return 0;
            }

            var overrides = new List<KeyValuePair<AnimationClip, AnimationClip>>();
            oc.GetOverrides(overrides);

            int updated = 0;
            for (int i = 0; i < overrides.Count; i++)
            {
                var originalClip = overrides[i].Key;
                if (originalClip == null) continue;

                string slotName = originalClip.name.ToLowerInvariant();

                // slot 名可能带前缀（anui_default_idle），用后缀或等值匹配 animKey
                string matchedPath = null;
                foreach (var (animKey, animPath) in animClips)
                {
                    string key = animKey.ToLowerInvariant();
                    if (slotName == key || slotName.EndsWith("_" + key))
                    {
                        matchedPath = animPath;
                        break;
                    }
                }
                if (matchedPath == null) continue;

                var clip = AssetDatabase.LoadAssetAtPath<AnimationClip>(matchedPath);
                if (clip == null || overrides[i].Value == clip) continue;

                overrides[i] = new KeyValuePair<AnimationClip, AnimationClip>(originalClip, clip);
                updated++;
            }

            if (updated > 0)
            {
                oc.ApplyOverrides(overrides);
                EditorUtility.SetDirty(oc);
            }

            return updated;
        }

        private static string GetOverrideControllerPath(ECategory cat, string charLower) => cat switch
        {
            ECategory.Order         => $"Assets/Res/UI/Animator/EmojiOverride/coui_emoji_{charLower}.overrideController",
            ECategory.Story         => $"Assets/Res/UI/Animator/EmojiOverride/coui_chatemoji_{charLower}.overrideController",
            ECategory.CharacterAnim => $"Assets/Res/Character/Animation/Override/coch_character_ani_{charLower}.overrideController",
            _ => null,
        };

        private static string GetBaseControllerPath(ECategory cat) => cat switch
        {
            ECategory.Order         => "Assets/Res/UI/Animator/EmojiMain/coui_emoji_common.controller",
            ECategory.Story         => "Assets/Res/UI/Animator/EmojiMain/coui_emoji_chat_common.controller",
            ECategory.CharacterAnim => "Assets/Res/Character/Animation/Main/coch_character_ani_common.controller",
            _ => null,
        };

        private static string GetAnimClipPath(ECategory cat, string charFolder, string charLower, string animLower) => cat switch
        {
            ECategory.Order or ECategory.Story =>
                $"Assets/Art/UI/Animation/Emoji/{charFolder}/anui_emoji_{charLower}_{animLower}.anim",
            ECategory.CharacterAnim =>
                $"Assets/Art/Character/Animation/{charFolder}/ansc_full_{charLower}_{animLower}.anim",
            _ => null,
        };

        private bool TryCreateOverrideController(string assetPath, string baseControllerPath)
        {
            var baseController = AssetDatabase.LoadAssetAtPath<RuntimeAnimatorController>(baseControllerPath);
            if (baseController == null)
            {
                Debug.LogWarning($"[动画贴图导入] 找不到基础 Controller：{baseControllerPath}，跳过 {assetPath}");
                return false;
            }

            EnsureAssetFolder(Path.GetDirectoryName(assetPath).Replace('\\', '/'));
            AssetDatabase.CreateAsset(new AnimatorOverrideController(baseController), assetPath);
            return true;
        }

        private static bool AssetFileExists(string assetPath)
        {
            string full = Path.GetFullPath(Path.Combine(Application.dataPath, "..", assetPath));
            return File.Exists(full);
        }

        /// <summary>把本次执行结果写成带时间戳的 Markdown 清单，存到工具内部 Manifests 目录。</summary>
        private void WriteImportManifest(List<string> importedTextures, List<string> createdOcPaths, List<string> createdAnimPaths)
        {
            if (importedTextures.Count + createdOcPaths.Count + createdAnimPaths.Count == 0)
                return;

            EnsureAssetFolder(ManifestFolderRelative);

            var timestamp = DateTime.Now.ToString("yyyyMMdd_HHmmss");
            var relativePath = $"{ManifestFolderRelative}/ImportManifest_{timestamp}.md";
            var fullPath = Path.GetFullPath(Path.Combine(Application.dataPath, "..", relativePath));

            var sb = new System.Text.StringBuilder();
            sb.AppendLine("# 动画贴图批量导入清单");
            sb.AppendLine();
            sb.AppendLine($"- 执行时间：{DateTime.Now:yyyy-MM-dd HH:mm:ss}");
            sb.AppendLine($"- JSON 文件：{(string.IsNullOrWhiteSpace(jsonFilePath) ? "-" : jsonFilePath)}");
            sb.AppendLine($"- 源根目录：{(string.IsNullOrWhiteSpace(sourceRoot) ? "-" : sourceRoot)}");
            sb.AppendLine();

            sb.AppendLine($"## 导入贴图：{importedTextures.Count} 张");
            foreach (var path in importedTextures)
                sb.AppendLine($"- `{path}`");
            sb.AppendLine();

            sb.AppendLine($"## 新建 Override Controller：{createdOcPaths.Count} 个");
            foreach (var path in createdOcPaths)
                sb.AppendLine($"- `{path}`");
            sb.AppendLine();

            sb.AppendLine($"## 新建 Animation Clip：{createdAnimPaths.Count} 个");
            foreach (var path in createdAnimPaths)
                sb.AppendLine($"- `{path}`");

            File.WriteAllText(fullPath, sb.ToString(), new System.Text.UTF8Encoding(true));
            AssetDatabase.ImportAsset(relativePath);
            Debug.Log($"[动画贴图导入] 清单已生成：{relativePath}");
        }

        // ───────────────────────── 工具方法 ─────────────────────────

        /// <summary>按类型返回贴图文件名前缀。</summary>
        private static string GetTexturePrefix(ECategory cat) => cat switch
        {
            ECategory.CharacterAnim => "spch_full_",
            _ => "spui_emoji_",
        };

        /// <summary>
        /// 从文件名（不含扩展名）提取末段数字并归一化为至少 2 位。
        /// 支持纯数字名（如 "001"）和以 '_' 分段的末段数字（如 "frame_029"）。
        /// 无法提取则返回 null。
        /// </summary>
        /// <summary>提取末段整数序号，用于数值排序；无法解析则返回 int.MaxValue。</summary>
        private static int ExtractSequenceInt(string nameWithoutExt)
        {
            if (string.IsNullOrEmpty(nameWithoutExt)) return int.MaxValue;
            if (int.TryParse(nameWithoutExt, out int n)) return n;
            int lastUnderscore = nameWithoutExt.LastIndexOf('_');
            if (lastUnderscore >= 0 && int.TryParse(nameWithoutExt.Substring(lastUnderscore + 1), out int m)) return m;
            return int.MaxValue;
        }

        private static string ExtractNormalizedSequence(string nameWithoutExt)
        {
            if (string.IsNullOrEmpty(nameWithoutExt)) return null;

            // 纯数字文件名
            if (int.TryParse(nameWithoutExt, out int n))
                return n < 10 ? $"0{n}" : n.ToString();

            // 取最后一个 '_' 后的数字段
            int lastUnderscore = nameWithoutExt.LastIndexOf('_');
            if (lastUnderscore >= 0 && int.TryParse(nameWithoutExt.Substring(lastUnderscore + 1), out int m))
                return m < 10 ? $"0{m}" : m.ToString();

            return null;
        }

        private static readonly string[] TextureExtensions = { ".png", ".jpg", ".jpeg", ".tga", ".psd", ".bmp", ".gif" };

        private static IEnumerable<string> EnumerateTextureFiles(string dir)
        {
            return Directory.EnumerateFiles(dir, "*.*", SearchOption.TopDirectoryOnly)
                .Where(f => TextureExtensions.Contains(Path.GetExtension(f).ToLowerInvariant()));
        }

        /// <summary>解析任务的源目录：绝对路径直接用；相对路径基于源根目录递归向下查找最深匹配；为空则用源根目录。</summary>
        private string ResolveSourceDir(ImportTask t)
        {
            if (string.IsNullOrWhiteSpace(t.sourceDir))
                return sourceRoot ?? "";

            if (Path.IsPathRooted(t.sourceDir))
                return t.sourceDir;

            return FindDeepestDir(sourceRoot, t.sourceDir) ?? Path.Combine(sourceRoot ?? "", t.sourceDir);
        }

        /// <summary>
        /// 在 root 下查找名称为 dirName 的目录。
        /// 优先返回直接子目录；不存在时递归搜索，返回路径层级最深的匹配项；均不存在则返回 null。
        /// </summary>
        private static string FindDeepestDir(string root, string dirName)
        {
            if (string.IsNullOrWhiteSpace(root))
                return Directory.Exists(dirName) ? dirName : null;

            // 直接子目录优先
            var direct = Path.Combine(root, dirName);
            if (Directory.Exists(direct)) return direct;

            if (!Directory.Exists(root)) return null;

            // 递归搜索，取层级最深（路径分隔符最多）的匹配
            try
            {
                var matches = Directory.GetDirectories(root, dirName, SearchOption.AllDirectories);
                if (matches.Length == 0) return null;
                return matches.OrderByDescending(p => p.Count(c => c == Path.DirectorySeparatorChar)).First();
            }
            catch { return null; }
        }

        /// <summary>按 类型 + 角色 推导引擎内目标目录（Assets/ 相对路径）。</summary>
        private string ResolveTargetDir(ImportTask t)
        {
            var character = string.IsNullOrWhiteSpace(t.character) ? "_Unknown" : t.character.Trim();
            // 从 sourceDir（格式 charKey-animKey）提取 animKey，用于订单/剧情子目录后缀
            int dash = t.sourceDir?.IndexOf('-') ?? -1;
            string animSuffix = dash >= 0 ? t.sourceDir.Substring(dash + 1) : "";
            switch (t.category)
            {
                case ECategory.Order:
                    var orderSub = string.IsNullOrWhiteSpace(animSuffix) ? orderSubFolder : $"{orderSubFolder}_{animSuffix}";
                    return Combine(emojiBasePath, character, orderSub);
                case ECategory.Story:
                    var storySub = string.IsNullOrWhiteSpace(animSuffix) ? storySubFolder : $"{storySubFolder}_{animSuffix}";
                    return Combine(emojiBasePath, character, storySub);
                case ECategory.CharacterAnim:
                    return Combine(characterAnimBasePath, character, charAnimSubFolder);
                default:
                    var defaultSub = string.IsNullOrWhiteSpace(animSuffix) ? orderSubFolder : $"{orderSubFolder}_{animSuffix}";
                    return Combine(emojiBasePath, character, defaultSub);
            }
        }

        private static string Combine(params string[] parts)
        {
            return string.Join("/", parts
                .Where(p => !string.IsNullOrWhiteSpace(p))
                .Select(p => p.Trim().Trim('/', '\\')));
        }

        private static string ToFullPath(string assetPath)
        {
            var projectRoot = Directory.GetParent(Application.dataPath)!.FullName;
            return Path.GetFullPath(Path.Combine(projectRoot, assetPath));
        }

        /// <summary>逐级确保 Assets 下的目录存在，不存在则用 AssetDatabase 新建。</summary>
        private static void EnsureAssetFolder(string assetPath)
        {
            assetPath = assetPath.Replace('\\', '/').TrimEnd('/');
            if (string.IsNullOrEmpty(assetPath) || AssetDatabase.IsValidFolder(assetPath))
            {
                return;
            }

            if (!assetPath.StartsWith("Assets"))
            {
                // 目标不在 Assets 下，直接用 IO 建目录（导入器可能无法识别，但保证目录存在）
                Directory.CreateDirectory(ToFullPath(assetPath));
                return;
            }

            var parts = assetPath.Split('/');
            var current = parts[0]; // "Assets"
            for (int i = 1; i < parts.Length; i++)
            {
                var next = current + "/" + parts[i];
                // StartAssetEditing 期间 IsValidFolder 可能滞后，同时检查物理磁盘避免重复创建
                if (!AssetDatabase.IsValidFolder(next) && !Directory.Exists(ToFullPath(next)))
                {
                    AssetDatabase.CreateFolder(current, parts[i]);
                }
                current = next;
            }
        }

        private static string FirstString(JObject obj, params string[] keys)
        {
            foreach (var key in keys)
            {
                var token = obj[key];
                if (token != null && token.Type != JTokenType.Null)
                {
                    var s = token.ToString();
                    if (!string.IsNullOrWhiteSpace(s)) return s;
                }
            }
            return null;
        }

        private static bool TryParseCategory(string raw, out ECategory category)
        {
            category = ECategory.Order;
            if (string.IsNullOrWhiteSpace(raw)) return false;

            var v = raw.Trim().ToLowerInvariant();

            // 角色动画 / 场景动画
            if (v.Contains("角色动画") || v.Contains("场景") || v.Contains("characteranim") ||
                v.Contains("character") || v.Contains("full") || v.Contains("scene"))
            {
                category = ECategory.CharacterAnim;
                return true;
            }

            // 剧情 / Chat
            if (v.Contains("剧情") || v.Contains("chat") || v.Contains("story"))
            {
                category = ECategory.Story;
                return true;
            }

            // 订单 / 表情动画(默认) / Common
            if (v.Contains("订单") || v.Contains("order") || v.Contains("表情") ||
                v.Contains("emoji") || v.Contains("common"))
            {
                category = ECategory.Order;
                return true;
            }

            return false;
        }
    }
}
