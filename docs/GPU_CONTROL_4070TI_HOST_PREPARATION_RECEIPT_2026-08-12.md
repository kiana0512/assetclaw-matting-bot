# RTX 4070 Ti 第四 GPU 节点主机预处理回执与 4090 对接清单

> 回执状态：`PREPARED_NOT_REGISTERED / PRODUCTION_NOT_APPROVED`  
> 采集时间：`2026-08-12T07:54:19Z`（北京时间 `2026-08-12 15:54:19`）  
> 节点 ID：`worker-4070ti-animation-host-01`  
> Windows DNS hostname：`DAC3OZhangqichao`  
> 当前稳定路由地址：`10.3.34.238/24`（DHCP，尚未确认网关保留）  
> 4090 控制中心：`10.3.34.11:443`  
> 本回执不包含任何 API Key、HMAC secret、Windows 管理员凭据或其他 secret。

## 1. 回执依据与结论

实施依据：

- 原始手册：`C:\Users\zhangqichao\Downloads\115_2026-08-12_4070TI_WSL2_PREPARATION_AND_INTEGRATION_HANDOFF.md`
- 原始手册 SHA-256：`38BC40024CF9CEFB1EA8460B582F97FBCAE459C3FB300A139CCC18AEAC54492B`
- GPU Control 已知控制面基线：`1.5.12@093ae8b7966ae5beb86990c7881c11d4c24d4e51`
- GPU Control 手册记录仓库 HEAD：`a7120a44a138d53ee0380949a2053cdebede94d9`

4070 主机侧所有不依赖 4090 专用交付物的预处理已经完成：

- WSL 2、Ubuntu 22.04.5、systemd、资源上限、ext4 目录骨架已完成并复验。
- WSL 内能够看到与 Windows 完全相同的 RTX 4070 Ti GPU UUID。
- GPU Control LAN CA 已按指定 SHA-256 校验并加入 WSL 系统信任库。
- WSL 内不使用 `-k/--insecure` 即可访问 4090 `/health/live`。
- Windows 上的 AssetClaw 动画管家和秋叶 ComfyUI 未被改造或纳管。
- 未安装 Docker，未创建 `gpucontrol` 账户，未注册节点，未写入 HMAC，未开放集群入站端口。

当前不能进入生产注册。原因不是本机授权不足，而是 4090 侧尚未交付不可猜测的版本、digest、专用身份和四节点适配代码；详见第 8 节。

## 2. 重要协议纠正

本回执取代此前草案中“Worker 主动拉取任务、双向租约心跳”的提议。正式对接必须复用 GPU Control 现有协议，不新增一套 Worker Pull 协议：

1. AssetClaw 只向 `https://10.3.34.11:443` 提交父批次。
2. 4090 Scheduler 持久化并管理 Jobs、Attempts 和 Leases。
3. 4070 的 Node Agent 约每 5 秒向 `POST /api/v1/nodes/heartbeat` 上报 HMAC 签名心跳。
4. 4090 Scheduler 主动连接 4070 节点的 ComfyUI TCP `8188` 推送任务。
5. 4090 受控运维主动连接 Node Agent TCP `9201`。
6. NodeLease 是 4090 控制面内部状态，不要求 4070 Worker 再实现一套拉取/续租 API。
7. Windows 物理地址用于路由；WSL NAT 地址是动态实现细节，不得登记为节点固定地址。

首次心跳成功不得自动激活节点。节点初始状态必须是 `DISABLED` 或 `DRAINING`，并保持 `max_concurrency=1`。

## 3. 已完成的本机部署

### 3.1 Windows 与 WSL

