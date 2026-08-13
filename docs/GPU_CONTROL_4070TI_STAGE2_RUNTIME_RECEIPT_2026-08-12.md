# RTX 4070 Ti Stage 2 锁定运行时部署回执

> 状态：`RUNTIME_INSTALLED_AWAITING_ARTIFACTS / NOT_REGISTERED`  
> 采集时间：`2026-08-12T08:15:06Z`  
> 节点：`worker-4070ti-animation-host-01`  
> WSL hostname：`worker-4070ti-wsl`  
> 4090 控制中心：`10.3.34.11`  
> 本回执不包含 HMAC、API Key、密码、私钥或其他 secret。

## 1. 实施依据

- Stage 2 实施书：`116_2026-08-12_4070TI_EXACT_RUNTIME_AND_STAGE2_EXECUTION.md`
- Stage 2 实施书 SHA-256：`272D262B562D664614B92CBD22423B27A2FF143BEB00395DE5FF859232CA2131`
- 上一阶段回执 SHA-256：`5C160711E384BE8A273AC6CF2EF0C678D83C09B23DD1A6EAB7B8B24F226B710D`
- 上一阶段本机实测 SHA 与 Stage 2 引用值完全一致。

## 2. 已完成结果

### 2.1 锁定运行时

| 组件 | 已安装版本 | 状态 |
|---|---|---|
| Docker client/server | `29.6.2 / 29.6.2` | 通过 |
| `docker-ce` | `5:29.6.2-1~ubuntu.22.04~jammy` | 通过 |
| `docker-ce-cli` | `5:29.6.2-1~ubuntu.22.04~jammy` | 通过 |
| containerd | `2.2.6`，commit `11ce9d5f3c68c941867e82890e93e815c1304f1b` | 通过 |
| `containerd.io` | `2.2.6-1~ubuntu.22.04~jammy` | 通过 |
| Docker Buildx | `0.35.0-1~ubuntu.22.04~jammy` | 通过 |
| Docker Compose | `5.3.1` | 通过 |
| NVIDIA Container Toolkit | `1.19.1-1` | 通过 |
| NVIDIA Container Toolkit Base | `1.19.1-1` | 通过 |
| libnvidia-container tools/runtime | `1.19.1-1` | 通过 |

Docker 官方仓库在实施时已有更高 Candidate，但 Stage 2 锁定版本仍全部可用。按用户要求，以“版本一致、功能稳定”为准，使用 `package=锁定版本` 下载和安装；安装前对九个 `.deb` 的文件大小与 SHA-256 逐项校验，全部与 Stage 2 第 3 节一致。

为防止后续 APT 漂移，以下九个包已执行 `apt-mark hold`：

```text
containerd.io
docker-buildx-plugin
docker-ce
docker-ce-cli
docker-compose-plugin
libnvidia-container-tools
libnvidia-container1
nvidia-container-toolkit
nvidia-container-toolkit-base
```

### 2.2 `.deb` 校验证据

| 包 | 实测大小 | 实测 SHA-256 | 匹配 |
|---|---:|---|---|
| `docker-ce` | 23,312,180 | `abda813589be3a9953c72181d2d1fa6064eb64966f917d70fe8996d9af485fc6` | 是 |
| `docker-ce-cli` | 16,889,272 | `5ad09e85f123841a0ced843f748e4ec52209f1773a770bdb39eb64f24eff6ba5` | 是 |
| `containerd.io` | 23,621,096 | `a5fd776785cf8482d1a342479d5eed53cccd6daf534ef129012797b6e817dee6` | 是 |
| `docker-buildx-plugin` | 17,205,924 | `62b77b009803ebea4f9bc3cdecd00e3bf6c88266a3525046105c4449ceea94c7` | 是 |
| `docker-compose-plugin` | 8,099,832 | `00784bd434f1fadde20cc047f5c88d97c9f2d17c82cef88ac69160421c553f2b` | 是 |
| `nvidia-container-toolkit` | 1,334,076 | `e66acb5b33420a8417429cd217abc8400b4a409a2ae17a3852cf6feb34b5c8e6` | 是 |
| `nvidia-container-toolkit-base` | 5,608,524 | `b6c5b4e77a28cde0197cc0e64edf75538604775d9f8aea502cef667e7e5b2132` | 是 |
| `libnvidia-container-tools` | 20,816 | `5642763d51961a2295dff09990048a5dcee81edbea2a8c5084e47b09ccf17268` | 是 |
| `libnvidia-container1` | 1,191,204 | `d73bb582af893135198ef81cb22135c790a75d2ad72910446477c6c4430f3e6b` | 是 |

