#!/usr/bin/env bash
set -euo pipefail

echo "COLLECTED_AT_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
echo "USER=$(id -un)"
echo "UID_GID=$(id -u):$(id -g)"
echo "HOSTNAME=$(hostname)"
echo "DOCKER_VERSION=$(docker version --format 'client={{.Client.Version}} server={{.Server.Version}}')"
echo "COMPOSE_VERSION=$(docker compose version --short)"
echo "CONTAINERD_VERSION=$(containerd --version)"
echo "DOCKER_ACTIVE=$(systemctl is-active docker)"
echo "CONTAINERD_ACTIVE=$(systemctl is-active containerd)"
echo "DOCKER_ROOT=$(docker info --format '{{.DockerRootDir}}')"
echo "DOCKER_ROOT_FS=$(findmnt -n -o FSTYPE -T "$(docker info --format '{{.DockerRootDir}}')")"
echo "ROOT_FREE_BYTES=$(df --output=avail -B1 / | tail -1 | tr -d ' ')"
echo "NVIDIA_CTK_VERSION=$(nvidia-ctk --version | head -1)"
echo "NVIDIA_CONTAINER_CLI_VERSION=$(nvidia-container-cli --version | head -1)"
echo "GPU=$(/usr/lib/wsl/lib/nvidia-smi --query-gpu=name,uuid,driver_version,memory.total --format=csv,noheader)"
echo "GPUCONTROL=$(getent passwd gpucontrol)"
echo "GPUCONTROL_ID=$(id gpucontrol)"
echo "HELD_PACKAGES=$(apt-mark showhold | sort | paste -sd ',' -)"
echo "TCP_CLUSTER_LISTENERS_BEGIN"
ss -lntp | grep -E ':(2375|2376|8188|9201|9100|2222|9400)\b' || true
echo "TCP_CLUSTER_LISTENERS_END"
echo "IMAGE_FILES_BEGIN"
find /srv/gpu-control/images -maxdepth 1 -type f -printf '%s %f\n' | sort
echo "IMAGE_FILES_END"
echo "MODEL_FILES_BEGIN"
find /opt/imageclip/models -type f -printf '%s %P\n' | sort
echo "MODEL_FILES_END"
echo "DIRECTORIES_BEGIN"
stat -c '%A %a %u:%g %U:%G %n' \
  /opt/gpu-control /opt/imageclip /opt/imageclip/models \
  /srv/gpu-control/images /srv/comfyui/runtime \
  /srv/comfyui/runtime/input /srv/comfyui/runtime/output \
  /srv/comfyui/runtime/temp /srv/comfyui/runtime/user \
  /srv/comfyui/runtime/user/default \
  /srv/comfyui/runtime/user/default/workflows
echo "DIRECTORIES_END"
echo "PACKAGE_VERSIONS_BEGIN"
dpkg-query -W \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin \
  nvidia-container-toolkit nvidia-container-toolkit-base \
  libnvidia-container-tools libnvidia-container1
echo "PACKAGE_VERSIONS_END"
echo "TLS=$(curl --fail --silent --show-error https://10.3.34.11/health/live)"
echo "NVIDIA_CDI_BEGIN"
nvidia-ctk cdi list || true
echo "NVIDIA_CDI_END"
