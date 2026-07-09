"""飞书抽帧自动化工具 — 图形界面。

运行：python gui.py
启动前可在界面修改帧率、下载/导出路径、飞书凭证等设置，点击「开始运行」执行。
"""

import os
import json
import argparse
import queue
import threading
import traceback
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from workflow import Workflow, load_config

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
EXAMPLE_PATH = os.path.join(HERE, "config.example.json")


def _initial_config() -> dict:
    if os.path.exists(CONFIG_PATH):
        return load_config(CONFIG_PATH)
    return load_config(EXAMPLE_PATH)


def _read_external_settings() -> dict:
    parser = argparse.ArgumentParser(description="飞书 framepacker 抽帧自动化工具")
    parser.add_argument("--fps", type=int, help="覆盖界面里的帧率")
    parser.add_argument("--diff-threshold", type=float, help="覆盖相似阈值")
    args = parser.parse_args()

    settings = {
        "fps": args.fps,
        "diff_threshold": args.diff_threshold,
    }

    if settings["fps"] is None:
        env_fps = os.getenv("FEISHU_FRAME_FPS")
        if env_fps:
            settings["fps"] = int(float(env_fps))

    if settings["diff_threshold"] is None:
        env_threshold = os.getenv("FEISHU_FRAME_DIFF_THRESHOLD")
        if env_threshold:
            settings["diff_threshold"] = float(env_threshold)

    return settings


def _apply_external_settings(cfg: dict, settings: dict) -> dict:
    if settings.get("fps") is not None:
        cfg.setdefault("framepacker", {})["fps"] = settings["fps"]
    if settings.get("diff_threshold") is not None:
        cfg.setdefault("dedup", {})["diff_threshold"] = settings["diff_threshold"]
    return cfg


def _reconstruct_table_url(fe: dict) -> str:
    """根据已存的 app_token / table_id / view_id 还原出表格链接，用于界面默认显示。"""
    token = fe.get("app_token", "")
    if not token:
        return ""
    url = f"https://feishu.cn/base/{token}"
    params = []
    if fe.get("table_id"):
        params.append(f"table={fe['table_id']}")
    if fe.get("view_id"):
        params.append(f"view={fe['view_id']}")
    if params:
        url += "?" + "&".join(params)
    return url


