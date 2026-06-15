# 参考：命名规范 / 目录识别 / 飞书读写 / manifest 校验

工具源码：`Assets/Modules/UepUtility/BatchRenameTool/Editor/AnimTextureBatchRename.cs`
（类 `Uep.Utility.AnimTextureBatchRename`，菜单 `Tools/UITC/动画贴图批量重命名`）。

## 1. 命名规范

工具按「贴图被哪个 AnimationClip 引用」来推导目标名，关键词取自动画文件名。
 
| 场景 | 目标贴图命名 | 动画文件名 |
|---|---|---|
| 表情动画（Emoji） | `spui_emoji_{角色}_{关键词}_{序号}` | `anui_emoji_{角色}_{关键词}_NN.anim` |
| 场景动画（CharacterAnim） | `spch_full_{角色}_{关键词}_{序号}` | `spch_*` / `spui_full_*`（旧） |

规则要点：
- **角色名**优先从路径里的 `Emoji/{角色}` 或 `CharacterAnim/{角色}` 段解析，否则从文件名 / 贴图文件夹
  下第一级子目录解析，统一转小写。
- **序号**直接沿用贴图原文件名末段数字（按 `_` 拆分后最后一段，纯数字命名如 `001.png` 也支持），
  **不**按动画关键帧顺序重排。位数归一化为「至少 2 位」：0~9 → `00`~`09`，10~99 保持两位，100+ 原值。
- **末段不是数字**的贴图会以错误项列出 → 需先手动改名后再扫描。
- 每张贴图只认领一个关键词（`idle` 优先，否则先扫描到的动画为准）。同关键词若同时存在
  `anui_` / `ani_` 版本，只采用 `anui_`。目标文件名全部小写。
- 未被任何动画引用、但文件名已是 `anui_emoji_*` / `spui_emoji_*` / `spch_full_*` / `spui_full_*`
  **且末段为纯数字**的，会被改成 `unworked_{原序号}` 占位（保留自身序号，**是归档不是删除**）；其余未引用贴图不改名。
  冲突时占位文件先回退到 `unworked_*`，再让动画引用批次落位（即工具的「阶段 1 占位回退」）。
- 末段**非纯数字**（如 `spui_emoji_mia_idle_31 1`——带空格）的未引用贴图**不会**进 unworked 桶，
  会落到「未引用不重命名」，工具不动它。
- 推论：在动画里**删掉某些关键帧**会让对应规范帧变成「未引用」，重扫后它们就被归档为 `unworked_NN`
  ——这正是制造一个真实「待整理」测试场景的办法。

## 2. 目录识别细节（最易错环节）

两个输入：**贴图文件夹**（`_textureFolder`）和**动画搜索文件夹**（`_animationFolder`）。

| 动画类型 | 动画搜索文件夹 | 贴图文件夹根 |
|---|---|---|
| 表情动画 | `Assets/Art/UI/Animation/Emoji` | `Assets/Art/UI/SpritesAnim/Emoji/{角色}` |
| 场景动画 | `Assets/Art/Character/Animation` | `Assets/Art/UI/SpritesAnim/CharacterAnim/{角色}` |

表情动画贴图子目录（由「类型」字段决定）：
- **剧情**：角色目录下的 `Chat` 子文件夹。大小写不敏感——`Chat`、`chat` 都属于剧情，取实际存在的那个。
- **订单**：角色目录下 **除 Chat 外** 的内容。具体子目录因角色而异（可能直接是角色根目录，也可能是
  `Common` / `{动作名}` 等），**必须列目录确认**，不要假设固定名。

> ⚠️ 重要：**不要**按「`{动作名}` 子目录=原始帧、`Common`=已整理」这类规律去猜。实测同一角色下，
> 未命名（待整理）贴图可能**同时散落在 `Idle`、`Common` 等多个子目录**里，没有哪个子目录天然"已整理"。
> 因此整理订单时不要只盯某一个按动作命名的子目录；以"该贴图是否已是规范命名 + 是否被动画引用"为准，
> 必要时把贴图文件夹放到能覆盖到这些帧的层级（如角色根目录），交给工具按动画引用自动认领。

确认流程：
1. `manage_asset` 列出角色目录下的全部子文件夹。
2. 剧情 → 命中 `Chat`/`chat`；订单 → 在非 Chat 子目录里找包含目标动作帧图的那个。
3. 进到候选目录确认有该动作的 `.png` 帧序列，再把这个目录作为贴图文件夹。

动画搜索文件夹可填到 `Emoji` / `Animation` 这一层（较宽，工具按角色+关键词匹配）；若担心匹配过多，
可缩到 `.../Emoji/{角色}`。工具历史 manifest 里动画文件夹常填 `Assets/Art/UI/Animation`（父级）也可用。

## 3. 飞书多维表格读写（lark-mcp）

- `app_token` = `CibAbxkphagGKns1yOJcaGK7nph`，`table_id` = `tblr2d000xleHj9p`，`view_id` = `vewVr7d7AI`。
- **所有调用都带 `useUAT: true`**（用户身份）；该应用需要 `bitable:app` 权限并完成 OAuth 授权。

### 字段（已核实 field_id）

