#!/usr/bin/env python3
"""Generate the single-file visual summary for the animation log audit."""

from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path

from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4, landscape
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "docs" / "animation-log-audit" / "generated"
OUTPUT = ROOT / "output" / "pdf" / "动画管家全量日志审计-可视化总结.pdf"
W, H = landscape(A4)

BG = HexColor("#090B17")
PANEL = HexColor("#141726")
PANEL_2 = HexColor("#1B1E30")
LINE = HexColor("#2C3045")
TEXT = HexColor("#F6F3FF")
MUTED = HexColor("#A8ABC2")
PURPLE = HexColor("#B45CFF")
PINK = HexColor("#FF5CB8")
CYAN = HexColor("#53D7E8")
GREEN = HexColor("#58D6A5")
AMBER = HexColor("#FFC45E")
RED = HexColor("#FF6D82")
BLUE = HexColor("#6B8CFF")


def register_fonts() -> None:
    pdfmetrics.registerFont(TTFont("CN", r"C:\Windows\Fonts\msyh.ttc"))
    pdfmetrics.registerFont(TTFont("CN-Bold", r"C:\Windows\Fonts\msyhbd.ttc"))


def load_data():
    summary = json.loads((DATA / "summary.json").read_text(encoding="utf-8"))
    with (DATA / "direct_tasks.csv").open(encoding="utf-8-sig", newline="") as f:
        tasks = list(csv.DictReader(f))
    with (DATA / "animation_flows.csv").open(encoding="utf-8-sig", newline="") as f:
        flows = list(csv.DictReader(f))
    return summary, tasks, flows


def sec(value: str | float | int | None) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def fmt_duration(value: float | None) -> str:
    if value is None:
        return "-"
    value = max(0, float(value))
    if value < 60:
        return f"{value:.1f}秒"
    minutes, seconds = divmod(round(value), 60)
    if minutes < 60:
        return f"{minutes}分{seconds:02d}秒"
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}时{minutes:02d}分"
    days, hours = divmod(hours, 24)
    return f"{days}天{hours}时"


def split_lines(text: str, max_width: float, font: str, size: float) -> list[str]:
    lines: list[str] = []
    for paragraph in str(text).splitlines() or [""]:
        current = ""
        for char in paragraph:
            candidate = current + char
            if current and pdfmetrics.stringWidth(candidate, font, size) > max_width:
                lines.append(current.rstrip())
                current = char.lstrip()
            else:
                current = candidate
        lines.append(current)
    return lines


def wrapped(c, text, x, y, width, size=10, color=TEXT, font="CN", leading=None, max_lines=None):
    leading = leading or size * 1.45
    lines = split_lines(text, width, font, size)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        tail = lines[-1]
        while tail and pdfmetrics.stringWidth(tail + "…", font, size) > width:
            tail = tail[:-1]
        lines[-1] = tail + "…"
    c.setFont(font, size)
    c.setFillColor(color)
    for line in lines:
        c.drawString(x, y, line)
        y -= leading
    return y


def pill(c, x, y, text, color=PURPLE, fill=None, size=8.5):
    width = pdfmetrics.stringWidth(text, "CN-Bold", size) + 18
    c.setFillColor(fill or HexColor("#2B203D"))
    c.setStrokeColor(color)
    c.roundRect(x, y - 4, width, 20, 10, fill=1, stroke=1)
    c.setFillColor(color)
    c.setFont("CN-Bold", size)
    c.drawCentredString(x + width / 2, y + 2, text)
    return width


def card(c, x, y, w, h, title=None, accent=None, fill=PANEL):
    c.setFillColor(fill)
    c.setStrokeColor(LINE)
    c.roundRect(x, y, w, h, 10, fill=1, stroke=1)
    if accent:
        c.setFillColor(accent)
        c.roundRect(x, y, 5, h, 2.5, fill=1, stroke=0)
    if title:
        c.setFillColor(TEXT)
        c.setFont("CN-Bold", 12)
        c.drawString(x + 16, y + h - 24, title)


def kpi(c, x, y, w, h, label, value, note="", color=PURPLE):
    card(c, x, y, w, h)
    c.setFillColor(MUTED)
    c.setFont("CN", 9)
    c.drawString(x + 14, y + h - 22, label)
    c.setFillColor(color)
    c.setFont("CN-Bold", 23)
    c.drawString(x + 14, y + h - 52, value)
    if note:
        wrapped(c, note, x + 14, y + 14, w - 28, 8, MUTED, max_lines=2)


