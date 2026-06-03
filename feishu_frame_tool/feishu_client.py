"""飞书多维表格 (Bitable) 客户端。

负责：获取 tenant_access_token、列出记录、下载附件、更新「进度」字段。
"""

import os
import re
import json
import time
import urllib.parse
from typing import Callable, Dict, List, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# 请求超时：(连接 30s, 读取 None=不限时)。
# 读取不设上限——表格记录/视频可能很多，单次请求/下载耗时较长也不会被中断；
# 仅保留连接超时，确保网络真正不可用时能及时失败而不是无限等待。
DEFAULT_TIMEOUT = (30, None)


def _make_session() -> requests.Session:
    """带自动重试的 Session：对超时、连接错误、429/5xx 自动重试若干次。"""
    s = requests.Session()
    retry = Retry(
        total=4, connect=4, read=4, backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=frozenset(["GET", "POST", "PUT"]),
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


class FeishuError(RuntimeError):
    pass


def parse_table_url(url: str) -> Dict[str, str]:
    """从飞书多维表格分享链接解析出 token / table_id / view_id。

    支持形如：
      https://xxx.feishu.cn/base/<app_token>?table=<table_id>&view=<view_id>
      https://xxx.feishu.cn/wiki/<wiki_token>?table=<table_id>&view=<view_id>

    返回 {"kind": base|wiki|"", "token": ..., "table_id": ..., "view_id": ...}
    （wiki 链接的 token 需再调用接口解析为真正的 app_token）
    """
    out = {"kind": "", "token": "", "table_id": "", "view_id": ""}
    if not url:
        return out
    m = re.search(r"/(base|wiki|sheets)/([A-Za-z0-9]+)", url)
    if m:
        out["kind"] = m.group(1)
        out["token"] = m.group(2)
    try:
        params = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        if params.get("table"):
            out["table_id"] = params["table"][0]
        if params.get("view"):
            out["view_id"] = params["view"][0]
    except Exception:
        pass
    return out


class FeishuClient:
    def __init__(self, app_id: str, app_secret: str, app_token: str,
                 table_id: str, view_id: str = "",
                 base_domain: str = "https://open.feishu.cn",
                 logger: Optional[Callable[[str], None]] = None):
        if not app_id or not app_secret:
            raise FeishuError("缺少 app_id / app_secret，请在配置里填写。")
        self.app_id = app_id
        self.app_secret = app_secret
        self.app_token = app_token
        self.table_id = table_id
        self.view_id = view_id
        self.base = base_domain.rstrip("/")
        self.log = logger or (lambda m: None)
        self._token = None
        self._token_expire = 0
        self._field_id_cache: Dict[str, str] = {}
        self.session = _make_session()

    # ── 鉴权 ──────────────────────────────────────────────────────────────
    def _tenant_token(self) -> str:
        if self._token and time.time() < self._token_expire - 60:
            return self._token
        url = f"{self.base}/open-apis/auth/v3/tenant_access_token/internal"
        resp = self.session.post(url, json={
            "app_id": self.app_id,
            "app_secret": self.app_secret,
        }, timeout=DEFAULT_TIMEOUT)
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(f"获取 tenant_access_token 失败: {data}")
        self._token = data["tenant_access_token"]
        self._token_expire = time.time() + data.get("expire", 7200)
        return self._token

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._tenant_token()}"}

    # ── 链接解析 / 构造 ───────────────────────────────────────────────────
    def resolve_wiki_app_token(self, wiki_token: str) -> str:
        """把知识库 (wiki) 节点 token 解析为真正的多维表格 app_token。"""
        url = f"{self.base}/open-apis/wiki/v2/spaces/get_node"
        resp = self.session.get(url, headers=self._headers(),
                               params={"token": wiki_token}, timeout=DEFAULT_TIMEOUT)
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(
                "解析知识库链接失败（可能应用缺少 wiki:wiki 权限，"
                f"或链接不是多维表格）: {data}")
        return data["data"]["node"]["obj_token"]

    @classmethod
    def from_feishu_config(cls, fe: dict,
                           logger: Optional[Callable[[str], None]] = None
                           ) -> "FeishuClient":
        """根据 feishu 配置构造客户端。

        若提供了 `table_url`（表格浏览器链接），优先用它解析出
        app_token / table_id / view_id；否则用配置里已存的值。
        """
        app_token = fe.get("app_token", "")
        table_id = fe.get("table_id", "")
        view_id = fe.get("view_id", "")
        wiki_token = ""

        url = (fe.get("table_url") or "").strip()
        if url:
            p = parse_table_url(url)
            if p["table_id"]:
                table_id = p["table_id"]
            if p["view_id"]:
                view_id = p["view_id"]
            if p["token"]:
                if p["kind"] == "wiki":
                    wiki_token = p["token"]
                else:
                    app_token = p["token"]

        client = cls(app_id=fe.get("app_id", ""),
                     app_secret=fe.get("app_secret", ""),
                     app_token=app_token, table_id=table_id, view_id=view_id,
                     base_domain=fe.get("base_domain", "https://open.feishu.cn"),
                     logger=logger)
        if wiki_token:
            client.app_token = client.resolve_wiki_app_token(wiki_token)
        return client

    # ── 字段 ──────────────────────────────────────────────────────────────
    def get_field_id(self, field_name: str) -> Optional[str]:
        if not self._field_id_cache:
            url = (f"{self.base}/open-apis/bitable/v1/apps/{self.app_token}"
                   f"/tables/{self.table_id}/fields?page_size=200")
            resp = self.session.get(url, headers=self._headers(), timeout=DEFAULT_TIMEOUT)
            data = resp.json()
            if data.get("code") != 0:
                raise FeishuError(f"获取字段列表失败: {data}")
            for f in data["data"].get("items", []):
                self._field_id_cache[f["field_name"]] = f["field_id"]
        return self._field_id_cache.get(field_name)

    # ── 记录 ──────────────────────────────────────────────────────────────
    def list_records(self) -> List[dict]:
        """列出全部记录（自动翻页）。"""
        records: List[dict] = []
        page_token = ""
        url = (f"{self.base}/open-apis/bitable/v1/apps/{self.app_token}"
               f"/tables/{self.table_id}/records")
        while True:
            params = {"page_size": 200}
            if self.view_id:
                params["view_id"] = self.view_id
            if page_token:
                params["page_token"] = page_token
            resp = self.session.get(url, headers=self._headers(), params=params,
                                    timeout=DEFAULT_TIMEOUT)
            data = resp.json()
            if data.get("code") != 0:
                raise FeishuError(f"列出记录失败: {data}")
            records.extend(data["data"].get("items", []))
            if data["data"].get("has_more"):
                page_token = data["data"]["page_token"]
            else:
                break
        return records

    def update_progress(self, record_id: str, progress_field: str, value: str) -> None:
        url = (f"{self.base}/open-apis/bitable/v1/apps/{self.app_token}"
               f"/tables/{self.table_id}/records/{record_id}")
        resp = self.session.put(url, headers=self._headers(),
                               json={"fields": {progress_field: value}},
                               timeout=DEFAULT_TIMEOUT)
        data = resp.json()
        if data.get("code") != 0:
            raise FeishuError(f"更新进度失败 (record={record_id}): {data}")

    # ── 附件下载 ──────────────────────────────────────────────────────────
    def download_attachment(self, attachment: dict, dest_dir: str,
                            field_name: str = "", record_id: str = "",
                            save_name: str = "") -> str:
        """下载单个附件对象到 dest_dir，返回本地文件路径。

        优先使用接口返回的 url/tmp_url（已内置 extra 鉴权参数）；
        若没有则手动构建 extra（兼容开启高级权限的表格）。
        """
        os.makedirs(dest_dir, exist_ok=True)
        name = save_name or attachment.get("name") or attachment.get("file_token", "video")
        dest = os.path.join(dest_dir, name)

        download_url = attachment.get("url")
        if not download_url:
            file_token = attachment["file_token"]
            extra = self._build_extra(field_name, record_id, file_token)
            download_url = (f"{self.base}/open-apis/drive/v1/medias/"
                            f"{file_token}/download")
            if extra:
                download_url += "?extra=" + urllib.parse.quote(extra)

        self.log(f"下载附件: {name}")
        with self.session.get(download_url, headers=self._headers(),
                             stream=True, timeout=DEFAULT_TIMEOUT) as r:
            ctype = r.headers.get("Content-Type", "")
            if r.status_code != 200 or "application/json" in ctype:
                # 错误时正文是 JSON
                try:
                    raise FeishuError(f"下载附件失败: {r.json()}")
                except ValueError:
                    raise FeishuError(f"下载附件失败 HTTP {r.status_code}")
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 16):
                    if chunk:
                        f.write(chunk)
        self.log(f"已保存: {dest}")
        return dest

    def _build_extra(self, field_name: str, record_id: str, file_token: str) -> Optional[str]:
        if not (field_name and record_id):
            return None
        field_id = self.get_field_id(field_name)
        if not field_id:
            return None
        extra = {
            "bitablePerm": {
                "tableId": self.table_id,
                "attachments": {field_id: {record_id: [file_token]}},
            }
        }
        return json.dumps(extra, separators=(",", ":"))