| 字段名 | field_id | 类型 | 说明 |
|---|---|---|---|
| 角色 | `fldnMfS8dg` | 文本(主) | **语义随层级变**：见下方树形结构 |
| 动画名 | `fld0ZNdcA4` | 文本 | 叶子记录的中文动作名（`待机`/`兴奋`…） |
| 类型 | `fldB4CYog5` | 多选 | `订单`(optVWsB3d6) / `剧情`(optsoxZY2a)，可空 |
| 进度 | `fld4ZYV5Ve` | 单选 | `待整理`/`整理中`/`待提交`/`已完成`/`不处理`/`待抽帧`/`抽帧中`/`K帧中` |
| 父記錄 | `fldnFiGoOj` | 单向关联 | 指向上一层记录，靠它还原树形 |

### 三层树形结构（靠 `父記錄`，不是视图分组）

- **第 0 层**：`表情动画`(record `recvl038gyI8sm`) / `场景动画`(record `recvl03fDGUZrv`) —— `角色` 字段存这俩。
- **第 1 层**：角色，`角色` 字段 = `Jessica` / `mia` / `susan` …（如 mia=`recvlne8C0yKvm`）。
- **第 2 层**：叶子动画，`角色` 字段 = **英文关键词**（`idle`/`excited`/`happy`…），`动画名` = 中文，带 `进度`/`类型`。

⚠️ 叶子的 `角色` 是关键词，**真正角色名在父记录、动画类型在祖父记录**。解析一条待整理叶子时：
`关键词 = 叶子.角色`；`角色名 = 父记录.角色`；`动画类型 = 祖父记录.角色`。

### 读取 / 写回

- ⚠️ **`进度 is 待整理` 的 filter 会漏（返回 0）**——单选字段这样过滤不可靠。改用**全量拉取**
  （`bitable_v1_appTableRecord_search` 不带 filter、`page_size` 500、带 `field_names` 缩字段）后客户端筛。
- `父記錄 contains <record_id>` 过滤**有效**，可用来取某角色/某分组下的全部子记录。
- 写回：`bitable_v1_appTableRecord_update`，单选字段 `进度` 直接传选项名字符串（如 `"整理中"`）。
- 字段名 / 选项值若与此处不符，以运行时 `bitable_v1_appTableField_list` 为准。

## 4. manifest 校验

每次「应用重命名」会在 `Assets/Modules/UepUtility/BatchRenameTool/Editor/Manifests/` 生成
`RenameManifest_<时间戳>.md`，结构：

```
# 动画贴图批量重命名清单
- 执行时间：...
- 贴图文件夹：Assets/Art/UI/SpritesAnim/Emoji/Tony/Idle
- 动画文件夹：Assets/Art/UI/Animation
## 阶段 1 占位回退（unworked）：N 项
## 阶段 2 动画引用：M 项
- `旧路径` → `新路径`  · 关键词: idle  · 序号: 29  · 来源: .../anui_emoji_tony_idle_01.anim
```

校验要点：
- 「贴图文件夹」「动画文件夹」与本次目标一致。
- 阶段 2 的新路径符合第 1 节命名规范；关键词、序号、来源动画自洽。
- 历史 manifest 仅**部分**正确，不要直接照搬旧记录的目录；以本次实际目录结构为准。
- 正例参考：`RenameManifest_20260528_115744.md`（Tony/Idle，单条 idle）、
  `RenameManifest_20260526_170049.md`（Jessica，含 `unworked_*` 占位回退 + 多关键词）、
  `RenameManifest_20260602_172431.md`（mia/idle，5 项占位回退 → `unworked_17~21`）。

### 已核实路径（表情动画样例）

- mia idle：贴图夹 `Assets/Art/UI/SpritesAnim/Emoji/Mia/Common`、动画夹 `Assets/Art/UI/Animation/Emoji/Mia`
  （动画夹缩到 `.../Emoji/{角色}` 这一层即可，匹配更聚焦）。
- Emoji 角色目录下的子文件夹实测是 `Chat` / `Common`（注意 `Chat` 首字母大写，剧情大小写都算）。
- idle/excited 等订单动作的帧图在 `Common` 里；同一 `Common` 可能混放多种表情的帧
  （工具按动画引用区分，不受影响）。

## 5. 常见错误与规避

- **贴图目录识别不准**（头号问题）：务必先列目录、按真实帧图定位，剧情认 Chat、订单认非 Chat。
  切勿假设某个按动作命名的子目录已整理——未命名贴图可能同时分布在 `Idle`、`Common` 等多个子目录。
- **末段非数字**：贴图文件名末段不是数字 → 扫描会列错误项。需先手动规范末段序号再重跑。
- **有错误项仍应用**：`run_rename.cs` 已在检测到预览错误项时中止；不要绕过该保护强行应用。
- **状态未回写**：每条记录开始时改「整理中」，工具成功执行后改「待提交」，避免漏改或并发重复。
- **execute_code 限制**：`MCPForUnity` 的 execute_code 禁止 `File.Delete`/`Directory.Delete`/
  `AssetDatabase.DeleteAsset`/`MoveAssetToTrash` 等；重命名走 `AssetDatabase.MoveAsset`，不受影响。
- **execute_code 取结果**：脚本用 `return` 把摘要带回，直接读返回值；**别依赖 `read_console`**
  （里面的 `Debug.Log` 实测常取不到，返回 0 条）。需要看分桶时，在脚本里把各列表拼进返回字符串。
- **飞书单选 filter 漏数据**：`进度 is 待整理` 会返回 0；改全量拉取 + 客户端筛（详见第 3 节）。
- **角色名取错层级**：叶子记录 `角色` 是英文关键词，角色名要从 `父記錄` 回溯（详见第 3 节）。