def page_base(c, page_no, section, title, subtitle=""):
    c.setFillColor(BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setFillColor(PINK)
    c.setFont("CN-Bold", 8.5)
    c.drawString(34, H - 31, f"LILCLICK · ANIMATION AUDIT  /  {section}")
    c.setFillColor(TEXT)
    c.setFont("CN-Bold", 23)
    c.drawString(34, H - 61, title)
    if subtitle:
        c.setFillColor(MUTED)
        c.setFont("CN", 9)
        c.drawString(34, H - 79, subtitle)
    c.setStrokeColor(LINE)
    c.line(34, 25, W - 34, 25)
    c.setFillColor(MUTED)
    c.setFont("CN", 7.5)
    c.drawString(34, 12, "数据截止 2026-07-30 · 来源：状态快照 + assetclaw.db · 审计方法 v1.1")
    c.drawRightString(W - 34, 12, f"{page_no:02d}")
    c.bookmarkPage(f"page-{page_no}")
    c.addOutlineEntry(f"{page_no:02d}  {title}", f"page-{page_no}", level=0, closed=False)


def hbar(c, x, y, w, value, max_value, color, label, value_text, h=13):
    c.setFillColor(HexColor("#25293A"))
    c.roundRect(x, y, w, h, h / 2, fill=1, stroke=0)
    fill_w = max(3, w * value / max_value) if max_value else 0
    c.setFillColor(color)
    c.roundRect(x, y, fill_w, h, h / 2, fill=1, stroke=0)
    c.setFillColor(TEXT)
    c.setFont("CN-Bold", 9)
    c.drawString(x, y + h + 5, label)
    c.setFillColor(color)
    c.drawRightString(x + w, y + h + 5, value_text)


def stacked_bar(c, x, y, w, h, segments, show_values=True):
    total = sum(value for _, value, _ in segments) or 1
    cursor = x
    for label, value, color in segments:
        sw = w * value / total
        c.setFillColor(color)
        c.rect(cursor, y, sw, h, fill=1, stroke=0)
        if show_values and sw > 30:
            c.setFillColor(BG)
            c.setFont("CN-Bold", 8)
            c.drawCentredString(cursor + sw / 2, y + h / 2 - 3, str(value))
        cursor += sw


def bullet(c, x, y, text, width, color=TEXT, dot=PURPLE, size=9.2, max_lines=3):
    c.setFillColor(dot)
    c.circle(x + 3, y + 3, 3, fill=1, stroke=0)
    return wrapped(c, text, x + 14, y + 7, width - 14, size, color, leading=size * 1.5, max_lines=max_lines)


def cover(c):
    c.setFillColor(BG)
    c.rect(0, 0, W, H, fill=1, stroke=0)
    c.setFillColor(HexColor("#21142F"))
    c.circle(W - 80, H - 60, 220, fill=1, stroke=0)
    c.setFillColor(HexColor("#30183C"))
    c.circle(W - 30, H - 25, 135, fill=1, stroke=0)
    c.setFillColor(PURPLE)
    c.roundRect(52, H - 120, 54, 54, 14, fill=1, stroke=0)
    c.setFillColor(TEXT)
    c.setFont("CN-Bold", 22)
    c.drawCentredString(79, H - 102, "Li")
    c.setFillColor(PINK)
    c.setFont("CN-Bold", 10)
    c.drawString(52, H - 160, "LILCLICK · PIPELINE PERFORMANCE")
    c.setFillColor(TEXT)
    c.setFont("CN-Bold", 34)
    c.drawString(52, H - 208, "动画管家全量日志审计")
    c.setFont("CN-Bold", 26)
    c.drawString(52, H - 247, "超级详细可视化总结")
    wrapped(c, "从飞书入口可观测性、抽帧、ComfyUI 抠图、Cherry 后处理、打包投递，到本机 4070 Ti 与三节点 GPU 集群的速度和异常审计。", 52, H - 285, 560, 11, MUTED, leading=18)

    y = 145
    for i, (label, value, color) in enumerate([
        ("直发父任务", "150", PURPLE),
        ("成功任务", "125", GREEN),
        ("动画流程成功", "0 / 26", RED),
        ("集群中位加速", "2.75×", CYAN),
    ]):
        x = 52 + i * 184
        card(c, x, y, 168, 84, fill=HexColor("#141524"))
        c.setFillColor(color)
        c.setFont("CN-Bold", 22)
        c.drawString(x + 14, y + 42, value)
        c.setFillColor(MUTED)
        c.setFont("CN", 8.5)
        c.drawString(x + 14, y + 19, label)
    c.setFillColor(MUTED)
    c.setFont("CN", 8)
    c.drawString(52, 74, "审计日期 2026-07-30（Asia/Shanghai）")
    c.drawString(52, 58, "单文件管理层摘要 · 详细证据、任务 ID、方法口径与实施验收标准均包含在内")
    c.bookmarkPage("page-1")
    c.addOutlineEntry("01  封面", "page-1", level=0, closed=False)
    c.showPage()


def executive(c, s):
    page_base(c, 2, "EXECUTIVE SUMMARY", "一页看懂：现在到底正常不正常", "全量历史与当前面板截断样本已分开，GPU 计算与排队/空转也已分开。")
    kpi(c, 34, 392, 183, 91, "直发成功率", "83.3%", "125 / 150；仍低于建议的 95% 运营门槛", GREEN)
    kpi(c, 229, 392, 183, 91, "当前页面样本", "43", "硬截断：20 图片 + 20 视频 + 3 流程，不是全量", AMBER)
    kpi(c, 424, 392, 183, 91, "集群中位加速", "2.75×", "均值 2.93×；整体接近三倍，但任务间波动大", CYAN)
    kpi(c, 619, 392, 188, 91, "一键流程成功", "0 / 26", "17 失败、9 取消；先修可靠性，再谈性能", RED)

    card(c, 34, 216, 376, 156, "已经确认正常的部分", GREEN)
    y = 334
    y = bullet(c, 52, y, "最近两个 97 帧三节点任务达到 2.425 / 2.638 帧每分，相对本机中位数是 4.36× / 4.74×。", 340, dot=GREEN)
    y = bullet(c, 52, y - 5, "集群远端真实排队中位仅 0.61 秒，P90 1.05 秒；目前无需因全局 queue depth 盲目扩容。", 340, dot=GREEN)
    bullet(c, 52, y - 5, "正常单图 P50 1分38秒、P90 3分11秒；主要时间确实花在抠图。", 340, dot=GREEN)

    card(c, 424, 216, 383, 156, "必须立即处理的部分", RED)
    y = 334
    y = bullet(c, 442, y, "26 个动画流程没有一个 DONE，且所有 stage 都缺可计算的开始/结束时间。", 347, dot=RED)
    y = bullet(c, 442, y - 5, "三条任务出现 38–42 小时空转/恢复间隔，当前页面会误报成 Cherry 计算慢。", 347, dot=RED)
    bullet(c, 442, y - 5, "125 个 DONE 只有 20 个可验证投递回执；成功状态无法证明用户真正收到。", 347, dot=RED)

    card(c, 34, 52, 773, 144, "最终判断", PURPLE, fill=HexColor("#181427"))
    c.setFillColor(PURPLE)
    c.setFont("CN-Bold", 17)
    c.drawString(53, 151, "GPU 集群总体速度基本正常；系统可靠性与可观测性不正常。")
    wrapped(c, "现阶段最值得投入的不是继续微调 GPU 参数，而是修复一键流程、投递回执和 worker 恢复状态机，并把排队、GPU 执行、后处理实际运行、空转间隔独立记录。完成这些后再做同素材 A/B，才能稳定证明三节点是否持续达到目标倍率。", 53, 121, 725, 10.5, TEXT, leading=17)
    c.showPage()


def scope_page(c, s):
    page_base(c, 3, "SCOPE & TRUST", "数据范围、状态与可信度", "审计读取全量父任务状态、动画流程快照和 SQLite 运行记录，而不是只读取前端当前页。")
    categories = [
        ("图片直发", 49, 42, 6, 1, PURPLE),
        ("序列帧 ZIP", 54, 42, 11, 1, PINK),
        ("视频直发", 47, 41, 2, 4, CYAN),
    ]
    card(c, 34, 286, 494, 196, "150 个直发任务状态分布")
    y = 430
    for name, total, done, failed, canceled, color in categories:
        c.setFillColor(TEXT)
        c.setFont("CN-Bold", 10)
        c.drawString(52, y + 10, name)
        c.setFillColor(MUTED)
        c.setFont("CN", 8)
        c.drawRightString(502, y + 10, f"{done}/{total} 成功 · {done/total:.1%}")
        stacked_bar(c, 52, y - 8, 450, 13, [("DONE", done, GREEN), ("FAILED", failed, RED), ("CANCELED", canceled, AMBER)])
        y -= 50
    c.setFont("CN", 8)
    c.setFillColor(GREEN); c.circle(57, 305, 3, fill=1, stroke=0); c.setFillColor(MUTED); c.drawString(65, 302, "DONE 125")
    c.setFillColor(RED); c.circle(145, 305, 3, fill=1, stroke=0); c.setFillColor(MUTED); c.drawString(153, 302, "FAILED 19")
    c.setFillColor(AMBER); c.circle(248, 305, 3, fill=1, stroke=0); c.setFillColor(MUTED); c.drawString(256, 302, "CANCELED 6")

    card(c, 542, 286, 265, 196, "一键动画流程", RED)
    c.setFillColor(RED)
    c.setFont("CN-Bold", 35)
    c.drawString(562, 415, "0")
    c.setFillColor(TEXT)
    c.setFont("CN-Bold", 12)
    c.drawString(598, 421, "/ 26 DONE")
    stacked_bar(c, 562, 374, 225, 16, [("FAILED", 17, RED), ("CANCELED", 9, AMBER)])
    wrapped(c, "17 FAILED（其中 15 条 worker 退出/僵死清理）\n9 CANCELED\n0 个流程有完整阶段时间", 562, 350, 220, 9.5, MUTED, leading=19)

    card(c, 34, 52, 773, 214, "数据可信度与缺口")
    items = [
        ("高", "父任务数量、状态、端到端时间", "状态快照直接记录", GREEN),
        ("高", "集群真实排队与 GPU 执行", "有 gpu_control 远端时间的样本", GREEN),
        ("高", "本机逐帧 / Cherry active time", "有 prompt 与 attempt 记录的样本", GREEN),
        ("中", "阶段基线", "125 个成功中 115 个时间线一致", AMBER),
        ("低", "飞书收到到附件落盘", "缺 event_received / download_finished", RED),
        ("低", "一键流程七步耗时", "26 个流程 stage 均无完整时间", RED),
        ("低", "用户是否真正收到", "125 个 DONE 只有 20 个持久回执", RED),
    ]
    y = 226
    for level, metric, reason, color in items:
        pill(c, 52, y - 3, level, color=color, fill=HexColor("#202334"), size=8)
        c.setFillColor(TEXT); c.setFont("CN-Bold", 9); c.drawString(103, y, metric)
        c.setFillColor(MUTED); c.setFont("CN", 8.3); c.drawString(333, y, reason)
        y -= 24
    c.showPage()


def e2e_page(c, s):
    page_base(c, 4, "END-TO-END", "端到端耗时：三类任务不能用一个平均数代表", "P50/P90 使用全部成功父任务；横条采用对数刻度，避免 41 小时异常把正常区间压扁。")
    groups = s["groups"]
    rows = [
        ("图片直发", groups["category:image_direct"]["e2e_task_s"], PURPLE, "42 成功"),
        ("序列帧 ZIP", groups["category:sequence_zip"]["e2e_task_s"], PINK, "42 成功"),
        ("视频直发", groups["category:video_direct"]["e2e_task_s"], CYAN, "41 成功"),
        ("全部直发", groups["all"]["e2e_task_s"], AMBER, "125 成功"),
    ]
    card(c, 34, 246, 520, 236, "全量成功任务 P50 / P90")
    max_log = math.log10(max(r[1]["p90"] for r in rows) + 1)
    y = 421
    for name, st, color, note in rows:
        c.setFillColor(TEXT); c.setFont("CN-Bold", 10); c.drawString(52, y + 18, name)
        c.setFillColor(MUTED); c.setFont("CN", 8); c.drawString(126, y + 18, note)
        hbar(c, 52, y - 5, 366, math.log10(st["p90"] + 1), max_log, color, "", fmt_duration(st["p90"]), 12)
        p50x = 52 + 366 * math.log10(st["p50"] + 1) / max_log
        c.setStrokeColor(TEXT); c.setLineWidth(1.2); c.line(p50x, y - 9, p50x, y + 11)
        c.setFillColor(TEXT); c.setFont("CN", 7.5); c.drawString(430, y - 2, f"P50 {fmt_duration(st['p50'])}")
        y -= 48

    card(c, 569, 246, 238, 236, "为什么当前页面更快", AMBER)
    c.setFillColor(AMBER); c.setFont("CN-Bold", 27); c.drawString(588, 414, "9分51秒")
    c.setFillColor(MUTED); c.setFont("CN", 8.5); c.drawString(588, 395, "当前页面 P50")
    c.setFillColor(TEXT); c.setFont("CN-Bold", 17); c.drawString(588, 354, "43 条 ≠ 全量")
    wrapped(c, "最新 20 图片 + 最新 20 视频 + 3 流程。图片窗口从 7 月 29 日开始，视频窗口却延伸到 7 月 25 日；时间范围不对称，也没有显示截断状态。", 588, 330, 197, 9.2, MUTED, leading=15)
    pill(c, 588, 272, "不能用于容量规划", RED, HexColor("#2A1C28"), 8.5)

    card(c, 34, 52, 773, 174, "怎么读这些长尾")
    y = 184
    y = bullet(c, 53, y, "图片正常区间已经较快，但两条约 41 小时的 worker 恢复异常把最大值拉高；不能用均值评价单图。", 735, dot=PURPLE)
    y = bullet(c, 53, y - 3, "序列 ZIP 受帧数、像素和单 prompt 超时影响，必须同时展示秒/帧与 Mpixel/s。", 735, dot=PINK)
    y = bullet(c, 53, y - 3, "视频历史长尾主要混有本地单 GPU 等待，不等于抽帧慢；补 queue_entered / execution_started 后才能正确归因。", 735, dot=CYAN)
    bullet(c, 53, y - 3, "全量 P50 19分44秒只是任务组合统计，不应成为跨类型 SLA。", 735, dot=AMBER)
    c.showPage()


def stage_page(c, s):
    page_base(c, 5, "PIPELINE", "每一步花了多久：阶段中位数", "阶段值只使用时间线一致的成功样本；端到端与阶段 cohort 不完全相同，不能机械相加。")
    data = [
        ("图片直发", "41 / 42", [("准备", 0.542, PURPLE), ("抠图", 83.877, CYAN), ("衔接", 2.253, BLUE), ("Cherry", 6.732, PINK), ("投递", 7.837, AMBER)]),
        ("序列帧 ZIP", "35 / 42", [("准备", 0.473, PURPLE), ("抠图", 1127.395, CYAN), ("衔接", 1.957, BLUE), ("Cherry", 75.754, PINK), ("投递", 25.566, AMBER)]),
        ("视频直发", "39 / 41", [("准备*", 3.114, PURPLE), ("抠图", 2783.922, CYAN), ("衔接", 5.810, BLUE), ("Cherry", 325.897, PINK), ("投递", 106.741, AMBER)]),
    ]
    y0 = 359
    for idx, (name, cohort, stages) in enumerate(data):
        y = y0 - idx * 115
        card(c, 34, y, 773, 98)
        c.setFillColor(TEXT); c.setFont("CN-Bold", 13); c.drawString(51, y + 62, name)
        c.setFillColor(MUTED); c.setFont("CN", 8); c.drawString(51, y + 43, f"阶段可信样本 {cohort}")
        start_x = 180
        box_w = 112
        for j, (label, value, color) in enumerate(stages):
            x = start_x + j * 119
            c.setFillColor(HexColor("#202335")); c.roundRect(x, y + 18, box_w, 58, 7, fill=1, stroke=0)
            c.setFillColor(color); c.setFont("CN-Bold", 8.5); c.drawString(x + 9, y + 57, label)
            c.setFillColor(TEXT); c.setFont("CN-Bold", 12); c.drawString(x + 9, y + 34, fmt_duration(value))
            if j < len(stages) - 1:
                c.setStrokeColor(LINE); c.line(x + box_w, y + 47, x + 118, y + 47)

    card(c, 34, 52, 773, 86, "关键解释", PURPLE)
    wrapped(c, "正常单图和视频的绝对瓶颈是抠图。ZIP 的 Cherry 包络明显高于实际 attempt，说明存在批次间空档。视频“准备*”在新任务通常只有数秒，但历史任务可混入数小时本机队列等待，因此当前字段不能用于证明 FFmpeg 慢。投递通常较短，但 2 帧任务 IMG_D48264D8B767 的投递达到 8分27秒，是明确异常。", 52, 101, 735, 9.3, TEXT, leading=15)
    c.showPage()


def gpu_page(c, s):
    page_base(c, 6, "GPU BENCHMARK", "本机 4070 Ti vs 三节点 GPU 集群", "基于现有高可信成功视频：本机逐 prompt 时间；集群仅计算 remote started → finished。")
    speed = s["speed_comparison"]
    local = speed["local_4070ti"]
    cluster = speed["cluster_pure_execution"]
    card(c, 34, 278, 440, 204, "吞吐对比（帧/分）")
    hbar(c, 57, 393, 360, local["p50"], cluster["p90"], PURPLE, "本机 4070 Ti · 中位数 · n=8", f"{local['p50']:.3f}")
    hbar(c, 57, 326, 360, cluster["p50"], cluster["p90"], CYAN, "GPU 集群纯执行 · 中位数 · n=12", f"{cluster['p50']:.3f}")
    c.setFillColor(MUTED); c.setFont("CN", 7.5); c.drawString(57, 296, "同一横轴；集群条不包含上传、排队、下载和解压。")

    card(c, 488, 278, 319, 204, "速度判断", GREEN)
    c.setFillColor(CYAN); c.setFont("CN-Bold", 34); c.drawString(510, 409, f"{speed['median_speedup']:.2f}×")
    c.setFillColor(TEXT); c.setFont("CN-Bold", 12); c.drawString(620, 417, "中位加速")
    c.setFillColor(GREEN); c.setFont("CN-Bold", 21); c.drawString(510, 367, f"{speed['mean_speedup']:.2f}×")
    c.setFillColor(MUTED); c.setFont("CN", 8.5); c.drawString(590, 373, "均值加速")
    wrapped(c, "结论：整体基本符合“约三倍”的工程预期，但不代表每个任务都恒定三倍。节点异构、分辨率、缓存、并发和分片拖尾都会造成波动。", 510, 337, 275, 9.2, TEXT, leading=15)

    card(c, 34, 140, 773, 118, "最近两个 97 帧三节点任务")
    recent = [
        ("VID_8DE68A9E8025", "40分00秒", 2.425, "55 / 27 / 15 帧", "4.36×"),
        ("VID_5B1FF7F20EA1", "36分46秒", 2.638, "42 / 27 / 28 帧", "4.74×"),
    ]
    y = 218
    for tid, duration, throughput, dist, ratio in recent:
        c.setFillColor(TEXT); c.setFont("CN-Bold", 9); c.drawString(52, y, tid)
        c.setFillColor(MUTED); c.setFont("CN", 8); c.drawString(208, y, f"纯 GPU {duration}")
        c.setFillColor(CYAN); c.setFont("CN-Bold", 10); c.drawString(335, y, f"{throughput:.3f} 帧/分")
        c.setFillColor(MUTED); c.setFont("CN", 8); c.drawString(458, y, f"节点分片 {dist}")
        c.setFillColor(GREEN); c.setFont("CN-Bold", 11); c.drawRightString(785, y, ratio)
        y -= 40

    card(c, 34, 52, 773, 68, "重要限制", AMBER)
    wrapped(c, "本轮是历史日志基准，不是新提交生产任务的实验。现有样本未统一输入分辨率、总像素、冷/热缓存和同时负载。要最终验收“稳定三倍”，应使用同一 100 帧素材，固定工作流和模型，分别运行本机与 1/2/3 节点热启动 5 次。", 52, 75, 735, 9.1, TEXT, leading=14)
    c.showPage()


def cluster_page(c, s):
    page_base(c, 7, "CLUSTER INTERNALS", "GPU 集群到底慢在哪里", "12 个成功视频有完整远端四段时间；真实排队与纯 GPU 计算已分离。")
    phases = [
        ("客户端准备/上传", 2.456, PURPLE, "P50"),
        ("远端真实排队", 0.607, AMBER, "P50"),
        ("纯 GPU 执行", 3379.851, CYAN, "P50"),
        ("结果发布/返回", 12.343, PINK, "P50"),
    ]
    card(c, 34, 294, 773, 188, "集群视频阶段 P50（时间轴按比例压缩展示）")
    x = 52
    y = 374
    widths = [135, 125, 270, 175]
    for (label, value, color, _), width in zip(phases, widths):
        c.setFillColor(HexColor("#202335")); c.roundRect(x, y, width, 61, 8, fill=1, stroke=0)
        c.setFillColor(color); c.setFont("CN-Bold", 9); c.drawString(x + 10, y + 40, label)
        c.setFillColor(TEXT); c.setFont("CN-Bold", 13); c.drawString(x + 10, y + 17, fmt_duration(value))
        x += width + 10
    c.setFillColor(CYAN); c.setFont("CN-Bold", 18); c.drawString(52, 326, ">99% 的远端中位时间在 GPU 执行，不在排队。")
    c.setFillColor(MUTED); c.setFont("CN", 8.5); c.drawRightString(787, 326, "排队 P90 1.05秒 · 最大 8.53秒")

    card(c, 34, 142, 375, 132, "明确慢点：VID_9D9EB9ACE6A1", RED)
    c.setFillColor(RED); c.setFont("CN-Bold", 23); c.drawString(53, 224, "1.179 帧/分")
    c.setFillColor(TEXT); c.setFont("CN-Bold", 10); c.drawString(201, 231, "97 帧 · 2 节点")
    wrapped(c, "真实排队仅 0.60 秒，但纯 GPU 执行 82分17秒。问题在执行期，应查单节点每帧耗时、显存换页、并发、模型加载和最后分片拖尾。", 53, 200, 335, 9.2, MUTED, leading=15)

    card(c, 424, 142, 383, 132, "节点数不能直接做因果结论", AMBER)
    c.setFillColor(TEXT); c.setFont("CN-Bold", 11); c.drawString(443, 224, "2 节点中位 1.819 · 3 节点中位 1.512")
    wrapped(c, "这不是“两节点更快”。两组素材、分辨率、缓存和负载不同，2 节点组还有两个约 4.7 帧/分的快样本。必须做同素材 A/B 并记录节点 telemetry。", 443, 199, 343, 9.2, MUTED, leading=15)

    card(c, 34, 52, 773, 70, "优化方向", GREEN)
    wrapped(c, "按节点历史像素吞吐加权分片；记录每节点帧数、GPU 秒和 P50/P95 帧延迟；大任务启用动态任务窃取；对最后少量慢帧做推测性重算；保持模型常驻并并行回传结果。", 52, 77, 735, 9.4, TEXT, leading=15)
    c.showPage()


def anomaly_page(c, tasks):
    page_base(c, 8, "ANOMALIES", "最需要关注的异常任务", "异常不是简单“耗时长”：下图把实际工作时间和空转/恢复时间拆开。")
    ids = ["IMG_65E2185AA833", "IMG_288AA489B664", "VID_BD18EE2C8C8F"]
    lookup = {r["task_id"]: r for r in tasks}
    selected = [lookup[i] for i in ids]
    card(c, 34, 256, 773, 226, "Cherry 包络：实际运行 vs 非运行间隔")
    y = 411
    for row in selected:
        active = sec(row["postprocess_attempt_sum_s"])
        idle = sec(row["postprocess_idle_gap_s"])
        total = max(active + idle, 1)
        c.setFillColor(TEXT); c.setFont("CN-Bold", 9.5); c.drawString(52, y + 18, row["task_id"])
        c.setFillColor(MUTED); c.setFont("CN", 8); c.drawString(220, y + 18, row["name"][:24])
        stacked_bar(c, 52, y - 2, 565, 18, [("ACTIVE", active, GREEN), ("IDLE", idle, RED)], show_values=False)
        c.setFillColor(GREEN); c.setFont("CN-Bold", 8); c.drawString(632, y + 8, f"实际 {fmt_duration(active)}")
        c.setFillColor(RED); c.drawRightString(787, y + 8, f"空转 {fmt_duration(idle)}")
        y -= 58
    c.setFillColor(MUTED); c.setFont("CN", 7.5); c.drawString(52, 273, "绿色段因比例极小可能仅显示最小宽度；精确时间以右侧标签为准。")

    card(c, 34, 52, 245, 184, "小 ZIP 投递异常", AMBER)
    c.setFillColor(AMBER); c.setFont("CN-Bold", 16); c.drawString(52, 192, "IMG_D48264D8B767")
    wrapped(c, "只有 2 帧：\n抠图 3分13秒\nCherry 9.7秒\n投递却用 8分27秒", 52, 165, 205, 10, TEXT, leading=20)
    c.setFillColor(MUTED); c.setFont("CN", 8); c.drawString(52, 72, "查上传重试、限流和回执写回。")

    card(c, 294, 52, 245, 184, "历史本机等待误归因", PURPLE)
    c.setFillColor(PURPLE); c.setFont("CN-Bold", 16); c.drawString(312, 192, "11–14 小时")
    wrapped(c, "多个 tasha_walk 视频在任务创建后等待数小时才进入 ComfyUI。当前被标为“创建与抽帧”，实质更像单 GPU 队列等待。", 312, 163, 205, 9.4, TEXT, leading=16)
    c.setFillColor(MUTED); c.setFont("CN", 8); c.drawString(312, 72, "补 queue_entered 才能正确定位。")

    card(c, 554, 52, 253, 184, "异常检测结论", RED)
    c.setFillColor(RED); c.setFont("CN-Bold", 27); c.drawString(574, 186, "66")
    c.setFillColor(TEXT); c.setFont("CN-Bold", 10); c.drawString(620, 194, "个稳健异常指标")
    wrapped(c, "按类别 + backend 分组，对端到端、阶段耗时与单位吞吐使用 median / MAD。建议样本不足 20 时只做规则告警。", 574, 158, 211, 9.4, TEXT, leading=16)
    c.setFillColor(MUTED); c.setFont("CN", 8); c.drawString(574, 72, "详细证据在 robust_outliers.csv。")
    c.showPage()


def failure_page(c):
    page_base(c, 9, "RELIABILITY", "失败结构：先消灭可恢复故障", "19 个直发失败与 26 个流程零成功，说明可靠性问题比排队优化更紧急。")
    reasons = [
        ("Comfy / 输出校验 / prompt 卡死", 13, RED),
        ("matte 目录无输出", 2, AMBER),
        ("Cherry 浏览器启动失败", 2, PINK),
        ("图片 worker 退出", 1, PURPLE),
        ("角色确认超时", 1, BLUE),
    ]
    card(c, 34, 250, 440, 232, "19 个直发 FAILED 原因")
    maxv = max(v for _, v, _ in reasons)
    y = 421
    for label, value, color in reasons:
        hbar(c, 53, y, 365, value, maxv, color, label, str(value), 11)
        y -= 38

    card(c, 489, 250, 318, 232, "序列 ZIP 的主要失败模式", RED)
    c.setFillColor(RED); c.setFont("CN-Bold", 29); c.drawString(510, 414, "11 + 1")
    c.setFillColor(MUTED); c.setFont("CN", 8.5); c.drawString(613, 422, "FAILED + CANCELED")
    y = 378
    y = bullet(c, 510, y, "单 prompt 600 秒未完成；重试 3 次仍失败。", 275, dot=RED)
    y = bullet(c, 510, y - 3, "单帧故障击穿整批，缺 checkpoint/requeue。", 275, dot=RED)
    y = bullet(c, 510, y - 3, "本机卡死后缺健康检查、重启和自动切集群。", 275, dot=RED)
    bullet(c, 510, y - 3, "建议失败帧单独重排并复用已完成产物。", 275, dot=GREEN)

    card(c, 34, 52, 773, 178, "一键动画流程：当前最大故障")
    c.setFillColor(RED); c.setFont("CN-Bold", 35); c.drawString(53, 160, "0 / 26")
    c.setFillColor(TEXT); c.setFont("CN-Bold", 12); c.drawString(53, 137, "成功流程")
    stacked_bar(c, 180, 153, 300, 20, [("FAILED", 17, RED), ("CANCELED", 9, AMBER)])
    c.setFillColor(MUTED); c.setFont("CN", 8); c.drawString(180, 134, "17 FAILED：15 worker 退出 + 2 飞书表格凭据/权限")
    wrapped(c, "处理顺序：权限预检 → worker 心跳与退出信息 → stage 时间 → 最小流程连续 10 次 → 恢复批量入口。当前 0 成功，应先修可靠性，不讨论流程 P90。", 53, 108, 720, 9.2, TEXT, leading=15)
    pill(c, 637, 152, "P0 立即处理", RED, HexColor("#2A1A24"), 9)
    c.showPage()


def dashboard_page(c):
    page_base(c, 10, "DASHBOARD AUDIT", "现有分析页：适合概览，不足以做根因定位", "保留现有信息架构，但必须修正 cohort、阶段归因、投递状态和数据可信度。")
    card(c, 34, 288, 245, 194, "已经做对", GREEN)
    y = 436
    for text in [
        "父任务端到端、P50/P90 与阶段条已经具备。",
        "能按图片、视频查看任务级证据。",
        "明确提示飞书入口时间暂未计入。",
        "作为最近任务运营概览是可用的。",
    ]:
        y = bullet(c, 52, y, text, 208, dot=GREEN, size=9, max_lines=2) - 5

    card(c, 297, 288, 245, 194, "会误导判断", RED)
    y = 436
    for text in [
        "固定 limit:20 却显示成整体统计。",
        "全局 queue depth 被当作任务排队。",
        "Cherry 包络把 worker 空转算成计算。",
        "DONE 可在没有 delivery ack 时出现。",
    ]:
        y = bullet(c, 315, y, text, 208, dot=RED, size=9, max_lines=2) - 5

    card(c, 560, 288, 247, 194, "必须增加", PURPLE)
    y = 436
    for text in [
        "时间范围、样本数、截断状态、可信度。",
        "真实 queue / GPU / active / idle 分段。",
        "秒/帧、Mpixel/s、版本与节点维度。",
        "失败 Pareto、流程漏斗、投递确认率。",
    ]:
        y = bullet(c, 578, y, text, 210, dot=PURPLE, size=9, max_lines=2) - 5

    card(c, 34, 52, 773, 216, "推荐的新页面结构")
    sections = [
        ("01", "运营健康", "成功率 / P90 / 投递确认 / worker 与节点健康", GREEN),
        ("02", "分段速度", "入口→下载→抽帧→排队→GPU→后处理→投递", CYAN),
        ("03", "异常失败", "MAD 异常 / idle gap / 重试 / 慢节点 / Pareto", RED),
        ("04", "一键流程", "七步漏斗 / 每步耗时 / 子任务 / 心跳", PURPLE),
    ]
    x = 52
    for num, title, desc, color in sections:
        c.setFillColor(HexColor("#202335")); c.roundRect(x, 88, 171, 128, 8, fill=1, stroke=0)
        c.setFillColor(color); c.setFont("CN-Bold", 18); c.drawString(x + 12, 183, num)
        c.setFillColor(TEXT); c.setFont("CN-Bold", 11); c.drawString(x + 12, 157, title)
        wrapped(c, desc, x + 12, 135, 147, 8.5, MUTED, leading=14, max_lines=4)
        x += 184
    c.showPage()


def roadmap_page(c):
    page_base(c, 11, "ROADMAP", "优化路线图：先可靠，再提速", "每一级都给出可验收结果，避免“做了优化但无法证明有效”。")
    phases = [
        ("P0 · 1–2 天", RED, ["修复流程权限与 worker 退出", "补心跳、stage 时间、退出信息", "DONE 必须绑定投递回执", "空转 >5分钟自动恢复/告警"], "最小流程连续 10 次成功 ≥90%；新成功任务回执 100%"),
        ("P1 · 3–7 天", AMBER, ["Comfy 单帧 checkpoint / 重排", "卡死后健康检查、重启、切集群", "抽帧 / 排队 / 执行独立事件", "分析页全量分页与 cohort"], "ZIP 整批失败下降 80%；新任务 95% 阶段可互斥归因"),
        ("P2 · 1–2 周", CYAN, ["节点遥测与加权分片", "动态调度 / 慢帧推测性重算", "浏览器池、流水线打包与上传", "同素材 A/B 基准"], "三节点中位 ≥2.7×、P90 ≥2.2×；小文件投递 P95 <60秒"),
        ("P3 · 持续", PURPLE, ["容量模型与队列仿真", "版本变更点检测", "质量 + 速度联合指标", "自动调度和容量预测"], "按版本/像素/节点持续回归，无性能换质量"),
    ]
    x = 34
    for title, color, actions, accept in phases:
        card(c, x, 112, 184, 370, accent=color)
        c.setFillColor(color); c.setFont("CN-Bold", 13); c.drawString(x + 18, 446, title)
        y = 410
        for action in actions:
            y = bullet(c, x + 18, y, action, 148, dot=color, size=8.7, max_lines=3) - 10
        c.setStrokeColor(LINE); c.line(x + 18, 215, x + 166, 215)
        c.setFillColor(MUTED); c.setFont("CN-Bold", 8); c.drawString(x + 18, 197, "验收")
        wrapped(c, accept, x + 18, 176, 148, 8.4, TEXT, leading=14, max_lines=5)
        x += 198
    card(c, 34, 52, 773, 42, fill=HexColor("#181427"))
    c.setFillColor(PURPLE); c.setFont("CN-Bold", 10); c.drawString(52, 67, "资源优先顺序")
    c.setFillColor(TEXT); c.setFont("CN", 9); c.drawString(144, 67, "流程与状态机可靠性  →  事件完整度  →  卡死恢复  →  节点调度  →  算法微调")
    c.showPage()


def observability_page(c):
    page_base(c, 12, "OBSERVABILITY", "把完整全链路真正测出来", "新增 append-only 事件表和统一 trace_id；状态快照继续保留，但不再承担历史审计。")
    events = [
        ("飞书收到", "event_received", PURPLE),
        ("附件下载", "download_finished", BLUE),
        ("抽帧", "extract_finished", AMBER),
        ("排队", "worker_assigned", PINK),
        ("GPU", "matte_finished", CYAN),
        ("后处理", "cherry_finished", PURPLE),
        ("打包", "package_finished", AMBER),
        ("回执", "send_ack", GREEN),
    ]
    card(c, 34, 333, 773, 149, "一个任务、一条 trace、八个必需业务节点")
    x = 51
    for i, (label, event, color) in enumerate(events):
        c.setFillColor(color); c.circle(x + 40, 407, 16, fill=1, stroke=0)
        c.setFillColor(BG); c.setFont("CN-Bold", 9); c.drawCentredString(x + 40, 404, str(i + 1))
        c.setFillColor(TEXT); c.setFont("CN-Bold", 8.5); c.drawCentredString(x + 40, 374, label)
        c.setFillColor(MUTED); c.setFont("CN", 6.5); c.drawCentredString(x + 40, 360, event)
        if i < len(events) - 1:
            c.setStrokeColor(LINE); c.setLineWidth(2); c.line(x + 57, 407, x + 80, 407)
        x += 91

    card(c, 34, 145, 374, 168, "最先落地的字段")
    wrapped(c, "trace_id · task_id · parent_task_id · task_type\nstage · event_name · attempt · worker_id\nbackend · node_id · event_at_utc · monotonic_ns\nframes · pixels · bytes · workflow_version\nqueue_depth · concurrent_jobs · error_code", 52, 274, 335, 9.2, TEXT, leading=19)

    card(c, 423, 145, 384, 168, "必须直接展示的派生指标")
    metrics = [
        "true_e2e = send_ack - event_received",
        "queue_wait 与 gpu_service 分离",
        "active_post 与 post_idle_gap 分离",
        "加权吞吐 = Σframes / ΣGPU分钟",
        "straggler_ratio 与 delivery confirmation rate",
    ]
    y = 274
    for text in metrics:
        y = bullet(c, 442, y, text, 340, dot=CYAN, size=9, max_lines=2) - 4

    card(c, 34, 52, 773, 73, "异常检测技术")
    wrapped(c, "先用确定性规则（心跳、超时、空转、无回执、时间倒序），再按任务类型、后端、工作流版本、分辨率和节点数做 median/MAD；样本稳定后再启用 EWMA/CUSUM 版本漂移和 Isolation Forest。", 52, 79, 735, 9.2, TEXT, leading=15)
    c.showPage()


def decisions_page(c):
    page_base(c, 13, "DECISIONS", "建议现在就做的六个决定", "这六项能把当前审计结论转化为可交付的工程工作。")
    decisions = [
        ("01", "把一键流程定为 P0 故障", "0/26 成功，不再将其与普通性能任务混合统计。", RED),
        ("02", "DONE 必须绑定投递回执", "没有 send_ack 的任务使用 OUTPUT_READY，不宣称用户已收到。", GREEN),
        ("03", "拆 active 与 idle", "Cherry/GPU/worker 的实际运行与等待、恢复间隔分别计时。", PINK),
        ("04", "统计改为服务端全量 cohort", "所有卡片显示起止时间、样本数、截断和数据可信度。", PURPLE),
        ("05", "批准同素材 GPU A/B", "固定 100 帧、workflow/model，1/2/3 节点各热跑 5 次。", CYAN),
        ("06", "按验收指标排优化", "先成功率和回执，再看秒/帧、Mpixel/s 与节点拖尾。", AMBER),
    ]
    positions = [(34, 328), (298, 328), (562, 328), (34, 126), (298, 126), (562, 126)]
    for (num, title, desc, color), (x, y) in zip(decisions, positions):
        card(c, x, y, 245, 178, accent=color)
        c.setFillColor(color); c.setFont("CN-Bold", 20); c.drawString(x + 18, y + 135, num)
        c.setFillColor(TEXT); c.setFont("CN-Bold", 11); c.drawString(x + 18, y + 105, title)
        wrapped(c, desc, x + 18, y + 81, 205, 9.2, MUTED, leading=16, max_lines=4)
    card(c, 34, 52, 773, 53, fill=HexColor("#181427"))
    c.setFillColor(PURPLE); c.setFont("CN-Bold", 11); c.drawString(52, 73, "一句话结论")
    c.setFillColor(TEXT); c.setFont("CN-Bold", 11); c.drawString(142, 73, "GPU 集群大体正常；流程、回执、恢复与事件记录必须先修。")
    c.showPage()


def appendix_page(c, s):
    page_base(c, 14, "APPENDIX", "口径、限制与证据索引", "本页用于复核数字，避免把弱代理指标当成精确事实。")
    card(c, 34, 267, 374, 215, "统计口径")
    lines = [
        "端到端：父任务 created_at → delivery 或终态 updated_at。",
        "成功：DONE / DONE_WITH_ERRORS / COMPLETED / SUCCESS。",
        "阶段基线：只含 115 个时间点单调的成功任务。",
        "集群速度：remote started_at → finished_at。",
        "本机速度：逐 prompt 完成时间。",
        "异常：类别 + backend 分组的 median/MAD 稳健 Z。",
    ]
    y = 434
    for line in lines:
        y = bullet(c, 52, y, line, 335, dot=PURPLE, size=8.8, max_lines=2) - 4

    card(c, 423, 267, 384, 215, "不能从现有数据断言")
    lines = [
        "文件 mtime 不能等同于飞书事件抵达时间。",
        "创建到 handshake 不能等同于纯抽帧。",
        "全局 queue_depth 不能等同于该任务排队时间。",
        "Cherry 包络不能等同于浏览器实际计算时间。",
        "节点数分组不能替代同素材 A/B 因果实验。",
        "DONE 不能在缺回执时自动等同于用户已收到。",
    ]
    y = 434
    for line in lines:
        y = bullet(c, 442, y, line, 343, dot=RED, size=8.8, max_lines=2) - 4

    card(c, 34, 52, 773, 195, "证据文件（工作区相对路径）")
    files = [
        ("全量任务", "docs/animation-log-audit/generated/direct_tasks.csv", "150 行"),
        ("动画流程", "docs/animation-log-audit/generated/animation_flows.csv", "26 行"),
        ("异常明细", "docs/animation-log-audit/generated/robust_outliers.csv", "66 行"),
        ("机器汇总", "docs/animation-log-audit/generated/summary.json", "方法 v1.1"),
        ("审计脚本", "scripts/audit_animation_logs.py", "可重复生成"),
        ("PDF 脚本", "scripts/generate_animation_audit_pdf.py", "本文件来源"),
    ]
    y = 206
    for label, path, note in files:
        c.setFillColor(TEXT); c.setFont("CN-Bold", 8.5); c.drawString(52, y, label)
        c.setFillColor(MUTED); c.setFont("CN", 8); c.drawString(130, y, path)
        c.setFillColor(PURPLE); c.setFont("CN-Bold", 8); c.drawRightString(785, y, note)
        y -= 25
    c.setFillColor(MUTED); c.setFont("CN", 7.5); c.drawString(52, 66, "本 PDF 基于现有历史日志完成，没有向生产 GPU 新提交任务。建议在修复事件记录后执行正式 A/B。")
    c.showPage()


def main() -> int:
    register_fonts()
    summary, tasks, _flows = load_data()
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(OUTPUT), pagesize=(W, H), pageCompression=1)
    c.setTitle("动画管家全量日志审计 - 超级详细可视化总结")
    c.setAuthor("Codex / LilClick Animation")
    c.setSubject("动画管家全链路性能、GPU 对比、异常与优化审计")
    cover(c)
    executive(c, summary)
    scope_page(c, summary)
    e2e_page(c, summary)
    stage_page(c, summary)
    gpu_page(c, summary)
    cluster_page(c, summary)
    anomaly_page(c, tasks)
    failure_page(c)
    dashboard_page(c)
    roadmap_page(c)
    observability_page(c)
    decisions_page(c)
    appendix_page(c, summary)
    c.save()
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
