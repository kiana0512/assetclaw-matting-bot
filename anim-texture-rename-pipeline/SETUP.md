# 环境准备（首次使用 / 分享给他人时照做）

这个 skill 依赖两个 MCP 服务和一个 Unity 工具，光有 skill 文件还跑不起来。
按下面装好、配好、授权一次即可。命令以 Windows（PowerShell + winget）为例。

> 说明：本文不含任何密钥。配置里的飞书 app secret 等敏感信息请向负责人索取，
> **不要**写进会随工程分发/提交的文件里。

---

## 0. 一图看清依赖

| 依赖 | 用途 | 是否随工程分发 | 
|---|---|---|
| Unity 工具 `AnimTextureBatchRename.cs` | 真正执行重命名 | ✅ 在 `Assets/Modules/UepUtility/BatchRenameTool/`，sync 工程即有 |
| 本 skill（`.cursor/skills/...`） | 教 Agent 跑流程 | ✅ 若已提交到工程 |
| **MCPForUnity**（unityMCP） | 用 `execute_code` 驱动工具、读日志 | ❌ 每人各自配 |
| **lark-mcp**（飞书） | 读/写多维表格 | ❌ 每人各自配 |
| Node.js / uv | 上面两个 MCP 的运行时 | ❌ 各自安装 |

---

## 1. 安装运行时

```powershell
# lark-mcp 需要 Node.js（提供 npx）
winget install OpenJS.NodeJS.LTS

# MCPForUnity 后端需要 uv
winget install astral-sh.uv
```

装完重开终端，确认：

```powershell
npx --version
uv --version
```

> 记下 `npx` 的绝对路径（通常 `C:\Program Files\nodejs\npx.cmd`），下一步可能要用到。

---

## 2. 配置 MCP（`~/.cursor/mcp.json`）

编辑 `C:\Users\<你的用户名>\.cursor\mcp.json`，加入这两个 server。
`lark-mcp` 的 `-a`（appId）/`-s`（appSecret）**向负责人索取**后填入：

```jsonc
{
  "mcpServers": {
    "unityMCP": {
      "command": "uvx",
      "args": ["mcp-for-unity-server"]
    },
    "lark-mcp": {
      // npx 不在 PATH 时，把 command 换成绝对路径，如：
      // "C:\\Program Files\\nodejs\\npx.cmd"
      "command": "npx",
      "args": [
        "@larksuiteoapi/lark-mcp", "mcp",
        "-a", "<飞书 APP_ID>",
        "-s", "<飞书 APP_SECRET>",
        "--oauth"
      ]
    }
  }
}
```

> unityMCP 的实际启动命令以你们仓库 / MCPForUnity 文档为准（有的用 `uvx mcp-for-unity-server`，
> 有的指向本地脚本）；和今天能跑通的那份保持一致即可。

改完在 Cursor 里重载 MCP（设置里 toggle 一下，或重启 Cursor），让它重新拉起这两个 server。

---

## 3. 飞书应用授权（一次性，但有坑）

目标表：`app_token=CibAbxkphagGKns1yOJcaGK7nph`、`table_id=tblr2d000xleHj9p`。

1. **开权限**：飞书开放平台 → 该应用 → 权限管理 → 开通 `bitable:app`（多维表格读写）→ **发布新版本**。
2. **配重定向 URL**：应用 → 安全设置 → 重定向 URL 添加 `http://localhost:3000/callback`
   （`https://open.feishu.cn/app/<APP_ID>/safe`）。
3. **走 OAuth**：在 Cursor 里触发任意一次带 `useUAT: true` 的飞书读取（比如读字段列表），
   lark-mcp 会返回一条 `http://localhost:3000/authorize?...` 链接。
   - ⚠️ **链接 60 秒内失效**，且本地授权服务只在这 60 秒里临时监听。**生成后立刻点开**、马上点「授权」。
   - 别点旧链接（会 `PKCE validation failed`），别拖太久（会 `连接被拒`）。
   - 浏览器停在 `localhost:3000/callback` 显示成功即完成；之后的飞书调用就能用了。
4. 该应用对**这张表**需有编辑权限（仅编辑权限即可读写记录）。

---

## 4. 启动 Unity 端

1. 打开本 Unity 工程。
2. 菜单 `Window/MCP For Unity` → **Start Server**（监听 127.0.0.1:8080，端口以工程配置为准）。
3. 确认 Cursor 里 unityMCP 已连接（能成功调 `read_console` / `execute_code`）。

---

## 5. 自检

全部就绪后，让 Agent：
- 用 lark-mcp 读一次目标表字段（验证飞书授权 OK）；
- 用 unityMCP `execute_code` 跑一句 `return "ok";`（验证 Unity 连接 OK）。

两者都通过，就可以正常使用本 skill 了。流程见 [SKILL.md](SKILL.md)，细节见 [reference.md](reference.md)。
