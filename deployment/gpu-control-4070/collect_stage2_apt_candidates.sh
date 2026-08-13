#!/usr/bin/env bash
set -euo pipefail

specs=(
  'docker-ce|5:29.6.2-1~ubuntu.22.04~jammy'
  'docker-ce-cli|5:29.6.2-1~ubuntu.22.04~jammy'
  'containerd.io|2.2.6-1~ubuntu.22.04~jammy'
  'docker-buildx-plugin|0.35.0-1~ubuntu.22.04~jammy'
  'docker-compose-plugin|5.3.1-1~ubuntu.22.04~jammy'
  'nvidia-container-toolkit|1.19.1-1'
  'nvidia-container-toolkit-base|1.19.1-1'
  'libnvidia-container-tools|1.19.1-1'
  'libnvidia-container1|1.19.1-1'
)

for spec in "${specs[@]}"; do
  package="${spec%%|*}"
  locked="${spec#*|}"
  candidate="$(apt-cache policy "${package}" | awk '/Candidate:/{print $2}')"
  if apt-cache madison "${package}" | awk '{print $3}' | grep -Fxq "${locked}"; then
    locked_available=true
  else
    locked_available=false
  fi
  printf '%s|%s|%s|%s\n' \
    "${package}" "${candidate}" "${locked}" "${locked_available}"
done