| 项目 | 实测值 | 状态 |
|---|---|---|
| Windows | Windows 10 Pro 22H2，build `19045.3803` | 通过 |
| Windows C 盘 | 总计 931.12 GiB，清理安装临时包后可用 375.31 GiB | 通过 |
| DNS hostname | `DAC3OZhangqichao` | 通过 |
| NetBIOS/环境名 | `DAC3OZHANGQICHA`（15 字符截断，正常） | 记录 |
| WSL 应用版本 | `2.7.11.0` | 通过 |
| WSL kernel | `6.18.33.2-microsoft-standard-WSL2` | 通过 |
| WSL 默认模式 | 2 | 通过 |
| 发行版注册名 | `Ubuntu` | 通过；官方包默认名称 |
| Linux 发行版 | `Ubuntu 22.04.5 LTS` | 通过 |
| systemd | `running`，PID 1=`systemd` | 通过 |
| WSL CPU | `12` | 通过 |
| WSL 内存 | `33,655,746,560` bytes，约 31.34 GiB 可见 | 通过 |
| WSL swap | `17,179,869,184` bytes，16 GiB | 通过 |
| 根文件系统 | `ext4` | 通过 |
| 根文件系统可用 | `1,024,407,539,712` bytes，约 954 GiB | 通过 |

已部署 `%UserProfile%\.wslconfig`：

```ini
[wsl2]
memory=32GB
processors=12
swap=16GB
localhostForwarding=true
```

已部署 `/etc/wsl.conf`：

```ini
[boot]
systemd=true
```

### 3.2 Ubuntu 安装来源与更新

| 项目 | 值 |
|---|---|
| Microsoft WSL 包地址 | `https://aka.ms/wslubuntu2204` |
| Appx 包版本 | `CanonicalGroupLimited.Ubuntu 2204.1.7.0` |
| Appx 大小 | `1,116,834,851` bytes |
| Microsoft Content-MD5 (Base64) | `LU+Yrp5/GSFyLaYg73K2Qw==`，校验一致 |
| Appx SHA-256 | `6AD6D88763451A50F98F2469CE80464D666204C08D07F8F6A89E0D5CA05B097A` |
| 初始镜像 | Ubuntu 22.04.1 LTS |
| 更新后 | Ubuntu 22.04.5 LTS |
| APT 镜像 | `https://repo.huaweicloud.com/ubuntu/`，Jammy HTTPS mirror |
| 原始源备份 | `/etc/apt/sources.list.pre-gpu-control-4070` |
| 普通升级结果 | `0 upgraded, 0 newly installed, 0 to remove, 7 not upgraded` |

说明：Canonical 官方单连接下载链路发生无 CPU/IO 增长的卡死，后改用微软商店包并行 HTTP Range 下载。所有分段按长度验证，合并后同时通过 Microsoft Content-MD5 和本地 SHA-256 校验。

安装完成后已删除 `1,116,834,851` bytes 的 Appx 临时包和 10 MiB Range 测速文件；需要时可从上述 Microsoft 地址重新下载。

### 3.3 GPU

| 项目 | Windows | WSL | 状态 |
|---|---|---|---|
| GPU | NVIDIA GeForce RTX 4070 Ti | NVIDIA GeForce RTX 4070 Ti | 一致 |
| UUID | `GPU-70c028e4-dd91-4337-8f96-29daa437d1c3` | 同左 | 一致 |
| NVIDIA driver | `576.52` | `576.52` | 一致 |
| VRAM | `12282 MiB` | `12282 MiB` | 一致 |
| Compute Capability | `8.9` | 由 Windows 已确认 | 记录 |

本项只证明 WSL GPU 透传正常，不代表当前 22 GB 最低显存门槛的生产工作流已经能在 12 GB 显存上运行。

### 3.4 ext4 路径骨架

以下目录已创建在 WSL ext4 根盘，当前均为 `0755 root:root`，等待正式安装脚本按 UID/GID 10001 设置必要的最小写权限：

| 路径 | 当前权限 | 用途 |
|---|---|---|
| `/opt/gpu-control` | `0755 root:root` | 经审核 GPU Control 交付代码 |
| `/opt/imageclip` | `0755 root:root` | 批准的外部管线副本 |
| `/opt/imageclip/models` | `0755 root:root` | 模型只读挂载源 |
| `/srv/comfyui/runtime` | `0755 root:root` | input/output/temp/user |
| `/srv/gpu-control/images` | `0755 root:root` | 离线镜像包 |

