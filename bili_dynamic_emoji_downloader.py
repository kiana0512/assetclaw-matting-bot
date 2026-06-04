#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
批量下载 B 站指定 UP 主动态里的表情包/图片：
- 静态图统一保存为 PNG
- 动图统一保存为 GIF
- 支持登录态 Cookie，但不要把 Cookie 发给别人
"""

import argparse
import hashlib
import io
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

import requests
from PIL import Image, ImageSequence, UnidentifiedImageError
from tqdm import tqdm


API_URL = "https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space"

FEATURES = (
    "itemOpusStyle,listOnlyfans,opusBigCover,onlyfansVote,"
    "forwardListHidden,decorationCard,commentsNewVersion,"
    "onlyfansAssetsV2,ugcDelete,onlyfansQaCard"
)


def read_cookie(cookie_file: Optional[str]) -> str:
    """
    Cookie 优先级：
    1. --cookie-file 指定文件
    2. 环境变量 BILI_COOKIE
    3. 空字符串，即不登录
    """
    if cookie_file:
        p = Path(cookie_file)
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
        raise FileNotFoundError(f"找不到 cookie 文件：{cookie_file}")

    return os.environ.get("BILI_COOKIE", "").strip()


def build_session(cookie: str) -> requests.Session:
    s = requests.Session()
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Referer": "https://space.bilibili.com/",
        "Origin": "https://space.bilibili.com",
        "Accept": "application/json, text/plain, */*",
    }
    if cookie:
        headers["Cookie"] = cookie
    s.headers.update(headers)
    return s


def clean_bili_image_url(url: str) -> Optional[str]:
    """
    B 站图片经常长这样：
    https://i0.hdslb.com/bfs/new_dyn/xxx.png@672w_672h_1c.webp

    这里会：
    - 补全 //i0.hdslb.com
    - 去掉 query
    - 去掉 @ 后面的图片处理参数
    - http 转 https
    """
    if not url or not isinstance(url, str):
        return None

    url = url.strip()

    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("http://"):
        url = "https://" + url[len("http://"):]

    if not url.startswith("https://"):
        return None

    # 去 query
    parts = urlsplit(url)
    url_no_query = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))

    # 去 @ 后缀，例如 .jpg@960w_540h.webp
    if "@" in url_no_query:
        url_no_query = url_no_query.split("@", 1)[0]

    if not is_bili_image_url(url_no_query):
        return None

    return url_no_query


def is_bili_image_url(url: str) -> bool:
    """
    只接受 B 站常见图片域名，避免误抓其他链接。
    """
    if not isinstance(url, str):
        return False

    return (
        "hdslb.com/bfs/" in url
        or "biliimg.com/bfs/" in url
        or "album.biliimg.com/bfs/" in url
    )


def unique_keep_order(items: Iterable[str]) -> List[str]:
    seen = set()
    out = []
    for x in items:
        if x and x not in seen:
            seen.add(x)
            out.append(x)
    return out


def recursive_find_image_urls(obj: Any) -> List[str]:
    """
    在 major.opus / major.draw 结构内部兜底寻找图片 URL。
    注意：只会在 major 内部调用，避免把头像、装扮卡、挂件图也抓下来。
    """
    found = []

    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in {"url", "src", "img_src", "image_url"} and isinstance(v, str):
                cleaned = clean_bili_image_url(v)
                if cleaned:
                    found.append(cleaned)
            else:
                found.extend(recursive_find_image_urls(v))

    elif isinstance(obj, list):
        for x in obj:
            found.extend(recursive_find_image_urls(x))

    return found


def extract_image_urls_from_card(card: Dict[str, Any]) -> List[str]:
    """
    只提取动态正文图文里的图片：
    - modules.module_dynamic.major.opus.pics[].url
    - modules.module_dynamic.major.draw.items[].src
    - 兜底：在 major.opus / major.draw 内递归找 url/src
    """
    modules = card.get("modules") or {}
    module_dynamic = modules.get("module_dynamic") or {}
    major = module_dynamic.get("major") or {}

    urls: List[str] = []

    opus = major.get("opus")
    if isinstance(opus, dict):
        for pic in opus.get("pics") or []:
            if isinstance(pic, dict):
                u = clean_bili_image_url(pic.get("url") or pic.get("src") or "")
                if u:
                    urls.append(u)
        urls.extend(recursive_find_image_urls(opus))

    draw = major.get("draw")
    if isinstance(draw, dict):
        for pic in draw.get("items") or []:
            if isinstance(pic, dict):
                u = clean_bili_image_url(pic.get("src") or pic.get("url") or "")
                if u:
                    urls.append(u)
        urls.extend(recursive_find_image_urls(draw))

    return unique_keep_order(urls)


def fetch_dynamic_page(
    session: requests.Session,
    host_mid: str,
    offset: str = "",
    timeout: int = 20,
) -> Tuple[List[Dict[str, Any]], str, bool]:
    params = {
        "host_mid": host_mid,
        "offset": offset,
        "timezone_offset": -480,
        "platform": "web",
        "features": FEATURES,
    }

    r = session.get(API_URL, params=params, timeout=timeout)

    if r.status_code in {401, 403, 412}:
        raise RuntimeError(
            f"请求被 B 站拒绝，HTTP {r.status_code}。"
            "建议登录浏览器后复制 Cookie 到 BILI_COOKIE 或 cookie.txt。"
        )

    r.raise_for_status()
    data = r.json()

    if data.get("code") != 0:
        raise RuntimeError(
            f"B 站接口返回错误：code={data.get('code')}, "
            f"message={data.get('message')}"
        )

    body = data.get("data") or {}
    items = body.get("items") or []
    next_offset = str(body.get("offset") or "")
    has_more = bool(body.get("has_more"))

    return items, next_offset, has_more


def download_bytes(
    session: requests.Session,
    url: str,
    retries: int = 3,
    sleep_sec: float = 1.0,
) -> bytes:
    last_err = None

    for i in range(retries):
        try:
            r = session.get(
                url,
                timeout=30,
                headers={
                    "Referer": "https://space.bilibili.com/",
                    "Accept": "image/avif,image/webp,image/apng,image/svg+xml,image/*,*/*;q=0.8",
                },
            )

            if r.status_code in {401, 403, 412}:
                raise RuntimeError(f"图片请求被拒绝，HTTP {r.status_code}")

            r.raise_for_status()
            return r.content

        except Exception as e:
            last_err = e
            if i < retries - 1:
                time.sleep(sleep_sec * (i + 1))

    raise RuntimeError(f"下载失败：{url}，原因：{last_err}")


def safe_filename_part(s: str, max_len: int = 80) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s[:max_len] or "item"


def card_date_and_id(card: Dict[str, Any]) -> Tuple[str, str]:
    card_id = str(card.get("id_str") or "")

    modules = card.get("modules") or {}
    author = modules.get("module_author") or {}
    pub_ts = author.get("pub_ts")

    if pub_ts:
        try:
            date_str = datetime.fromtimestamp(int(pub_ts)).strftime("%Y%m%d")
        except Exception:
            date_str = "unknown_date"
    else:
        date_str = "unknown_date"

    if not card_id:
        basic = card.get("basic") or {}
        card_id = str(basic.get("rid_str") or "unknown_dynamic")

    return date_str, card_id


def convert_and_save_image(
    raw: bytes,
    out_dir: Path,
    base_name: str,
) -> Tuple[Path, bool, str]:
    """
    返回：
    - 保存路径
    - 是否为动图
    - 内容 sha1
    """
    sha1 = hashlib.sha1(raw).hexdigest()

    try:
        im = Image.open(io.BytesIO(raw))
    except UnidentifiedImageError:
        bad_path = out_dir / f"{base_name}_{sha1[:10]}.bin"
        bad_path.write_bytes(raw)
        return bad_path, False, sha1

    is_animated = bool(getattr(im, "is_animated", False)) and getattr(im, "n_frames", 1) > 1

    if is_animated:
        out_path = out_dir / f"{base_name}_{sha1[:10]}.gif"

        frames = []
        durations = []

        for frame in ImageSequence.Iterator(im):
            frames.append(frame.convert("RGBA"))
            durations.append(frame.info.get("duration", im.info.get("duration", 100)))

        if not frames:
            # 理论上不会发生，兜底保存为 PNG
            out_path = out_dir / f"{base_name}_{sha1[:10]}.png"
            im.convert("RGBA").save(out_path, "PNG")
            return out_path, False, sha1

        frames[0].save(
            out_path,
            format="GIF",
            save_all=True,
            append_images=frames[1:],
            duration=durations,
            loop=im.info.get("loop", 0),
            disposal=2,
        )

        return out_path, True, sha1

    else:
        out_path = out_dir / f"{base_name}_{sha1[:10]}.png"
        im.convert("RGBA").save(out_path, "PNG")
        return out_path, False, sha1


def load_existing_hashes(out_dir: Path) -> set:
    """
    读取 manifest.jsonl，避免重复下载。
    """
    manifest = out_dir / "manifest.jsonl"
    seen = set()

    if not manifest.exists():
        return seen

    with manifest.open("r", encoding="utf-8") as f:
        for line in f:
            try:
                row = json.loads(line)
                if row.get("sha1"):
                    seen.add(row["sha1"])
            except Exception:
                continue

    return seen


def append_manifest(out_dir: Path, row: Dict[str, Any]) -> None:
    manifest = out_dir / "manifest.jsonl"
    with manifest.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def crawl(args: argparse.Namespace) -> None:
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    cookie = read_cookie(args.cookie_file)
    session = build_session(cookie)

    seen_hashes = load_existing_hashes(out_dir) if args.dedupe else set()

    offset = ""
    page = 0
    total_cards = 0
    total_imgs = 0
    saved_imgs = 0
    skipped_dup = 0

    while True:
        page += 1

        print(f"\n[Page {page}] offset={offset or '<first>'}")
        items, next_offset, has_more = fetch_dynamic_page(session, args.mid, offset)

        total_cards += len(items)

        page_image_jobs = []
        for card in items:
            date_str, card_id = card_date_and_id(card)
            urls = extract_image_urls_from_card(card)

            for idx, url in enumerate(urls, start=1):
                base_name = safe_filename_part(f"{date_str}_{card_id}_{idx:02d}")
                page_image_jobs.append((card_id, date_str, idx, url, base_name))

        total_imgs += len(page_image_jobs)

        print(f"本页动态数：{len(items)}，图片数：{len(page_image_jobs)}")

        for card_id, date_str, idx, url, base_name in tqdm(page_image_jobs, desc="下载图片"):
            try:
                raw = download_bytes(session, url, sleep_sec=args.sleep)
                sha1 = hashlib.sha1(raw).hexdigest()

                if args.dedupe and sha1 in seen_hashes:
                    skipped_dup += 1
                    continue

                saved_path, is_animated, sha1 = convert_and_save_image(raw, out_dir, base_name)
                seen_hashes.add(sha1)
                saved_imgs += 1

                append_manifest(out_dir, {
                    "file": str(saved_path.name),
                    "dynamic_id": card_id,
                    "date": date_str,
                    "index_in_dynamic": idx,
                    "source_url": url,
                    "is_animated": is_animated,
                    "sha1": sha1,
                })

                time.sleep(args.sleep)

            except Exception as e:
                print(f"\n[WARN] 下载失败：{url}\n原因：{e}")

        if args.limit_pages and page >= args.limit_pages:
            print("\n达到 --limit-pages 限制，停止。")
            break

        if not has_more:
            print("\n没有更多动态，结束。")
            break

        if not next_offset or next_offset == offset:
            print("\n没有拿到有效 next offset，停止，避免死循环。")
            break

        offset = next_offset
        time.sleep(args.page_sleep)

    print("\n完成。")
    print(f"动态页数：{page}")
    print(f"动态条数：{total_cards}")
    print(f"发现图片：{total_imgs}")
    print(f"保存图片：{saved_imgs}")
    print(f"跳过重复：{skipped_dup}")
    print(f"输出目录：{out_dir.resolve()}")


def main():
    parser = argparse.ArgumentParser(
        description="批量下载 B 站 UP 主动态里的图片/表情包，静态转 PNG，动图转 GIF。"
    )

    parser.add_argument(
        "--mid",
        default="3546769371695776",
        help="UP 主 UID / mid，默认是你给的这个主页。",
    )
    parser.add_argument(
        "--out",
        default="bili_stickers",
        help="输出目录。",
    )
    parser.add_argument(
        "--cookie-file",
        default=None,
        help="Cookie 文件路径。也可以不用这个，改用环境变量 BILI_COOKIE。",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.8,
        help="每张图下载后的间隔秒数，建议不要太小。",
    )
    parser.add_argument(
        "--page-sleep",
        type=float,
        default=1.5,
        help="每页动态之间的间隔秒数。",
    )
    parser.add_argument(
        "--limit-pages",
        type=int,
        default=0,
        help="限制抓取页数。0 表示不限制。调试时建议先设 1 或 2。",
    )
    parser.add_argument(
        "--no-dedupe",
        action="store_false",
        dest="dedupe",
        help="关闭按内容 SHA1 去重。",
    )

    args = parser.parse_args()
    crawl(args)


if __name__ == "__main__":
    main()