#!/usr/bin/env bash
set -u

output="$(ssh -vv \
  -o BatchMode=yes \
  -o PreferredAuthentications=none \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  gpucontrol@127.0.0.1 true 2>&1)"
exit_code=$?

printf '%s\n' "${output}" \
  | grep -E 'Authentications that can continue|Permission denied' \
  | tail -3 || true
echo "exit_code=${exit_code}"