未提前创建 `gpucontrol` 账户；`getent passwd gpucontrol` 无结果。未提前创建 Docker 组；`getent group docker` 无结果。

## 4. 网络与 TLS 回执

### 4.1 固定身份

| 项目 | 实测值 | 备注 |
|---|---|---|
| Windows IPv4 | `10.3.34.238/24` | DHCP 分配；需在网关保留 |
| 物理 MAC | `34:5a:60:47:c6:1d` | Intel I225-V |
| 网卡速率 | 1 Gbps | 当前实测 |
| 默认网关 | `10.3.34.1` | 同时是 DHCP Server |
| DNS | `10.3.254.217`, `10.1.254.217` | Windows 实测 |
| 当前 WSL NAT IPv4 | `172.24.3.33` | 动态；不得登记为节点固定地址 |
| WSL NAT gateway/DNS proxy | `172.24.0.1` | 动态实现细节 |
| WSL machine-id | `5535424f509445a99b69f80763573586` | 诊断参考，不作为唯一节点身份 |
| 控制中心 | `10.3.34.11:443` | TCP 与 TLS 均通过 |

### 4.2 TLS

| 项目 | 结果 |
|---|---|
| LAN CA SHA-256 | `ad4a4dbd95bb789be03451ff0c25b2bc65dfe170428bd675789c2ebba1e6dc2b` |
| WSL CA 路径 | `/usr/local/share/ca-certificates/gpu-control-lan-ca.crt` |
| 系统信任库 | 已执行 `update-ca-certificates` |
| 无 insecure 健康检查 | `curl https://10.3.34.11/health/live` 返回 `{"status":"live"}` |

禁止后续使用 `-k`、`--insecure` 或关闭证书校验规避 TLS 问题。

### 4.3 当前入站面

- `netsh interface portproxy show all`：空。
- 集群端口 `8188/9201/9100/9400/2222`：本次未新增监听、未新增 portproxy。
- 当前只观察到 AssetClaw WebUI `127.0.0.1:5180` 和 Gateway `127.0.0.1:7865` 的 loopback 监听。
- `http://127.0.0.1:5180/`：HTTP 200。
- `http://127.0.0.1:7865/health`：HTTP 200。
- Windows Docker：`NOT_INSTALLED`；WSL Docker：`NOT_INSTALLED`。

## 5. 业务与安全边界确认

1. AssetClaw 动画管家仍在 Windows 4070 Ti 主机运行，继续拥有动画业务状态和后处理职责。
2. 所有生产抠图父批次最终只提交给 4090 GPU Control；本次未切换生产流量。
3. 秋叶 ComfyUI 原样保留为人工冷备，不注册、不自动调度、不作为静默回退。
4. 4090 只能控制 WSL 内正式 Worker 服务，不能获得 Windows 宿主、AssetClaw、飞书、P4、Unity 或秋叶 ComfyUI 的控制权。
5. 未复用 AssetClaw API Key，未创建自定义 API Key，未复用其他节点 HMAC。
6. 未打开 Docker TCP 2375，未共享 Windows 管理员凭据。

## 6. 4070 主机事实 YAML