class App:
    def __init__(self, root: tk.Tk, external_settings: dict | None = None):
        self.root = root
        root.title("飞书 → 本地抽帧自动化工具")
        root.geometry("860x720")

        self.cfg = _initial_config()
        if external_settings:
            self.cfg = _apply_external_settings(self.cfg, external_settings)
        # 「表格链接」默认显示当前正在读取的表格地址（配置里若没存链接则按 ID 还原）
        fe_cfg = self.cfg.setdefault("feishu", {})
        if not fe_cfg.get("table_url"):
            fe_cfg["table_url"] = _reconstruct_table_url(fe_cfg)
        self.vars = {}
        self.log_queue: "queue.Queue[str]" = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None

        self._build_ui()
        self._poll_log()

    # ── UI 构建 ───────────────────────────────────────────────────────────
    def _build_ui(self):
        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=False, padx=10, pady=(10, 6))

        # —— 基础设置页 ——
        base = ttk.Frame(nb, padding=10)
        nb.add(base, text="基础设置")
        self._row(base, 0, "帧率 (fps)", ("framepacker", "fps"))
        self._path_row(base, 1, "视频下载目录", ("paths", "download_dir"))
        self._path_row(base, 2, "序列帧导出目录", ("paths", "export_dir"))

        self.dedup_var = tk.BooleanVar(
            value=bool(self.cfg.get("dedup", {}).get("enabled", False)))
        ttk.Checkbutton(base, text="去除相似帧 (导出后本地处理)",
                        variable=self.dedup_var).grid(
            row=3, column=1, sticky="w", pady=4)
        self._row(base, 4, "相似阈值%(越大删越多)", ("dedup", "diff_threshold"),
                  default="0.2")
        ttk.Label(
            base,
            text="参考值：≈0.1 完全相同 ｜ ≈0.2 轻微差异(推荐) ｜ ≈0.3 略微差异",
            foreground="gray").grid(row=5, column=1, sticky="w", pady=(0, 4))
        self.renumber_var = tk.BooleanVar(
            value=bool(self.cfg.get("dedup", {}).get("renumber", False)))
        ttk.Checkbutton(base, text="去重后重新连续编号",
                        variable=self.renumber_var).grid(
            row=6, column=1, sticky="w", pady=4)

        # —— 飞书设置页 ——
        fe = ttk.Frame(nb, padding=10)
        nb.add(fe, text="飞书凭证")
        self._row(fe, 0, "App ID", ("feishu", "app_id"))
        self._row(fe, 1, "App Secret", ("feishu", "app_secret"), show="*")
        self._row(fe, 2, "表格链接", ("feishu", "table_url"))
        ttk.Label(
            fe,
            text="粘贴多维表格的浏览器地址即可（留空则沿用上次的表格）。",
            foreground="gray").grid(row=3, column=1, sticky="w", pady=(0, 4))

        # —— 高级设置页 ——
        # These status values are retained for old configs, but the bot now
        # processes every record that has animation video attachments.
        adv = ttk.Frame(nb, padding=10)
        nb.add(adv, text="高级")
        ttk.Label(adv, text="状态字段已保留；机器人不再按状态筛选记录：",
                  foreground="gray").grid(row=0, column=0, columnspan=2,
                                          sticky="w", pady=(0, 6))
        self._row(adv, 1, "状态·保留字段 1", ("status", "to_extract"))
        self._row(adv, 2, "状态·保留字段 2", ("status", "extracting"))
        ttk.Label(adv, text="※ 会处理所有带「动画」视频附件的记录。",
                  foreground="gray").grid(row=3, column=0, columnspan=2,
                                          sticky="w", pady=(6, 0))

        # —— 操作按钮 ——
        btns = ttk.Frame(self.root, padding=(10, 0))
        btns.pack(fill="x")
        ttk.Button(btns, text="保存配置", command=self._save).pack(side="left")
        ttk.Button(btns, text="测试连接", command=self._test).pack(side="left", padx=6)
        self.run_btn = ttk.Button(btns, text="开始运行", command=self._run)
        self.run_btn.pack(side="left", padx=6)
        self.stop_btn = ttk.Button(btns, text="停止", command=self._stop, state="disabled")
        self.stop_btn.pack(side="left")

        # —— 日志区 ——
        logf = ttk.LabelFrame(self.root, text="运行日志", padding=6)
        logf.pack(fill="both", expand=True, padx=10, pady=10)
        self.log_text = tk.Text(logf, height=16, wrap="word", state="disabled")
        self.log_text.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(logf, command=self.log_text.yview)
        sb.pack(side="right", fill="y")
        self.log_text.config(yscrollcommand=sb.set)

    def _row(self, parent, r, label, keys, show=None, default=None):
        ttk.Label(parent, text=label, width=18, anchor="e").grid(
            row=r, column=0, sticky="e", padx=(0, 8), pady=4)
        val = self.cfg.get(keys[0], {}).get(keys[1], default if default is not None else "")
        var = tk.StringVar(value="" if val is None else str(val))
        self.vars[keys] = var
        ent = ttk.Entry(parent, textvariable=var, width=58, show=show)
        ent.grid(row=r, column=1, sticky="we", pady=4)
        parent.columnconfigure(1, weight=1)
        return ent

    def _path_row(self, parent, r, label, keys):
        ent = self._row(parent, r, label, keys)
        def browse():
            d = filedialog.askdirectory()
            if d:
                self.vars[keys].set(d)
        ttk.Button(parent, text="浏览…", command=browse).grid(
            row=r, column=2, padx=4)

    # ── 配置收集/保存 ────────────────────────────────────────────────────
    def _collect(self) -> dict:
        cfg = json.loads(json.dumps(self.cfg))  # deep copy
        for (sec, key), var in self.vars.items():
            cfg.setdefault(sec, {})[key] = var.get().strip()
        # 类型修正
        cfg.setdefault("framepacker", {})
        try:
            cfg["framepacker"]["fps"] = int(float(cfg["framepacker"]["fps"]))
        except (ValueError, KeyError):
            pass
        cfg.setdefault("dedup", {})
        cfg["dedup"]["enabled"] = bool(self.dedup_var.get())
        cfg["dedup"]["renumber"] = bool(self.renumber_var.get())
        try:
            cfg["dedup"]["diff_threshold"] = float(cfg["dedup"]["diff_threshold"])
        except (ValueError, KeyError):
            cfg["dedup"]["diff_threshold"] = 2.5
        return cfg

    def _save(self):
        cfg = self._collect()
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        self.cfg = cfg
        self._emit(f"配置已保存到 {CONFIG_PATH}")
        messagebox.showinfo("保存", "配置已保存。")

    # ── 日志 ──────────────────────────────────────────────────────────────
    def _emit(self, msg: str):
        self.log_queue.put(msg)

    def _poll_log(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.config(state="normal")
                self.log_text.insert("end", msg + "\n")
                self.log_text.see("end")
                self.log_text.config(state="disabled")
        except queue.Empty:
            pass
        self.root.after(150, self._poll_log)

    # ── 测试连接 ──────────────────────────────────────────────────────────
    def _test(self):
        cfg = self._collect()
        def task():
            try:
                from feishu_client import FeishuClient
                c = FeishuClient.from_feishu_config(cfg["feishu"], logger=self._emit)
                recs = c.list_records()
                self._emit(f"✓ 连接成功，读到 {len(recs)} 条记录。")
            except Exception as e:
                self._emit(f"✗ 连接失败: {e}")
        threading.Thread(target=task, daemon=True).start()

    # ── 运行/停止 ─────────────────────────────────────────────────────────
    def _run(self):
        if self.worker and self.worker.is_alive():
            return
        cfg = self._collect()
        self.stop_event.clear()
        self.run_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self._emit("===== 开始运行 =====")

        def task():
            try:
                wf = Workflow(cfg, logger=self._emit, stop_event=self.stop_event)
                wf.run()
            except Exception as e:
                self._emit(f"运行出错: {e}")
                self._emit(traceback.format_exc())
            finally:
                self.root.after(0, self._on_done)

        self.worker = threading.Thread(target=task, daemon=True)
        self.worker.start()

    def _on_done(self):
        self.run_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self._emit("===== 结束 =====")

    def _stop(self):
        self.stop_event.set()
        self._emit("正在停止（完成当前步骤后退出）...")


def main():
    external_settings = _read_external_settings()
    root = tk.Tk()
    App(root, external_settings=external_settings)
    root.mainloop()


if __name__ == "__main__":
    main()
