#!/usr/bin/env bash
set -euo pipefail

. /etc/os-release
echo "DISTRO=${PRETTY_NAME}"
echo "KERNEL=$(uname -r)"
echo "SYSTEMD=$(systemctl is-system-running || true)"
echo "PID1=$(ps -p 1 -o comm= | xargs)"
echo "NPROC=$(nproc)"
echo "MEM_TOTAL_BYTES=$(free -b | awk '/^Mem:/{print $2}')"
echo "SWAP_TOTAL_BYTES=$(free -b | awk '/^Swap:/{print $2}')"
echo "ROOT_FS_TYPE=$(findmnt -n -o FSTYPE /)"
echo "ROOT_FREE_BYTES=$(df --output=avail -B1 / | tail -1 | tr -d ' ')"
echo "GPU=$(/usr/lib/wsl/lib/nvidia-smi --query-gpu=name,uuid,driver_version,memory.total --format=csv,noheader)"
echo "CA_SHA256=$(sha256sum /usr/local/share/ca-certificates/gpu-control-lan-ca.crt | awk '{print $1}')"
echo "TLS=$(curl --fail --silent --show-error https://10.3.34.11/health/live)"

if getent passwd gpucontrol >/dev/null; then
  echo "GPUCONTROL_PASSWD=$(getent passwd gpucontrol)"
else
  echo "GPUCONTROL_PASSWD=ABSENT"
fi

if getent group docker >/dev/null; then
  echo "DOCKER_GROUP=$(getent group docker)"
else
  echo "DOCKER_GROUP=ABSENT"
fi

if command -v docker >/dev/null; then
  echo "DOCKER_CMD=$(command -v docker)"
else
  echo "DOCKER_CMD=NOT_INSTALLED"
fi

for path in \
  /opt/gpu-control \
  /opt/imageclip \
  /opt/imageclip/models \
  /srv/comfyui/runtime \
  /srv/gpu-control/images
do
  stat -c 'DIR=%a:%U:%G:%n' "${path}"
done
