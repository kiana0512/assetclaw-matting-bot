---
name: anim-texture-rename-pipeline
description: 根据飞书多维表格的「进度」状态，自动整理角色动画贴图的不规范命名。当某个角色的某个动作/表情在表格中进度列为「待整理」时，读取它属于场景动画还是表情动画、是订单还是剧情，定位对应的贴图与动画目录，通过 Unity 工具 AnimTextureBatchRename 完成批量规范命名，并回写表格进度。Use when the user mentions 整理动画贴图命名、批量重命名、待整理、AnimTextureBatchRename、表情动画/场景动画 整理，或要根据飞书多维表格驱动重命名流程。
---

# 动画贴图批量整理流水线

把「飞书多维表格里标记为待整理的角色动画」整理成规范命名。核心产物由 Unity 工具
`Uep.Utility.AnimTextureBatchRename`（菜单 `Tools/UITC/动画贴图批量重命名`）生成。

## 依赖能力 

- **飞书 MCP（`lark-mcp`）**：读取 / 更新多维表格记录。
- **Unity MCP（`MCPForUnity`）**：用 `execute_code` 编译运行 C# 驱动重命名工具；用
  `read_console` 取日志、`manage_asset` 列目录。

> 首次使用 / 分享给他人时，先按 [SETUP.md](SETUP.md) 装好运行时、配 MCP、完成飞书 OAuth 授权、启动 Unity Server。

目标表格（从 URL 解析）：
- `app_token`：`CibAbxkphagGKns1yOJcaGK7nph`
- `table_id`：`tblr2d000xleHj9p`
- `view_id`：`vewVr7d7AI`

表格是**三层树形记录**（靠 `父記錄` 关联，不是视图分组）：
- 第 0 层：`表情动画` / `场景动画`（动画类型）
- 第 1 层：角色（`角色` 字段 = `Jessica` / `mia` / `susan` …）
- 第 2 层：叶子动画（`角色` 字段实为**英文关键词** `idle`/`excited`，`动画名` 是中文 `待机`/`兴奋`，带 `进度`、`类型`）

⚠️ 关键：叶子记录的 `角色` 字段存的是**关键词**，真正的角色名在它的**父记录**上，动画类型在**祖父记录**上。
要靠 `父記錄` 向上回溯还原「角色 + 动画类型」。字段 ID、读取要点与避坑见 [reference.md](reference.md)。

## 工作流

逐条处理「进度 = 待整理」的记录，复制此清单跟踪进度：

```
- [ ] 1. 读表：拉取进度=待整理的记录，确定 角色 / 动画类型 / 动作名 / 类型(订单|剧情)
- [ ] 2. 回写：把该条进度改为「整理中」
- [ ] 3. 定位目录：按规则推导并核实 贴图文件夹 与 动画搜索文件夹
- [ ] 4. 执行工具：execute_code 驱动 扫描分析 → 校验无错误 → 应用重命名
- [ ] 5. 验收：read_console 确认成功、生成了 manifest
- [ ] 6. 回写：把该条进度改为「待提交」
```

逐条处理（一次只推进一条记录的整个流程），不要批量改完状态再统一执行。

### 步骤 1：读表

⚠️ **不要**用 `进度 is 待整理` 的 filter 搜索——实测单选字段这样过滤会**漏返回 0 条**。改为
**全量拉取**（`bitable_v1_appTableRecord_search`，不带 filter，`page_size` 500）后在客户端筛 `进度=待整理`。
（按 `父記錄 contains <record_id>` 过滤是有效的，可用来取某角色的全部子记录。）

对每条待整理叶子记录解析出：
- **关键词** = 叶子的 `角色` 字段（英文，如 `idle`/`excited`）；
- **角色名** = 叶子父记录的 `角色`（如 `Jessica`/`mia`，靠 `父記錄.link_record_ids` 回溯）；
- **动画类型** = 祖父记录的 `角色`（`表情动画` / `场景动画`）；
- **类型** = 叶子的 `类型` 字段（`订单` / `剧情`，可能为空；为空按订单处理）。

记下叶子的 `record_id` 备回写。所有读写调用都要带 `useUAT: true`（用户身份）。

### 步骤 2：标记整理中

立即把该记录「进度」更新为 `整理中`，避免并发重复处理。

### 步骤 3：定位目录（最关键，最易错）

按下表推导出两个路径。**贴图文件夹必须精确到该动作实际所在的子目录**——目录识别不准是本流程
最常见的错误。推导后务必用 `manage_asset` 列目录核实（见下方「核实」）。

