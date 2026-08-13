#!/usr/bin/env bash
set -Eeuo pipefail

export DEBIAN_FRONTEND=noninteractive

if [[ "$(id -u)" -ne 0 ]]; then
  echo "ERROR: run as root" >&2
  exit 1
fi

. /etc/os-release
if [[ "${VERSION_CODENAME}" != "jammy" ]]; then
  echo "ERROR: expected Ubuntu jammy, got ${VERSION_CODENAME}" >&2
  exit 1
fi

docker_packages=(
  docker-ce
  docker-ce-cli
  containerd.io
  docker-buildx-plugin
  docker-compose-plugin
)

docker_versions=(
  '5:29.6.2-1~ubuntu.22.04~jammy'
  '5:29.6.2-1~ubuntu.22.04~jammy'
  '2.2.6-1~ubuntu.22.04~jammy'
  '0.35.0-1~ubuntu.22.04~jammy'
  '5.3.1-1~ubuntu.22.04~jammy'
)

docker_sizes=(
  23312180
  16889272
  23621096
  17205924
  8099832
)

docker_hashes=(
  abda813589be3a9953c72181d2d1fa6064eb64966f917d70fe8996d9af485fc6
  5ad09e85f123841a0ced843f748e4ec52209f1773a770bdb39eb64f24eff6ba5
  a5fd776785cf8482d1a342479d5eed53cccd6daf534ef129012797b6e817dee6
  62b77b009803ebea4f9bc3cdecd00e3bf6c88266a3525046105c4449ceea94c7
  00784bd434f1fadde20cc047f5c88d97c9f2d17c82cef88ac69160421c553f2b
)

nvidia_packages=(
  nvidia-container-toolkit
  nvidia-container-toolkit-base
  libnvidia-container-tools
  libnvidia-container1
)

nvidia_versions=(
  '1.19.1-1'
  '1.19.1-1'
  '1.19.1-1'
  '1.19.1-1'
)

nvidia_sizes=(
  1334076
  5608524
  20816
  1191204
)

nvidia_hashes=(
  e66acb5b33420a8417429cd217abc8400b4a409a2ae17a3852cf6feb34b5c8e6
  b6c5b4e77a28cde0197cc0e64edf75538604775d9f8aea502cef667e7e5b2132
  5642763d51961a2295dff09990048a5dcee81edbea2a8c5084e47b09ccf17268
  d73bb582af893135198ef81cb22135c790a75d2ad72910446477c6c4430f3e6b
)

verify_candidate() {
  local package="$1"
  local expected="$2"
  local candidate
  candidate="$(apt-cache policy "${package}" | awk '/Candidate:/{print $2}')"
  echo "candidate ${package}=${candidate}"
  if [[ "${candidate}" != "${expected}" ]]; then
    if apt-cache madison "${package}" | awk '{print $3}' | grep -Fxq "${expected}"; then
      echo "NOTICE: repository candidate is newer; installing available locked version ${expected}"
    else
      echo "ERROR: locked version unavailable for ${package}: ${expected}" >&2
      exit 10
    fi
  fi
}

verify_deb() {
  local directory="$1"
  local package="$2"
  local expected_size="$3"
  local expected_hash="$4"
  local matches=()
  local file actual_size actual_hash

  mapfile -t matches < <(find "${directory}" -maxdepth 1 -type f -name "${package}_*.deb" -print)
  if [[ "${#matches[@]}" -ne 1 ]]; then
    echo "ERROR: expected exactly one ${package} deb, found ${#matches[@]}" >&2
    exit 11
  fi

  file="${matches[0]}"
  actual_size="$(stat -c '%s' "${file}")"
  actual_hash="$(sha256sum "${file}" | awk '{print $1}')"
  echo "verified ${package} size=${actual_size} sha256=${actual_hash}"

  if [[ "${actual_size}" != "${expected_size}" ]]; then
    echo "ERROR: size mismatch for ${package}: ${actual_size} != ${expected_size}" >&2
    exit 12
  fi
  if [[ "${actual_hash}" != "${expected_hash}" ]]; then
    echo "ERROR: SHA-256 mismatch for ${package}: ${actual_hash} != ${expected_hash}" >&2
    exit 13
  fi
}

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg jq openssl rsync openssh-client

install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg \
  -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
