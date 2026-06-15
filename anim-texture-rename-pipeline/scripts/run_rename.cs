// ============================================================================
// MCPForUnity execute_code 用脚本：全自动驱动 Uep.Utility.AnimTextureBatchRename。
//
// 用法：
//   1. 把下面两个占位符替换成实际路径（都必须是有效的 Assets 文件夹）：
//        {{TEXTURE_FOLDER}}    贴图文件夹，如 Assets/Art/UI/SpritesAnim/Emoji/Susan/Chat
//        {{ANIMATION_FOLDER}}  动画搜索文件夹，如 Assets/Art/UI/Animation/Emoji
//   2. 用 execute_code（action="execute"）把整段代码作为 code 发送。
//   3. 用 read_console 读取 [RENAME] 开头的日志判断结果。
//
// 它做的事（等价于工具的「扫描分析」+「应用重命名」，但跳过模态确认框）：
//   打开窗口 → 反射设置两个目录字段 → 扫描 → 若预览有错误项则中止、不应用
//   → 否则按「阶段1占位回退 → 阶段2动画引用」顺序落地重命名 → 写 manifest。
//
// 注意：execute_code 的 wrapper 已 using System / Collections.Generic / Linq /
// Reflection / UnityEngine / UnityEditor，无需再写 using。
// ============================================================================
 
var texPath = "{{TEXTURE_FOLDER}}";
var animPath = "{{ANIMATION_FOLDER}}";

var type = AppDomain.CurrentDomain.GetAssemblies()
    .SelectMany(a => { try { return a.GetTypes(); } catch { return new Type[0]; } })
    .FirstOrDefault(t => t.FullName == "Uep.Utility.AnimTextureBatchRename");
if (type == null) { Debug.LogError("[RENAME] 找不到类型 Uep.Utility.AnimTextureBatchRename"); return "[RENAME] 失败：类型未找到"; }

if (!AssetDatabase.IsValidFolder(texPath)) { Debug.LogError("[RENAME] 贴图文件夹无效: " + texPath); return "[RENAME] 失败：贴图文件夹无效 " + texPath; }
if (!AssetDatabase.IsValidFolder(animPath)) { Debug.LogError("[RENAME] 动画文件夹无效: " + animPath); return "[RENAME] 失败：动画文件夹无效 " + animPath; }
var tex = AssetDatabase.LoadAssetAtPath<DefaultAsset>(texPath);
var anim = AssetDatabase.LoadAssetAtPath<DefaultAsset>(animPath);

var w = EditorWindow.GetWindow(type);
const BindingFlags F = BindingFlags.NonPublic | BindingFlags.Instance;
type.GetField("_textureFolder", F).SetValue(w, tex);
type.GetField("_animationFolder", F).SetValue(w, anim);

// 扫描
type.GetMethod("ScanAndBuildPreview", F).Invoke(w, null);

var rowType = type.GetNestedType("RenamePreviewRow", BindingFlags.NonPublic);
var errorField = rowType.GetField("Error");
var oldField = rowType.GetField("OldPath");
var newField = rowType.GetField("NewPath");
var listType = typeof(List<>).MakeGenericType(rowType);
var addMethod = listType.GetMethod("Add");

// 把某个预览列表里「无错误」的行收集成 List<RenamePreviewRow>，并统计错误项
int errCount = 0;
var errMsgs = new List<string>();
Func<string, object> collectValid = (fieldName) =>
{
    var src = type.GetField(fieldName, F).GetValue(w) as System.Collections.IEnumerable;
    var dst = Activator.CreateInstance(listType);
    if (src != null)
        foreach (var row in src)
        {
            var ev = errorField.GetValue(row) as string;
            if (string.IsNullOrEmpty(ev)) addMethod.Invoke(dst, new[] { row });
            else { errCount++; if (errMsgs.Count < 20) errMsgs.Add(ev); }
        }
    return dst;
};

var displacement = collectValid("_unreferencedRenamePreview");
var animRows = collectValid("_renamePreview");
int validCount = (int)listType.GetProperty("Count").GetValue(displacement)
               + (int)listType.GetProperty("Count").GetValue(animRows);

if (errCount > 0)
{
    foreach (var m in errMsgs) Debug.LogWarning("[RENAME] 错误项: " + m);
    Debug.LogError("[RENAME] 扫描发现 " + errCount + " 个错误项，已中止，未应用。请核实贴图目录是否选对、贴图文件名末段是否为数字。");
    return "[RENAME] 扫描发现 " + errCount + " 个错误项，已中止";
}
if (validCount == 0)
{
    Debug.LogWarning("[RENAME] 扫描无可重命名项。可能：目录选错、目录内无待整理贴图、或已是规范命名。");
    return "[RENAME] 无可重命名项（请核实贴图目录）";
}

// 目标路径去重（与工具一致，避免两行写到同一目标）
var targets = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
foreach (var lst in new[] { displacement, animRows })
    foreach (var row in (System.Collections.IEnumerable)lst)
    {
        var np = newField.GetValue(row) as string;
        if (!targets.Add(np)) { Debug.LogError("[RENAME] 目标路径重复，已中止：" + np); return "[RENAME] 目标路径重复，已中止"; }
    }

// 应用前体检（不安全项会被工具拦截并弹框，仅在有问题时出现）
var preflight = (bool)type.GetMethod("PreflightCheckApplyRenames", F).Invoke(w, new object[] { displacement, animRows });
if (!preflight) { Debug.LogError("[RENAME] 应用前体检未通过，已中止（见上方 Console / 弹框）。"); return "[RENAME] 体检未通过，已中止"; }

// 阶段 1：占位回退
var moveArgs = new object[] { "阶段 1 占位回退", displacement, 0 };
bool ok1 = (bool)type.GetMethod("RunMoveBatch", F).Invoke(w, moveArgs);
if (!ok1) { Debug.LogError("[RENAME] 阶段 1 占位回退失败，已中止。"); return "[RENAME] 阶段 1 失败"; }
int done1 = (int)moveArgs[2];

// 阶段 2：动画引用重命名（按依赖顺序）
var animArgs = new object[] { "阶段 2 动画引用", animRows, 0 };
bool ok2 = (bool)type.GetMethod("RunAnimRenameInDependencyOrder", F).Invoke(w, animArgs);
if (!ok2) { Debug.LogError("[RENAME] 阶段 2 动画引用重命名失败，已中止。"); return "[RENAME] 阶段 2 失败"; }
int done2 = (int)animArgs[2];

AssetDatabase.Refresh();

var manifestPath = (string)type.GetMethod("WriteManifest", F).Invoke(w, new object[] { displacement, animRows });
Debug.Log("[RENAME] 应用完成：占位回退 " + done1 + " 项，动画引用 " + done2 + " 项。manifest=" + (manifestPath ?? "(未生成)"));
return "[RENAME] 应用完成 done1=" + done1 + " done2=" + done2 + " manifest=" + (manifestPath ?? "");
