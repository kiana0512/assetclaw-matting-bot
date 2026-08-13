#!/usr/bin/env bash
set -u

pkill -TERM -x apt-get 2>/dev/null || true
sleep 2

ps -eo pid,cmd | grep -E '[a]pt-get|[d]pkg' || true

for url in \
  https://mirrors.aliyun.com/ubuntu/dists/jammy/InRelease \
  https://mirrors.tuna.tsinghua.edu.cn/ubuntu/dists/jammy/InRelease \
  https://repo.huaweicloud.com/ubuntu/dists/jammy/InRelease
do
  echo "URL=${url}"
  curl -L --fail --connect-timeout 3 --max-time 8 -o /dev/null -sS \
    -w 'code=%{http_code} bytes=%{size_download} speed=%{speed_download}\n' \
    "${url}" || true
done