| 动画类型 | 动画搜索文件夹 | 贴图文件夹（根） |
|---|---|---|
| 表情动画 | `Assets/Art/UI/Animation/Emoji` | `Assets/Art/UI/SpritesAnim/Emoji/{角色}/...` |
| 场景动画 | `Assets/Art/Character/Animation` | `Assets/Art/UI/SpritesAnim/CharacterAnim/{角色}/...` |

表情动画的贴图子目录由「类型」字段决定：
- **剧情** → 角色目录下的 `Chat` 子文件夹。**`Chat` / `chat` 大小写都算剧情**，按实际存在的那个为准。
- **订单** → 角色目录下 **除 `Chat` 外** 的部分（即非 Chat 的那块）。

**核实**：用 `manage_asset` 列出 `Assets/Art/UI/SpritesAnim/Emoji/{角色}`（或 CharacterAnim 对应角色）
下的子目录，确认：
1. 剧情 → 取真实存在的 `Chat`/`chat` 文件夹路径；
2. 订单 → 取包含该动作帧图的非 Chat 子目录（可能就是角色根目录，或 `Common`/`{动作名}` 等子目录）；
3. 选中的目录里确实有目标动作的帧图（`.png`）再继续。

路径与子目录结构有疑问时，先列目录、再按目录里真实存在的帧图判断，**不要凭空拼路径**。

> ⚠️ 不要假设「`{动作名}` 子目录=原始帧、`Common`=已整理」：实测未命名（待整理）贴图可能同时散落在
> `Idle`、`Common` 等多个子目录，没有哪个子目录天然已整理。判定以「是否已是规范命名 + 是否被动画引用」为准，
> 必要时把贴图文件夹放到能覆盖这些帧的层级（如角色根目录），交给工具按动画引用自动认领。

### 步骤 4：执行重命名工具（全自动）

用 `MCPForUnity` 的 `execute_code`（action=`execute`）发送 [scripts/run_rename.cs](scripts/run_rename.cs)，
发送前把模板里的 `{{TEXTURE_FOLDER}}`、`{{ANIMATION_FOLDER}}` 替换为步骤 3 的两个路径。

脚本会：打开工具窗口 → 反射设置两个目录字段 → 调 `ScanAndBuildPreview()` 扫描 →
**若预览存在错误项则中止、不应用** → 否则按工具底层逻辑落地重命名并写 manifest → 打印 `[RENAME]` 摘要。

它不直接调 `ApplyRenames()`（该方法带模态确认框，会卡住 execute_code），而是跳过确认框、
直接复用底层的 `PreflightCheckApplyRenames` / `RunMoveBatch` / `RunAnimRenameInDependencyOrder` /
`WriteManifest`。仅当出现真正的危险项（缺 `.meta`、目标被占用等）时才会弹框拦截。

**建议先只扫描预览、再应用**：第一次对某目录操作时，先跑一段「只 `ScanAndBuildPreview()` 并打印
`_unreferencedRenamePreview`/`_renamePreview`/`_alreadyNamedTextures`/`_unreferencedTextures` 四个桶」的
只读脚本，把「旧名→新名」给用户确认无误后，再发应用脚本。这样能在落地前抓出目录选错。

⚠️ 读结果优先用 **execute_code 的返回值**（脚本用 `return` 把摘要直接带回），不要依赖 `read_console`——
实测 execute_code 里的 `Debug.Log` 不一定能被 `read_console` 取到（常返回 0 条）。`run_rename.cs`
已把结果通过 `return` 返回。

### 步骤 5：验收

成功标准 = 工具成功执行：
- console 出现 `[RENAME] 应用完成`，且无 error 级日志；
- `Assets/Modules/UepUtility/BatchRenameTool/Editor/Manifests/` 下新增了一份 `RenameManifest_*.md`。

若扫描阶段报错（`[RENAME] 扫描发现 N 个错误项，已中止`）：多半是贴图目录选错或贴图末段非数字。
回到步骤 3 重新核实目录；不要在有错误项时强行应用。manifest 校验方法见 [reference.md](reference.md)。

### 步骤 6：标记待提交

工具成功执行后，把该记录「进度」更新为 `待提交`。然后处理下一条「待整理」记录。

## 命名规范速记

- 表情动画：`spui_emoji_{角色}_{关键词}_{序号}`，动画来源 `anui_emoji_{角色}_{关键词}_NN.anim`
- 场景动画：`spch_full_{角色}_{关键词}_{序号}`

完整规范、序号归一化规则、占位 `unworked_*` 机制见 [reference.md](reference.md)。
