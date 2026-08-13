$ErrorActionPreference = 'Stop'
$outputPath = 'C:\assetclaw-matting-bot\tmp\wsl-gpu-guard-install.txt'
$lines = & C:\Windows\System32\wsl.exe -d Ubuntu -u root -- sh -lc @'
set -eu
install -o root -g root -m 0755 /mnt/c/assetclaw-matting-bot/deployment/gpu-control-4070/gpu-control-dxg-guard.py /usr/local/sbin/gpu-control-dxg-guard.py
install -o root -g root -m 0644 /mnt/c/assetclaw-matting-bot/deployment/gpu-control-4070/gpu-control-dxg-guard.service /etc/systemd/system/gpu-control-dxg-guard.service
install -o root -g root -m 0644 /mnt/c/assetclaw-matting-bot/deployment/gpu-control-4070/gpu-control-comfyui-delayed-start.service /etc/systemd/system/gpu-control-comfyui-delayed-start.service
python3 - <<'PY'
from pathlib import Path
p = Path('/opt/gpu-control/deploy/gpu-node/compose.wsl.yaml')
s = p.read_text()
needle = '  comfyui:\n'
if '  comfyui:\n    restart: "no"\n' not in s:
    if needle not in s:
        raise SystemExit('comfyui service not found in WSL overlay')
    s = s.replace(needle, '  comfyui:\n    # Prevent immediate CUDA startup during WSL boot; systemd starts it after dxg settles.\n    restart: "no"\n', 1)
    p.write_text(s)
PY
docker update --restart=no gpu-control-node-comfyui-1 >/dev/null
systemctl daemon-reload
systemctl enable --now gpu-control-comfyui-delayed-start.service gpu-control-dxg-guard.service >/dev/null
echo '---SERVICES---'
systemctl is-active gpu-control-comfyui-delayed-start.service gpu-control-dxg-guard.service
systemctl is-enabled gpu-control-comfyui-delayed-start.service gpu-control-dxg-guard.service
echo '---RESTART-POLICY---'
docker inspect -f '{{.Name}} restart={{.HostConfig.RestartPolicy.Name}} running={{.State.Running}} health={{if .State.Health}}{{.State.Health.Status}}{{else}}n/a{{end}}' gpu-control-node-comfyui-1
echo '---PORTS---'
ss -lnt | grep -E ':(22|8188|9201) '
echo '---GUARD---'
journalctl -u gpu-control-dxg-guard.service -n 10 --no-pager
'@
[System.IO.File]::WriteAllLines($outputPath, [string[]]$lines, [System.Text.UTF8Encoding]::new($false))