```yaml
handoff:
  collected_at_utc: "2026-08-12T07:54:19Z"
  operator: "Codex on local host with user authorization"
  status: "PREPARED_NOT_REGISTERED"

windows:
  hostname: "DAC3OZhangqichao"
  netbios_name: "DAC3OZHANGQICHA"
  product_name: "Windows 10 Pro"
  version: "22H2"
  build: "19045.3803"
  reboot_after_wsl_features: true
  nvidia_driver: "576.52"
  gpu_name: "NVIDIA GeForce RTX 4070 Ti"
  gpu_uuid: "GPU-70c028e4-dd91-4337-8f96-29daa437d1c3"
  gpu_vram_mib: 12282
  assetclaw_unchanged: true
  qiuyue_comfyui_unchanged: true

network:
  ipv4: "10.3.34.238"
  cidr: "10.3.34.238/24"
  physical_mac: "34:5a:60:47:c6:1d"
  gateway: "10.3.34.1"
  dhcp_server: "10.3.34.1"
  dhcp_reservation_confirmed: false
  dns_servers: ["10.3.254.217", "10.1.254.217"]
  current_wsl_nat_ipv4: "172.24.3.33"
  controller_443_reachable: true
  controller_tls_verified: true
  ca_sha256: "ad4a4dbd95bb789be03451ff0c25b2bc65dfe170428bd675789c2ebba1e6dc2b"
  inbound_cluster_ports_opened: []

wsl:
  wsl_version: "2.7.11.0"
  kernel_version: "6.18.33.2-microsoft-standard-WSL2"
  distribution_registration_name: "Ubuntu"
  distribution: "Ubuntu"
  distribution_version: "22.04.5 LTS"
  wsl_version_mode: 2
  systemd_active: true
  gpu_visible: true
  gpu_uuid_seen: "GPU-70c028e4-dd91-4337-8f96-29daa437d1c3"
  root_filesystem_type: "ext4"
  root_free_bytes: 1024407539712
  memory_limit_gib: 32
  processor_limit: 12
  swap_gib: 16
  gpucontrol_account_exists: false
  gpucontrol_uid: null

runtime:
  docker_preexisting: false
  docker_engine_version: "NOT_INSTALLED"
  docker_compose_version: "NOT_INSTALLED"
  containerd_version: "NOT_INSTALLED"
  nvidia_container_toolkit_version: "NOT_INSTALLED"
  note: "Do not install latest; wait for GPU Control locked delivery."

security:
  docker_2375_closed: true
  qiuyue_8188_not_exposed_to_cluster: true
  no_secret_in_report: true
  no_windows_admin_credential_shared: true

evidence:
  source_handoff_sha256: "38BC40024CF9CEFB1EA8460B582F97FBCAE459C3FB300A139CCC18AEAC54492B"
  ubuntu_appx_sha256: "6AD6D88763451A50F98F2469CE80464D666204C08D07F8F6A89E0D5CA05B097A"
  receipt_filename: "GPU_CONTROL_4070TI_HOST_PREPARATION_RECEIPT_2026-08-12.md"
  blockers:
    - "DHCP reservation on 10.3.34.1 is not confirmed"
    - "GPU Control four-node repository adaptation is not delivered"
    - "Dedicated node HMAC and disabled node record are not delivered"
    - "Locked Docker/containerd/Compose versions and package hashes are not delivered"
    - "ComfyUI image digest, model manifest and official install/rollback scripts are not delivered"
    - "12 GB unchanged-workflow canary has not passed"
```

## 7. 目标节点登记值

4090 侧创建节点记录时必须使用以下值并 fail closed：

```yaml
node_id: "worker-4070ti-animation-host-01"
windows_host: "DAC3OZhangqichao"
route_address: "10.3.34.238"
physical_mac: "34:5a:60:47:c6:1d"
gpu_uuid: "GPU-70c028e4-dd91-4337-8f96-29daa437d1c3"
gpu_name: "NVIDIA GeForce RTX 4070 Ti"
gpu_vram_mib: 12282
pool_before_canary: null
pool_after_canary: "PRIMARY"
initial_mode: "DISABLED"
max_concurrency: 1
comfyui_port: 8188
node_agent_port: 9201
metrics_ports: [9100, 9400]
controller: "10.3.34.11:443"
```

不得把当前 WSL NAT 地址 `172.24.3.33` 写入节点数据库。若 4090 侧只能接受 `DRAINING`，可用 `DRAINING` 替代 `DISABLED`，但绝不能首次心跳后自动转为 `ACTIVE`。

