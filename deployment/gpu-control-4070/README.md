# 4070 Ti GPU Control 节点主机准备配置

此目录保存 4070 Ti 主机的可审计配置模板，依据：

- `115_2026-08-12_4070TI_WSL2_PREPARATION_AND_INTEGRATION_HANDOFF.md`
- 源文档 SHA256：`38BC40024CF9CEFB1EA8460B582F97FBCAE459C3FB300A139CCC18AEAC54492B`

文件用途：

- `wslconfig`：部署到 Windows 用户目录的 `.wslconfig`，限制 WSL2 为 32 GiB 内存、12 个处理器和 16 GiB swap。
- `wsl.conf`：部署到 Ubuntu 22.04 的 `/etc/wsl.conf`，启用 systemd。

当前阶段只允许主机准备。Docker、NVIDIA Container Toolkit、Worker 容器、Node Agent、端口映射、Windows 防火墙规则、HMAC 密钥及节点注册，必须等待 4090 主控团队提供被锁定且可校验的正式交付包。
