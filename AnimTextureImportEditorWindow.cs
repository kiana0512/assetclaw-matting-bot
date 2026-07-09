using UnityEditor;
using UnityEngine;

namespace SparkTools.Editor.AnimTextureImporter
{
    /// <summary>
    /// 动画贴图批量导入工具主窗口。
    ///
    /// 用途：把「多维表格提炼出的 JSON」或「手动录入」的一批 (角色名, 类型) 任务，
    /// 从指定源目录批量导入到引擎里对应的角色目录，并统一设置贴图导入参数
    /// （TextureType=Sprite、Default/Android MaxSize=256、Android 勾选 Override）。
    ///
    /// 目录映射（基础路径与子目录均在面板上开放可配置）：
    ///   订单     → {EmojiBase}/{角色}/Common
    ///   剧情     → {EmojiBase}/{角色}/Chat
    ///   角色动画 → {CharacterAnimBase}/{角色}/Common
    /// </summary>
    public class AnimTextureImportEditorWindow : GUIEditor<AnimTextureImportEditorWindow>
    {
        [MenuItem("Tools/UITC/动画贴图批量导入")]
        public static void OpenWindow()
        {
            ShowWindow();
        }

        protected override void InitBeforeOpen()
        {
            titleContent = new GUIContent("动画贴图批量导入");
            minSize = new Vector2(560, 720);
        }

        protected override void AwakeRegister()
        {
            RegisterModule<AnimTextureImportModule>();
        }

        protected override void OnGUIShowModule()
        {
            DrawModule<AnimTextureImportModule>();
        }
    }
}