### 2.3 服务与安全

| 项目 | 实测结果 |
|---|---|
| `docker.service` | `active` |
| `containerd.service` | `active` |
| Docker data-root | `/var/lib/docker` |
| Docker data-root 文件系统 | `ext4` |
| NVIDIA Docker runtime | 已写入 `/etc/docker/daemon.json` |
| NVIDIA CDI | `nvidia.com/gpu=all`，发现 1 个设备 |
| Docker TCP 2375/2376 | 未监听 |
| 8188/9201/9100/2222/9400 | 未监听 |
| Windows portproxy | 空 |
| Windows 集群入站规则 | 本轮未创建 |
| 根文件系统可用 | `1,023,794,761,728` bytes |

`/etc/docker/daemon.json` 只配置 NVIDIA runtime，没有 TCP hosts：

```json
{
  "runtimes": {
    "nvidia": {
      "args": [],
      "path": "nvidia-container-runtime"
    }
  }
}
```

### 2.4 账户、hostname 与目录

```text
gpucontrol:x:1000:1000:,,,:/home/gpucontrol:/bin/bash
uid=1000(gpucontrol) gid=1000(gpucontrol) groups=1000(gpucontrol),999(docker)
hostname=worker-4070ti-wsl
```

目录状态：

| 路径 | UID:GID | 权限 |
|---|---:|---:|
| `/opt/gpu-control` | `1000:1000` | `0755` |
| `/opt/imageclip` | `1000:1000` | `0755` |
| `/opt/imageclip/models` | `1000:1000` | `0755` |
| `/srv/gpu-control/images` | `1000:1000` | `0755` |
| `/srv/comfyui/runtime` | `10001:10001` | `0775` |
| `/srv/comfyui/runtime/input` | `10001:10001` | `0775` |
| `/srv/comfyui/runtime/output` | `10001:10001` | `0775` |
| `/srv/comfyui/runtime/temp` | `10001:10001` | `0775` |
| `/srv/comfyui/runtime/user/default/workflows` | `10001:10001` | `0775` |

### 2.5 未改变边界

- AssetClaw WebUI `127.0.0.1:5180`：HTTP 200。
- AssetClaw Gateway `127.0.0.1:7865/health`：HTTP 200。
- 秋叶 ComfyUI 未修改、未纳管、未暴露给集群。
- WSL 到 `https://10.3.34.11/health/live` 仍在系统 CA 校验下返回 `{"status":"live"}`。
- 未安装 Node Agent，未生成 `.env`，未注入 HMAC，未注册节点，未启用生产流量。

## 3. 当前等待 4090 传输的制品

本机对应目录目前为空。请 4090 GPU Control 维护方通过批准的局域网文件传输方式交付以下内容。

### 3.1 ComfyUI 不可变镜像归档

源文件：

```text
4090: /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz
4070: /srv/gpu-control/images/comfyui-projects-registry-0.2.3.tar.gz
```

必须满足：

```yaml
archive_size_bytes: 8271225047
archive_sha256: "20aa6ffec448ad2615916db49b5411b5974f01c1a51e61081160b544d6966586"
image_id: "sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea"
repo_digest: "registry.local:5000/gpu-control/comfyui@sha256:d76e54a137d7b630de4503e0f0b16fa4441b25f6a5b5e1561d7fb1615eca36ea"
```

传输完成后，4070 侧将校验大小/SHA，执行 `docker load`，再用该 digest 做 C0 NVIDIA/PyTorch/CUDA 自检；不会启动常驻 ComfyUI。

### 3.2 ImageClip 批准副本与四个模型

请同步批准的 `/opt/imageclip` 副本，身份必须为：

```yaml
imageclip_commit: "691770cd6a59fd7c51391456fe900dc57a313233"
combined_pipeline_sha256: "00e7104762f0a1fdf3a4c20e043bec2b9f088132452d5a5ce4302ba268edac0b"
api_template_sha256: "cfe65f832f831f8003c3b7d9d4406f84af1ab53a5cedd108046e0d356ba8a94a"
workflow_manifest_sha256: "a9456442829bb1fcc77f82e8c2e5006228f1dde02563d77c2022bed5b01e53c0"
model_manifest_sha256: "4932d81a5a73ba8ea9c4afe5cf04a5dc48c8a506845a79d2a73460d360a540ee"
```

四个模型目标路径：

