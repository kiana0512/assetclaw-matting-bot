# 4070 Ti WSL2 GPU 卡死事件、恢复结果与防复发交接

日期：2026-08-12（Asia/Shanghai）  
节点：`worker-4070ti-animation-host-01`  
Windows：`10.3.34.238`  
主控：`10.3.34.11`  
WSL 发行版：`Ubuntu` / Ubuntu 22.04.5 LTS  
GPU：NVIDIA GeForce RTX 4070 Ti / 12,282 MiB  
Windows NVIDIA 驱动：`576.52`  
WSL：`2.7.11.0`，内核 `6.18.33.2-microsoft-standard-WSL2`

## 1. 当前恢复结果

2026-08-12 18:56（本地时间）完成 WSL/Hyper-V 服务栈恢复，当前新 WSL boot ID：

```text
8b00f8a8-442d-4929-8720-4493090677ad
```

实测状态：

| 项目 | 结果 |
|---|---|
| `10.3.34.238:2222 -> WSL:22` | TCP `Open=True` |
| `10.3.34.238:8188 -> WSL:8188` | TCP `Open=True` |
| `10.3.34.238:9201 -> WSL:9201` | TCP `Open=True` |
| Node Agent `/health/ready` | `{"status":"ready","control_script":"/usr/local/sbin/gpu-node-ctl"}` |
| 4090 双向心跳 | `node.heartbeat_accepted` |
| 4090 对 identity/system/GPU metrics 拉取 | HTTP 200 |
| ComfyUI 容器 | Running / Healthy |
| Node Agent 容器 | Running / Healthy |
| SSH 自愈维护任务 | Enabled / Running |

端口映射目标仍为 WSL `eth0` 的 `172.24.3.33`：

```text
10.3.34.238:2222 -> 172.24.3.33:22
10.3.34.238:8188 -> 172.24.3.33:8188
10.3.34.238:9201 -> 172.24.3.33:9201
```

## 2. 卡死根因结论

这次不是 C 盘不足、Linux 根分区不足、Linux 内存耗尽或 swap 耗尽。

上一轮 WSL boot 的内核日志在 GPU 作业期间出现高频错误：

```text
misc dxg: dxgk: create_existing_sysmem: establish_gpadl failed: -122
misc dxg: dxgk: dxgkio_create_allocation: Ioctl failed: -12
```

`-12` 是内核分配层的 `ENOMEM`。这里发生在 WSL `dxgkrnl` / Hyper-V GPADL / GPU 共享内存映射层，不能用 Linux `free` 尚有空闲来排除。错误在短时间内连续爆发后，WSL 管理通道失去响应，而已经建立的少量转发连接可能短暂存活，表现为“端口偶尔还在，但 wsl.exe 和新任务卡死”。

当前恢复后的首次 GPU 初始化还记录了：

```text
memcpy: detected field-spanning write ... drivers/hv/dxgkrnl/dxgvmbus.c:3095
WARNING ... dxgvmb_send_wait_sync_object_gpu
process: python3.11
```

结合 ComfyUI 日志，4070 Ti 使用 12 GB 低显存模式运行相同工作流时发生大量模型装入/卸载与 CPU offload：

```text
Total VRAM 12282 MB
Set vram state to: LOW_VRAM
6820.02 MB loaded, 2176.00 MB offloaded
Unloaded partially: 6820.02 MB freed ... lowvram patches: 231
```

因此本次直接原因是：**12 GB 4070 Ti 在 WSL2 中执行高显存/高共享内存压力工作流时，触发 Windows GPU 驱动与 WSL `dxgkrnl` 的分配失败风暴，最终拖死 WSL VM。** 容器原有 `restart: unless-stopped` 又会在 WSL/Docker 启动后立即初始化 CUDA，使恢复过程也存在重复触发风险。

## 3. 已部署的防复发措施

### 3.1 ComfyUI 不再随 Docker 立即启动

已将当前容器和 WSL compose overlay 中 ComfyUI 的 restart policy 改为：

```text
restart=no
```

Node Agent、SSH 和其他控制服务不受影响，仍可先上线并接受 4090 检查。

### 3.2 WSL 启动后延迟并验证 GPU

新增 systemd 单元：

```text
gpu-control-comfyui-delayed-start.service
```

行为：

