# RTX 4070 Ti 最小 SSH 传输入口与持久化回执

> 状态：`SSH_TRANSFER_ACCESS_READY`  
> 日期：2026-08-12  
> 入口：`10.3.34.238:2222`  
> 唯一允许来源：`10.3.34.11/32`  
> 登录账户：`gpucontrol`  
> 本回执不包含密码、私钥、HMAC 或 API Key。

## 1. 4090 可以立即验证

从 `control-4090`（`10.3.34.11`）执行：

```bash
ssh-keyscan -T 5 -p 2222 -t ed25519 10.3.34.238 \
  | ssh-keygen -lf -
```

必须得到 4070 SSH host ED25519 指纹：

```text
SHA256:ItZdiX1CfBI6+QEQferq5kWUImsNpNJ/n/k9rfyfb4U
```

确认指纹后连接：

```bash
ssh -p 2222 gpucontrol@10.3.34.238
```

4090 公钥指纹已在 4070 本机验证为：

```text
SHA256:9IH8SkUA1hBKM3oSnAzGZ/Xt9kQIh55Elt/FVBNkZqA
```

## 2. 实际链路

```text
control-4090 10.3.34.11
  -> TCP 2222
Windows 10.3.34.238:2222
  -> Windows portproxy
WSL current NAT 172.24.3.33:22
  -> OpenSSH
gpucontrol (public key only)
```

当前 `netsh interface portproxy show v4tov4`：

```text
Listen on ipv4:             Connect to ipv4:
Address         Port        Address         Port
--------------- ----------  --------------- ----------
10.3.34.238     2222        172.24.3.33     22
```

WSL NAT 地址是动态值，不能写入 4090 节点数据库；对外始终使用 Windows `10.3.34.238:2222`。

## 3. SSH 加固证据

OpenSSH：

```text
distribution=Ubuntu (WSL2)
hostname=worker-4070ti-wsl
ssh.service=active
ssh.service=enabled
listener=0.0.0.0:22 and [::]:22 inside WSL NAT
```

`sshd -T -C user=gpucontrol,host=worker-4070ti-wsl,addr=10.3.34.11` 的关键生效值：

```text
permitrootlogin no
pubkeyauthentication yes
passwordauthentication no
kbdinteractiveauthentication no
allowusers gpucontrol
```

无密钥探测结果：

```text
Authentications that can continue: publickey
gpucontrol@127.0.0.1: Permission denied (publickey).
```

因此：

- root SSH 登录关闭。
- 密码登录关闭。
- keyboard-interactive 登录关闭。
- 只允许 `gpucontrol`。
- 只接受 `/home/gpucontrol/.ssh/authorized_keys` 中已验证的 4090 ED25519 公钥。

权限：

```text
0700 gpucontrol:gpucontrol /home/gpucontrol/.ssh
0600 gpucontrol:gpucontrol /home/gpucontrol/.ssh/authorized_keys
0644 root:root /etc/ssh/sshd_config.d/60-gpu-control.conf
```

## 4. Windows 防火墙证据

规则：

```yaml
name: GPUControl-4070-SSH-From-4090
display_name: GPU Control SSH 2222 from 4090
enabled: true
direction: Inbound
action: Allow
profile: Any
protocol: TCP
local_address: 10.3.34.238
local_port: 2222
remote_address: 10.3.34.11
```

本机 `Test-NetConnection 10.3.34.238 -Port 2222`：

```text
SourceAddress=10.3.34.238
TcpTestSucceeded=True
```

该结果证明 Windows listener 和 portproxy 已建立；真正的来源限制及 SSH 握手仍应由 4090 从 `10.3.34.11` 完成最终验收。

当前受管端口中只有 Windows `10.3.34.238:2222` 在监听。以下均未开放：

```text
8188 9201 9100 9400 2375 2376
```

## 5. 重启与动态 IP 自愈

已安装脚本：

```text
C:\ProgramData\GPUControl\Update-4070WslSshProxy.ps1
C:\ProgramData\GPUControl\Maintain-4070WslSsh.ps1
```

最终实现改为单个隐藏常驻 Maintainer，旧的 Start/Watchdog 周期任务已删除。原因是短生命周期 Watchdog 每分钟执行 `wsl.exe` 后退出，会使没有长期 Windows 客户端的 WSL 在约 15 秒后自动关机，从而中断 SSH/rsync。

Maintainer 行为：