| 相对 `/opt/imageclip/models` 路径 | 字节数 | SHA-256 |
|---|---:|---|
| `unet/flux-2-klein-9b-Q6_K.gguf` | 7,865,424,160 | `1cd667293607431e79c9e7e01ecf5c602bd00539c2c0f49d4817a62998b5fe98` |
| `text_encoders/qwen_3_8b_fp8mixed.safetensors` | 8,664,848,742 | `abad16806e0cbabc54e0325d6565847443fe396d5f0be38bb3cd3fe75a1201d6` |
| `vae/flux2-vae.safetensors` | 336,213,556 | `d64f3a68e1cc4f9f4e29b6e0da38a0204fe9a49f2d4053f0ec1fa1ca02f9c4b5` |
| `loras/Koutu_Flux2klein_v2_000007250.safetensors` | 165,704,392 | `79838cfe96bc7508f4d5e6aca6588191eda333ec983a3b202afe694857ccd27d` |

请不要只回复“已同步”；需要提供传输方式、源主机、完成时间和源端 SHA 复验结果。

## 4. Stage 2 YAML

```yaml
stage2_runtime_receipt:
  collected_at_utc: "2026-08-12T08:15:06Z"
  node_id: "worker-4070ti-animation-host-01"
  status: "RUNTIME_INSTALLED_AWAITING_ARTIFACTS"

docker:
  server_version: "29.6.2"
  compose_version: "5.3.1"
  containerd_version: "2.2.6"
  package_versions_match: true
  package_sha256_match: true
  packages_held: true
  tcp_2375_listening: false
  tcp_2376_listening: false

nvidia_container:
  toolkit_version: "1.19.1-1"
  package_sha256_match: true
  cdi_device_detected: "nvidia.com/gpu=all"
  gpu_uuid_in_container: "NOT_TESTED_IMAGE_NOT_TRANSFERRED"
  pytorch_version: "NOT_TESTED_IMAGE_NOT_TRANSFERRED"
  cuda_version: "NOT_TESTED_IMAGE_NOT_TRANSFERRED"

image:
  transferred: false
  archive_size_bytes: 0
  archive_sha256: "NOT_COLLECTED"
  image_id: "NOT_LOADED"
  repo_digest: "NOT_LOADED"

filesystem:
  operator_account: "gpucontrol"
  operator_uid: 1000
  runtime_uid_gid: "10001:10001"
  docker_data_root: "/var/lib/docker"
  docker_data_root_filesystem: "ext4"
  free_bytes_after_runtime: 1023794761728

models:
  transferred: false
  manifest_sha256: "NOT_COLLECTED"
  all_sizes_match: false
  all_sha256_match: false

unchanged_boundaries:
  assetclaw_unchanged: true
  qiuyue_comfyui_unchanged: true
  imageclip_workflow_unchanged: true
  windows_cluster_ports_opened: []
  node_agent_installed: false
  node_registered: false
  production_traffic_enabled: false

next_inputs_required:
  - "Transfer immutable ComfyUI archive from control-4090"
  - "Transfer approved ImageClip tree and four exact model files"
  - "After artifacts: verify SHA, docker load, execute C0"
  - "Later: DHCP reservation, four-node release commit and dedicated HMAC"
```

## 5. 当前正确停止点

本机基础运行时已经稳定并与 3090-B 锁定版本一致。当前不需要重新安装 WSL、Ubuntu、Docker 或 NVIDIA Toolkit。

SSH-only 传输入口已根据后续授权单独开通：Windows `10.3.34.238:2222` 仅允许 `10.3.34.11`，转发到 WSL SSH 22；8188/9201/9100/9400 仍未开放。详细证据见 `GPU_CONTROL_4070TI_SSH_TRANSFER_ACCESS_RECEIPT_2026-08-12.md`。

下一动作应是 4090 通过该入口向本机传输第 3 节制品。制品到达后，4070 侧继续 SHA 校验、镜像导入和 C0；在此之前不开放其他端口、不安装 Node Agent、不注册节点。
## 2026-08-12 WSL GPU 卡死补充

4070 Ti 在 12 GB 低显存工作流压力下触发 WSL `dxgkrnl` 的 `-122/-12` GPU 分配错误风暴。节点已恢复，`2222/8188/9201` 均实测可达，并已安装 ComfyUI 延迟启动和 DXG 错误熔断。完整证据、处置和主控调度约束见：

```text
docs/GPU_CONTROL_4070TI_WSL_DXG_FREEZE_INCIDENT_AND_RECOVERY_2026-08-12.md
```