1. 等待 Docker；
2. 再等待 30 秒，让 WSL `dxg` 和 Windows GPU 通道稳定；
3. 每 2 秒执行一次 WSL NVIDIA 探测，最多 30 次；
4. 只有 `nvidia-smi` 成功后才启动 ComfyUI。

### 3.3 DXG 错误风暴自动熔断

新增常驻单元：

```text
gpu-control-dxg-guard.service
```

它实时跟踪 kernel journal，只匹配已经确认会导致本次故障的两类错误：

```text
create_existing_sysmem: establish_gpadl failed: -122
dxgkio_create_allocation: Ioctl failed: -12
```

15 秒内累计 3 次即熔断。熔断动作仅为：

```text
docker stop --time 5 gpu-control-node-comfyui-1
```

它不会关闭 WSL、SSH、9201 Node Agent，也不会删除任务或数据。这样 4090 主控仍能收到节点状态并通过 SSH 处理故障，而不是失去整台 4070 Ti。

当前单元状态：

```text
gpu-control-comfyui-delayed-start.service: active / enabled
gpu-control-dxg-guard.service: active / enabled
```

## 4. 4090 主控必须采用的调度约束

4070 Ti 是 12 GB 节点，不能把已在 22/24 GB 节点通过的工作流默认视为可直接等价调度。主控侧应：

1. 将该节点标记为 12 GB constrained/canary worker；
2. 只分配已经在此节点完成显存峰值验证的动画/抠图工作流；
3. 对预计显存超过该节点安全门槛的作业在调度前拒绝，而不是依赖 ComfyUI 运行时 offload 硬扛；
4. 若 8188 失联而 9201 与 2222 正常，应判断为本机 GPU 熔断，不要高频重启 ComfyUI；
5. 熔断后先将节点置为 `DRAINING`，读取下列日志，确认无持续 `dxg` 错误后再恢复；
6. 镜像/compose 更新必须保留 WSL overlay 的 `restart: "no"`，否则会重新引入开机即 CUDA 初始化问题。

## 5. 运维检查与恢复命令

4090 主控连接：

```bash
ssh -p 2222 gpucontrol@10.3.34.238
```

检查保护与故障日志：

```bash
systemctl status gpu-control-dxg-guard.service --no-pager
journalctl -u gpu-control-dxg-guard.service -n 100 --no-pager
journalctl -k --since '-15 min' | grep -E 'establish_gpadl failed: -122|dxgkio_create_allocation: Ioctl failed: -12'
docker ps -a --filter name=gpu-control-node-comfyui-1
```

确认主控已将节点置为 `DRAINING` 且内核不再新增错误后，人工恢复 ComfyUI：

```bash
nvidia-smi
sudo docker start gpu-control-node-comfyui-1
```

不要通过 `docker update --restart=unless-stopped` 绕过延迟启动与熔断设计。

## 6. 落地文件

仓库文件：

```text
deployment/gpu-control-4070/gpu-control-dxg-guard.py
deployment/gpu-control-4070/gpu-control-dxg-guard.service
deployment/gpu-control-4070/gpu-control-comfyui-delayed-start.service
scripts/Install-4070WslGpuGuard.ps1
```

WSL 安装位置：

```text
/usr/local/sbin/gpu-control-dxg-guard.py
/etc/systemd/system/gpu-control-dxg-guard.service
/etc/systemd/system/gpu-control-comfyui-delayed-start.service
/opt/gpu-control/deploy/gpu-node/compose.wsl.yaml
```

Windows 自愈任务继续使用：

```text
GPUControl-4070-WSL-Maintainer
C:\ProgramData\GPUControl\Maintain-4070WslSsh.ps1
C:\ProgramData\GPUControl\Update-4070WslSshProxy.ps1
```

## 7. 当前可交付给 4090 的一句话状态

```text
4070 Ti WSL 已恢复；10.3.34.238 的 2222/8188/9201 均实测可达，9201 ready，双向心跳 accepted，ComfyUI healthy。已部署 30 秒 GPU 延迟启动和 dxg 分配错误熔断：若再出现 -122/-12 错误风暴，只停止 ComfyUI，保留 SSH、Node Agent 与 WSL；请主控将该 12 GB 节点保持 constrained/canary 调度并保留 compose.wsl.yaml 的 restart=no。
```