printf '%s\n' \
  'deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu jammy stable' \
  > /etc/apt/sources.list.d/docker.list

curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
  | gpg --batch --yes --dearmor \
      -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
  | sed 's#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#' \
  > /etc/apt/sources.list.d/nvidia-container-toolkit.list

apt-get update

for index in "${!docker_packages[@]}"; do
  verify_candidate "${docker_packages[$index]}" "${docker_versions[$index]}"
done
for index in "${!nvidia_packages[@]}"; do
  verify_candidate "${nvidia_packages[$index]}" "${nvidia_versions[$index]}"
done

package_root="$(mktemp -d /var/tmp/gpu-control-stage2-packages.XXXXXX)"
trap 'rm -rf -- "${package_root}"' EXIT
docker_dir="${package_root}/docker"
nvidia_dir="${package_root}/nvidia"
install -d -m 0700 "${docker_dir}" "${nvidia_dir}"

pushd "${docker_dir}" >/dev/null
apt-get download \
  "docker-ce=${docker_versions[0]}" \
  "docker-ce-cli=${docker_versions[1]}" \
  "containerd.io=${docker_versions[2]}" \
  "docker-buildx-plugin=${docker_versions[3]}" \
  "docker-compose-plugin=${docker_versions[4]}"
popd >/dev/null

for index in "${!docker_packages[@]}"; do
  verify_deb "${docker_dir}" "${docker_packages[$index]}" \
    "${docker_sizes[$index]}" "${docker_hashes[$index]}"
done

pushd "${nvidia_dir}" >/dev/null
apt-get download \
  "nvidia-container-toolkit=${nvidia_versions[0]}" \
  "nvidia-container-toolkit-base=${nvidia_versions[1]}" \
  "libnvidia-container-tools=${nvidia_versions[2]}" \
  "libnvidia-container1=${nvidia_versions[3]}"
popd >/dev/null

for index in "${!nvidia_packages[@]}"; do
  verify_deb "${nvidia_dir}" "${nvidia_packages[$index]}" \
    "${nvidia_sizes[$index]}" "${nvidia_hashes[$index]}"
done

apt-get install -y --no-install-recommends "${docker_dir}"/*.deb
systemctl enable --now containerd docker

apt-get install -y --no-install-recommends "${nvidia_dir}"/*.deb
nvidia-ctk runtime configure --runtime=docker
systemctl restart docker

apt-mark hold \
  "${docker_packages[@]}" \
  "${nvidia_packages[@]}"

if getent passwd gpucontrol >/dev/null; then
  echo "gpucontrol account already exists"
else
  adduser --disabled-password --gecos '' gpucontrol
fi
usermod -aG docker gpucontrol

gpu_group="$(id -gn gpucontrol)"
install -d -m 0755 -o gpucontrol -g "${gpu_group}" \
  /opt/gpu-control /opt/imageclip /opt/imageclip/models \
  /srv/gpu-control/images
install -d -m 0775 -o 10001 -g 10001 \
  /srv/comfyui/runtime \
  /srv/comfyui/runtime/input \
  /srv/comfyui/runtime/output \
  /srv/comfyui/runtime/temp \
  /srv/comfyui/runtime/user \
  /srv/comfyui/runtime/user/default \
  /srv/comfyui/runtime/user/default/workflows

echo '=== VERSION RECEIPT ==='
docker version --format 'client={{.Client.Version}} server={{.Server.Version}}'
docker compose version
containerd --version
dpkg-query -W \
  docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin \
  nvidia-container-toolkit nvidia-container-toolkit-base \
  libnvidia-container-tools libnvidia-container1
systemctl is-active docker containerd
id gpucontrol
getent passwd gpucontrol

if ss -lntp | grep -Eq ':(2375|2376)\b'; then
  echo 'ERROR: Docker TCP 2375/2376 is listening' >&2
  exit 20
fi

echo 'docker_tcp_2375_2376=closed'
docker info --format '{{json .SecurityOptions}}'
stat -c '%A %a %u:%g %U:%G %n' \
  /opt/gpu-control /opt/imageclip /opt/imageclip/models \
  /srv/gpu-control/images /srv/comfyui/runtime \
  /srv/comfyui/runtime/input /srv/comfyui/runtime/output \
  /srv/comfyui/runtime/temp /srv/comfyui/runtime/user