## 8. 4090 GPU Control 团队必须回填/交付

以下内容收到前，4070 侧不会自行猜测或安装：

### 8.1 四节点控制面代码

请提供包含以下改动的 GPU Control commit 和变更说明：

1. `scripts/bootstrap_nodes.py` 的 `EXPECTED_IDS` 加入 `worker-4070ti-animation-host-01`。
2. `scripts/generate_env.py` 生成第四节点 env、inventory、监控 targets。
3. `packages/gpu_control_core/settings.py` 增加 4070 专用 HMAC 字段，禁止回退共享 secret。
4. `.env.example` 增加 4070 host 和专用 HMAC 占位字段。
5. Web 调度页节点排序支持第四节点。
6. WSL Prometheus 告警不再只匹配 `worker-3090-b`。
7. smoke/load 脚本和运行时文案扩展为四节点，同时保留三节点历史基线。

请回填：

```yaml
gpu_control_delivery:
  version: ""
  source_revision: ""
  four_node_commit: ""
  bootstrap_nodes_sha256: ""
  generate_env_sha256: ""
  settings_sha256: ""
  env_example_sha256: ""
  smoke_test_sha256: ""
```

### 8.2 节点身份与密钥

4090 团队必须：

- 在数据库预创建 `worker-4070ti-animation-host-01`。
- 初始模式设为 `DISABLED` 或 `DRAINING`。
- `max_concurrency=1`。
- 绑定 `10.3.34.238`、物理 MAC 和 GPU UUID。
- 生成仅供该节点使用的独立 HMAC；不得复用 3090-B、AssetClaw 或共享 secret。
- 通过安全通道交付 secret；回执文档只记录 secret ID/fingerprint，不记录明文。

请回填：

```yaml
node_identity_delivery:
  record_precreated: false
  initial_mode: ""
  max_concurrency: 1
  dedicated_hmac_secret_id: ""
  dedicated_hmac_fingerprint: ""
  secure_delivery_channel: ""
  first_heartbeat_auto_activates: false
```

### 8.3 锁定运行时

已知但不足以安装的锁定项：

- NVIDIA Container Toolkit：`1.19.1-1`
- 容器 Python：`3.11.13`
- PyTorch：`2.7.1`
- CUDA Runtime：`12.8.1`
- ComfyUI：`0.28.0@700821e1364eaab0e8f21c538a2131719fec57bf`
- 现有可漂移 tag：`registry.local:5000/gpu-control/comfyui:projects-0.2.3`

仍必须提供：

```yaml
runtime_delivery:
  docker_engine_version: ""
  docker_engine_packages_sha256: []
  docker_compose_plugin_version: ""
  docker_compose_package_sha256: ""
  containerd_version: ""
  containerd_package_sha256: ""
  nvidia_container_toolkit_version: "1.19.1-1"
  nvidia_container_toolkit_package_sha256: ""
  approved_cuda_test_image_digest: ""
  comfyui_image_tag: "registry.local:5000/gpu-control/comfyui:projects-0.2.3"
  comfyui_image_repo_digest: ""
  node_agent_version: ""
  node_agent_source_revision: ""
  compose_sha256: ""
  install_script_sha256: ""
  rollback_script_sha256: ""
```

### 8.4 ImageClip、模型和管线身份

不得为了 12 GB 显存自行修改工作流、模型、提示词、分辨率、输出格式或节点参数。请交付：

```yaml
pipeline_delivery:
  imageclip_commit: ""
  pipeline_sha256: ""
  workflow_name: "imageclip-rgba"
  workflow_sha256: ""
  approved_min_vram_mb: 22000
  model_manifest_sha256: ""
  models:
    - relative_path: ""
      size_bytes: 0
      sha256: ""
```

### 8.5 Windows WSL 服务、端口转发与防火墙

请复用并交付 3090-B 已验证、可重复执行、可重启自愈的正式脚本及 SHA-256：