1. 隐藏运行 `wsl.exe -d Ubuntu -u gpucontrol -- /bin/sleep infinity`，保持 WSL 常驻。
2. 启动并保持 `ssh.service`。
3. 只从 `eth0` 的 JSON 地址信息读取 WSL IPv4；不再使用 `hostname -I`，不会误取 Docker `172.17.0.1`。
4. 仅在目标地址不一致时修复 `10.3.34.238:2222 -> ETH0_IP:22`。
5. 仅在规则不一致时修复来源限定防火墙规则。
6. 不管理、不删除其他 portproxy 或防火墙规则。
7. 每 60 秒在同一个隐藏进程中检查，不反复创建交互任务或窗口。
8. 日志只记录启动、变化和错误，不记录密钥或 secret。

日志：

```text
C:\ProgramData\GPUControl\logs\wsl-ssh-proxy.log
C:\ProgramData\GPUControl\logs\wsl-ssh-maintainer.log
```

计划任务：

| 任务 | 触发方式 | 账户 | 权限 | 状态 |
|---|---|---|---|---|
| `GPUControl-4070-WSL-Maintainer` | `zhangqichao` 登录后启动并长期运行 | `zhangqichao` | Highest / Interactive / Hidden | `Running` |

WSL 发行版属于安装它的 Windows 用户。当前任务不保存 Windows 密码，因此 Windows 重启后在 `zhangqichao` 登录时自动恢复并持续常驻。若要求无人登录即可恢复，只能由主机管理员在 Task Scheduler 本地保存该 Windows 账户凭据，凭据不得交给 4090 或写入脚本。

### SSH reset 根因与修复验证

日志证据表明旧实现曾出现：

```text
systemd-logind: System is powering down
sshd: Received signal 15; terminating
```

该事件大约每分钟发生一次，与 rsync `connection reset` 时间吻合。最终修复后执行连续 45 秒采样，覆盖原自动关机窗口与一次检查周期：

```text
Time      Boot ID                               SSH PID  eth0          portproxy target
16:40:56  a6c46ce7-03c6-4f10-8380-c0f4824bf61c  252      172.24.3.33   172.24.3.33:22
16:41:12  a6c46ce7-03c6-4f10-8380-c0f4824bf61c  252      172.24.3.33   172.24.3.33:22
16:41:27  a6c46ce7-03c6-4f10-8380-c0f4824bf61c  252      172.24.3.33   172.24.3.33:22
16:41:42  a6c46ce7-03c6-4f10-8380-c0f4824bf61c  252      172.24.3.33   172.24.3.33:22
```

四次采样中 boot ID、SSH PID、eth0 地址和 portproxy 均未变化；没有新的 `powering down`、SSH stop/start 或映射刷新。

当前 ComfyUI 归档断点文件为：

```text
/srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz.part
size=7196508160 bytes
```

4090 可重新运行支持断点续传的 `transfer_4070ti_artifacts.sh`；不需要从零开始传输。

## 6. 文件哈希

```text
2827BC443543D5AF9ED19614A9CDE538DC437FFD9F81518A2054283EC12394A4  authorized_keys.control-4090
C3F9ADFB73F027707B70CB63A26A1BDB1ACF59CA7E5AEDF78ECDEA5033C24E73  60-gpu-control-ssh.conf
38B53E1C8C444485D9073587E5077C3A92449C9DBA1B46DD5F9B14E913DF8F90  Update-4070WslSshProxy.ps1
71565A2A6BDAC8A2DD19882CF2D53E507750BB7929A1D4BA83A562FC92AC426E  Maintain-4070WslSsh.ps1
D651A44B90D944E8FEB7A009B20DE1F776432C93CDDCFFAB319F3063D16A91D8  Install-4070WslSshPersistence.ps1
```

安装到 `C:\ProgramData\GPUControl` 的自愈脚本 SHA-256 与仓库副本一致：

```text
38B53E1C8C444485D9073587E5077C3A92449C9DBA1B46DD5F9B14E913DF8F90
```

## 7. 4090 传输建议

先在 4090 写入并核验 known_hosts，再开始 25 GB 制品传输：

```bash
install -d -m 0700 ~/.ssh
ssh-keyscan -T 5 -p 2222 -t ed25519 10.3.34.238 >> ~/.ssh/known_hosts
ssh-keygen -lf ~/.ssh/known_hosts | grep 'ItZdiX1CfBI6+QEQferq5kWUImsNpNJ/n/k9rfyfb4U'
```

镜像归档可使用：

```bash
rsync -ah --partial --append-verify --info=progress2 \
  -e 'ssh -p 2222' \
  /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz \
  gpucontrol@10.3.34.238:/srv/gpu-control/images/
```

ImageClip 与模型按批准源路径以相同方式 rsync 到 `/opt/imageclip/`。传输结束后，4070 侧会重新验证文件大小和 SHA-256，再导入镜像并执行 C0；不会仅根据 rsync 成功就信任制品。