- WSL systemd：`docker`、`containerd`、`gpu-node-agent`。
- Windows Keepalive：保持指定 `Ubuntu` 发行版运行。
- Windows Watchdog：发现 WSL `eth0` 地址改变时修复 portproxy。
- portproxy：Windows `10.3.34.238` 的 `8188/9201/9100/9400` 转发到当前 WSL IPv4。
- Windows Firewall：上述端口来源只允许 `10.3.34.11`。
- 可选 SSH 2222：只允许批准运维来源，只进入 WSL 专用账户。
- 日志可以记录旧/新 WSL IP，不得记录 secret。

请回填：

```yaml
windows_wsl_delivery:
  keepalive_script_sha256: ""
  watchdog_script_sha256: ""
  portproxy_script_sha256: ""
  firewall_script_sha256: ""
  service_install_script_sha256: ""
  approved_controller_source_cidr: "10.3.34.11/32"
  ssh_2222_enabled: false
  ssh_approved_source_cidrs: []
```

### 8.6 网络管理

请网络管理员在 `10.3.34.1` 确认：

```yaml
dhcp_reservation:
  mac: "34:5a:60:47:c6:1d"
  ipv4: "10.3.34.238"
  confirmed: false
  confirmed_by: ""
  confirmed_at_utc: ""
```

### 8.7 4090 侧最终回执

请在返回本机前至少给出：

- 上述所有版本、commit、digest 和脚本 SHA-256。
- 节点数据库预创建证据，状态必须非 ACTIVE。
- 4090 到 `10.3.34.238:8188/9201/9100/9400` 的连通验收方法。
- HMAC 心跳 canonical string、签名算法、时间漂移上限和重放保护规则。
- ComfyUI push/job callback 的现有协议版本，不新增 Worker Pull 协议。
- 三节点基线回归结果，证明加入第四节点不会破坏现有 4090/3090-A/3090-B。
- 4070 一键禁用与卸载/回滚步骤。

## 9. 12 GB 显存准入门槛

当前批准工作流 `imageclip-rgba` 声明 `min_vram_mb=22000`，本机只有 `12282 MiB`，因此节点在未通过 canary 前不具备生产资格。

4090 交付完整运行时后，按以下顺序执行且保持原工作流不变：

1. 节点保持 `DISABLED/DRAINING`，运行本机容器自检。
2. 定向 1 帧 canary，记录峰值显存、耗时、输出 SHA/视觉结果和日志。
3. 定向 6 帧 canary，检查持续显存、缓存和错误恢复。
4. 定向 30 帧 canary，检查 OOM、租约、重试、输出完整性和 Windows/AssetClaw 稳定性。
5. 只有三组 canary 全部通过，并由 GPU Control 负责人签字，才允许加入 `PRIMARY`。

禁止通过以下方式“过测试”：

- 修改 workflow、模型、提示词、分辨率或输出格式。
- 静默降级为另一抠图管线。
- 放宽现有 22 GB 能力门禁但不保留可审计例外。
- 自动把失败任务转给秋叶 ComfyUI。

若不通过，只禁用 4070 节点，原三节点继续生产。

## 10. 下一阶段执行顺序

1. 4090 团队完成第 8 节并返回带 SHA/digest 的交付 MD。
2. 网络管理员确认 DHCP reservation。
3. 4070 侧校验所有交付哈希，再执行官方 Docker/容器安装脚本。
4. 安装专用 `gpucontrol` 账户，UID/GID 必须由正式脚本设置为 `10001`。
5. 安装 systemd 服务、Keepalive、Watchdog、portproxy 和最小防火墙规则。
6. 节点以 `DISABLED/DRAINING` 注册，验证专用 HMAC 心跳和 4090 主动推送。
7. 执行 1/6/30 帧 unchanged-workflow canary。
8. 通过后才讨论 `PRIMARY` 与 AssetClaw 全远端生产切换。

在第 8 节没有完整回填前，本机当前状态就是正确的停止点。
