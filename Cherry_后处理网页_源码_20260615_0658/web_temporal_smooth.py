"""
Cherry - 帧序列处理工具（时序平滑 + 锐化）
运行: python3 web_temporal_smooth.py
浏览器访问: http://localhost:8765
"""

import io, os, sys, zipfile, traceback, json
import cv2, numpy as np, torch
import torch.nn.functional as F
from flask import Flask, jsonify, request, send_file, render_template_string

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024


# ── 算法 ────────────────────────────────────────────────────────────────────
def alpha_denoise(imgs, 阈值=0.06, 平滑半径=0):
    imgs = imgs.float().clamp(0, 1)
    B, H, W, C = imgs.shape
    if C < 4: return imgs
    rgb   = imgs[..., :3]
    alpha = imgs[..., 3]
    cleaned = torch.where(alpha < float(阈值), torch.zeros_like(alpha), alpha)
    if int(平滑半径) > 0:
        ksize = int(平滑半径) * 2 + 1
        a_np  = cleaned.numpy()
        out   = np.zeros_like(a_np)
        for b in range(B):
            blurred = cv2.GaussianBlur(a_np[b], (ksize, ksize), sigmaX=0)
            core    = a_np[b] > 0.5
            out[b]  = np.where(core, np.maximum(blurred, a_np[b]), blurred)
        cleaned = torch.from_numpy(out.astype(np.float32))
    return torch.cat([rgb, cleaned.unsqueeze(-1)], dim=-1).clamp(0, 1)


def edge_color_fix(imgs, 边缘宽度=8, 修正强度=1.0):
    """
    边缘颜色修正：
    1. 对 alpha>0.5 的实心区做形态学腐蚀，得到干净的内核
    2. 内核向外扩展 边缘宽度 像素的环形区域作为待修正边缘
       （同时捕获半透明 alpha 过渡像素和二值 alpha 的边界像素）
    3. 对边缘区域每个像素，用距离变换找最近的内核像素并采色
    alpha 不变，只替换边缘 RGB，彻底去除背景污染色。
    """
    from scipy.ndimage import distance_transform_edt

    imgs = imgs.float().clamp(0, 1)
    B, H, W, C = imgs.shape
    if C < 4: return imgs

    rgb   = imgs[..., :3].numpy()
    alpha = imgs[..., 3].numpy()
    result_rgb = rgb.copy()

    n = max(1, int(边缘宽度))
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (n*2+1, n*2+1))

    for b in range(B):
        a  = alpha[b]
        im = rgb[b]

        solid = (a > 0.5).astype(np.uint8)          # 实心区（含半透明像素）

        if solid.sum() == 0:
            print(f"[边缘颜色修正] 帧{b} 无实心像素，跳过")
            continue

        # 腐蚀得到干净内核（远离边界的像素）
        inner = cv2.erode(solid, kernel)             # 内核 (uint8)

        if inner.sum() == 0:
            # 字符太小，腐蚀后消失 → 直接用 solid 作为内核
            inner = solid

        # 待修正区：实心但不在内核 + 半透明像素
        edge_mask = ((solid.astype(bool)) & (~inner.astype(bool))) | \
                    ((a > 0.01) & (a <= 0.5))

        if not edge_mask.any():
            print(f"[边缘颜色修正] 帧{b} 无边缘像素")
            continue

        # 对每个像素找最近的内核像素，采样其 RGB
        _, idx = distance_transform_edt(~inner.astype(bool), return_indices=True)
        fill_color = im[idx[0], idx[1], :]           # (H,W,3)

        t = float(修正强度)
        fixed = (fill_color * t + im * (1.0 - t)).clip(0, 1)
        result_rgb[b] = np.where(edge_mask[:, :, np.newaxis], fixed, im)
        print(f"[边缘颜色修正] 帧{b} 修正={edge_mask.sum()}px  内核={inner.sum()}px")

    out_rgb = torch.from_numpy(result_rgb.astype(np.float32))
    return torch.cat([out_rgb, imgs[..., 3:4]], dim=-1).clamp(0, 1)


def _ring_mask(alpha: torch.Tensor, pixels: int) -> torch.Tensor:
    """返回 alpha 边缘向内 pixels 像素的环形 mask (B,H,W)，float 0/1"""
    B, H, W = alpha.shape
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (pixels*2+1, pixels*2+1))
    alpha_np = (alpha.cpu().numpy() > 0.5).astype(np.uint8)
    rings = []
    for b in range(B):
        outer = alpha_np[b]
        inner = cv2.erode(outer, kernel)
        rings.append(outer - inner)
    return torch.from_numpy(np.stack(rings, axis=0)).float().to(alpha.device)


def _temporal_median_seq(seq, r):
    """对 (B,H,W) 序列在时间轴做滑动窗口中值，窗口=[t-r,t+r]，边界裁剪。"""
    B = seq.shape[0]
    out = torch.empty_like(seq)
    for t in range(B):
        s, e = max(0, t-r), min(B, t+r+1)
        out[t] = seq[s:e].median(dim=0).values
    return out


def _temporal_gauss_seq(seq, r, wb):
    """对 (B,H,W) 序列在时间轴做高斯加权平均，边界处权重重新归一化。"""
    B = seq.shape[0]
    out = torch.empty_like(seq)
    for t in range(B):
        s, e = max(0, t-r), min(B, t+r+1)
        ws = s-(t-r); w = wb[ws:ws+(e-s)]; w = w/w.sum()
        out[t] = (seq[s:e] * w.view(-1,1,1)).sum(0)
    return out


def temporal_smooth(imgs, 窗口大小=5, 平滑强度=1.0, 同步修正RGB=False, 最小Alpha保护=0.05,
                    环形宽度=25, 平滑方式="中值+高斯", 填充闪烁缺口=True, 背景阈值=0.02):
    imgs = imgs.float().clamp(0, 1)
    B, H, W, C = imgs.shape
    if C < 4: return imgs
    r = (窗口大小 - 1) // 2
    coords = torch.arange(-r, r+1, dtype=torch.float32)
    wb = torch.exp(-0.5 * (coords / max(float(平滑强度), 0.1)) ** 2)
    rgb, alpha = imgs[..., :3], imgs[..., 3]
    ro = rgb.clone()

    # ── alpha 时序去闪 ──────────────────────────────────────────────
    if 平滑方式 == "高斯平均":
        ao = _temporal_gauss_seq(alpha, r, wb)
    elif 平滑方式 == "时序中值":
        ao = _temporal_median_seq(alpha, r)
    else:  # 中值+高斯：先中值去脉冲跳变，再高斯抚平残留
        ao = _temporal_gauss_seq(_temporal_median_seq(alpha, r), r, wb)

    # ── 背景清零 / 填充闪烁缺口 ─────────────────────────────────────
    for t in range(B):
        s, e = max(0, t-r), min(B, t+r+1)
        if 填充闪烁缺口:
            # 只在整个时间窗都接近背景时清零；闪烁掉的像素由窗口内其它帧补回
            bg = alpha[s:e].max(dim=0).values <= float(背景阈值)
        else:
            # 旧行为：当前帧透明处强制清零（会保留闪烁）
            bg = alpha[t] <= 0
        ao[t] = torch.where(bg, torch.zeros_like(ao[t]), ao[t])

    # ── 可选 RGB 时序修正（仅静止素材）─────────────────────────────
    if 同步修正RGB:
        ring = _ring_mask(alpha, int(环形宽度)) if 环形宽度 > 0 else None
        for t in range(B):
            s, e = max(0, t-r), min(B, t+r+1)
            ws = s-(t-r); w = wb[ws:ws+(e-s)]; w = w/w.sum()
            a_slice = alpha[s:e]                          # (n,H,W)
            pre = rgb[s:e] * a_slice.unsqueeze(-1)        # 预乘 alpha
            pre_avg = (pre * w.view(-1,1,1,1)).sum(0)
            a_avg = ao[t].unsqueeze(-1).clamp(min=1e-6)
            ra = (pre_avg / a_avg).clamp(0, 1)            # 反预乘
            # 当前帧 alpha >= 0.5 才做 RGB 混合，外边缘半透明像素不参与
            solid = alpha[t] >= 0.5
            if ring is not None:
                apply_mask = (solid & (ring[t] > 0)).unsqueeze(-1)
            else:
                apply_mask = (solid & (alpha[t] >= float(最小Alpha保护))).unsqueeze(-1)
            ro[t] = torch.where(apply_mask, ra, rgb[t])
    return torch.cat([ro, ao.unsqueeze(-1)], dim=-1).clamp(0,1)


def ps_bicubic_sharper(imgs: torch.Tensor, target_w: int, target_h: int) -> torch.Tensor:
    """
    PS Bicubic Sharper：a=-1.0 三次核，分离卷积实现。

    标准 bicubic 用 a=-0.75（Catmull-Rom），PS Bicubic Sharper 用 a=-1.0。
    负叶更深 → 边缘振铃更强 → 主观感知更锐。
    """
    B, H, W, C = imgs.shape
    scale = min(target_w / W, target_h / H)
    nW = max(1, round(W * scale))
    nH = max(1, round(H * scale))

    x = imgs.permute(0, 3, 1, 2).float()   # (B, C, H, W)
    a = -1.0

    def cubic_w(coords: torch.Tensor, src_size: int):
        """coords: (N,) 浮点源坐标 → idx (N,4) 整数, w (N,4) 权重"""
        i0   = coords.floor().long()
        frac = coords - i0.float()
        # 4邻域到采样点的距离：1+f, f, 1-f, 2-f
        dist = torch.stack([1+frac, frac, 1-frac, 2-frac], dim=1)  # (N,4)
        t    = dist.abs()
        w    = torch.zeros_like(t)
        m1   = t <= 1
        m2   = (t > 1) & (t <= 2)
        w[m1] = (a+2)*t[m1]**3 - (a+3)*t[m1]**2 + 1
        w[m2] = a*t[m2]**3 - 5*a*t[m2]**2 + 8*a*t[m2] - 4*a
        # 归一化（防止边界截断后权重不为1）
        w = w / w.sum(dim=1, keepdim=True).clamp(min=1e-6)
        idx = torch.stack([
            (i0 - 1).clamp(0, src_size-1),
            i0      .clamp(0, src_size-1),
            (i0 + 1).clamp(0, src_size-1),
            (i0 + 2).clamp(0, src_size-1),
        ], dim=1)                            # (N,4)
        return idx, w

    dev = x.device

    # ── 抗锯齿预滤波 ─────────────────────────────────────────────────────
    # 缩小时每个输出像素覆盖源图 1/scale 像素，需先低通滤除高频
    if scale < 1.0:
        sigma = 0.5 / scale          # 截止频率对应的高斯 sigma（像素单位）
        r     = max(1, int(sigma * 3))
        ks    = r * 2 + 1
        coords1d = torch.arange(ks, dtype=torch.float32, device=dev) - r
        g1d   = torch.exp(-0.5 * (coords1d / sigma) ** 2)
        g1d   = g1d / g1d.sum()
        g2d   = (g1d.unsqueeze(0) * g1d.unsqueeze(1)).unsqueeze(0).unsqueeze(0)
        g2d   = g2d.expand(C, 1, ks, ks)
        x     = F.conv2d(F.pad(x, [r, r, r, r], mode="reflect"), g2d, groups=C)

    # 输出坐标映射到输入坐标（center-aligned，与 PS 对齐）
    y_src = (torch.arange(nH, dtype=torch.float32, device=dev) + 0.5) * H / nH - 0.5
    x_src = (torch.arange(nW, dtype=torch.float32, device=dev) + 0.5) * W / nW - 0.5

    yi, wy = cubic_w(y_src, H)   # (nH,4)
    xi, wx = cubic_w(x_src, W)   # (nW,4)

    # 水平方向插值：(B,C,H,W) → (B,C,H,nW)
    xg     = x[:, :, :, xi.reshape(-1)].view(B, C, H, nW, 4)
    inter  = (xg * wx.view(1, 1, 1, nW, 4)).sum(-1)          # (B,C,H,nW)

    # 垂直方向插值：(B,C,H,nW) → (B,C,nH,nW)
    inter  = inter.permute(0, 1, 3, 2)                        # (B,C,nW,H)
    yg     = inter[:, :, :, yi.reshape(-1)].view(B, C, nW, nH, 4)
    result = (yg * wy.view(1, 1, 1, nH, 4)).sum(-1)          # (B,C,nW,nH)
    result = result.permute(0, 1, 3, 2).clamp(0, 1)          # (B,C,nH,nW)

    # 画布：RGBA 透明，RGB 白色
    canvas = torch.ones(B, C, target_h, target_w, device=dev, dtype=imgs.dtype)
    if C == 4:
        canvas[:, 3] = 0.0
    top  = (target_h - nH) // 2
    left = (target_w - nW) // 2
    canvas[:, :, top:top+nH, left:left+nW] = result
    return canvas.permute(0, 2, 3, 1).clamp(0, 1)


def _gauss_kernel(radius, device, dtype):
    sigma = max(radius/2.0, 0.5)
    size  = radius*2+1
    c     = torch.arange(size, dtype=torch.float32) - radius
    g     = torch.exp(-0.5*(c/sigma)**2); g = g/g.sum()
    k     = (g.unsqueeze(0)*g.unsqueeze(1)).to(device=device, dtype=dtype)
    return k

def sharpen(imgs, 强度=1.0, 半径=2, 阈值=0.02, 内缩像素=4, 透明区保护=0.05):
    imgs = imgs.float().clamp(0, 1)
    B, H, W, C = imgs.shape
    has_alpha = (C == 4)
    rgb = imgs[..., :3]

    # 确定锐化区域
    if has_alpha and 内缩像素 > 0:
        kernel_e = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (int(内缩像素)*2+1, int(内缩像素)*2+1))
        alpha_np = (imgs[..., 3].cpu().numpy() > 0.5).astype(np.uint8)
        masks = [cv2.erode(alpha_np[b], kernel_e) for b in range(B)]
        sm = torch.from_numpy(np.stack(masks)).float().unsqueeze(-1).to(imgs.device)
    elif has_alpha and 透明区保护 > 0:
        sm = (imgs[..., 3:4] >= float(透明区保护)).float()
    else:
        sm = torch.ones(B, H, W, 1, device=imgs.device)

    k = _gauss_kernel(半径, imgs.device, imgs.dtype)
    ks = k.shape[0]; pad = int(半径)
    k4 = k.unsqueeze(0).unsqueeze(0).expand(3, 1, ks, ks)
    x  = rgb.permute(0, 3, 1, 2)
    bl = F.conv2d(F.pad(x, [pad,pad,pad,pad], mode="reflect"), k4, groups=3)
    diff = x - bl
    shp  = (x + float(强度)*diff*(diff.abs()>float(阈值)).float()).clamp(0, 1)
    sm4  = sm.permute(0, 3, 1, 2)
    out  = (shp*sm4 + x*(1-sm4)).permute(0, 2, 3, 1)
    if has_alpha:
        return torch.cat([out, imgs[..., 3:4]], dim=-1)
    return out


def blur_under_composite(imgs, 半径=10, 强度=5.0):
    """透明图模糊自叠加：同一张图模糊一份垫底，清晰原图叠在上方。

    预乘 alpha 后再模糊，避免透明区 RGB 渗入边缘形成黑边/脏边。
    """
    imgs = imgs.float().clamp(0, 1)
    B, H, W, C = imgs.shape
    radius, sigma = int(半径), float(强度)
    if radius <= 0 or sigma <= 0:
        return imgs
    rgb = imgs[..., :3]
    a = imgs[..., 3:4] if C == 4 else torch.ones(B, H, W, 1, device=imgs.device)

    # 预乘后做可分离高斯模糊（rgb*a 和 a 一起，4 通道）
    premult = torch.cat([rgb * a, a], dim=-1)        # (B,H,W,4)
    ax = torch.arange(-radius, radius + 1, dtype=premult.dtype, device=premult.device)
    k = torch.exp(-(ax * ax) / (2.0 * sigma * sigma)); k = k / k.sum()
    x = premult.permute(0, 3, 1, 2)                  # (B,4,H,W)
    kh = k.view(1, 1, 1, -1).repeat(4, 1, 1, 1)
    x = F.conv2d(F.pad(x, (radius, radius, 0, 0), mode="replicate"), kh, groups=4)
    kv = k.view(1, 1, -1, 1).repeat(4, 1, 1, 1)
    x = F.conv2d(F.pad(x, (0, 0, radius, radius), mode="replicate"), kv, groups=4)
    blur = x.permute(0, 2, 3, 1)

    bb_a = blur[..., 3:4].clamp(0, 1)
    bb_rgb = torch.where(bb_a > 1e-6, blur[..., :3] / bb_a.clamp(min=1e-6),
                         torch.zeros_like(blur[..., :3]))

    # alpha-over：清晰原图盖在模糊底层上
    out_a = a + bb_a * (1.0 - a)
    out_premult = rgb * a + bb_rgb * bb_a * (1.0 - a)
    out_rgb = torch.where(out_a > 1e-6, out_premult / out_a.clamp(min=1e-6),
                          torch.zeros_like(out_premult))
    if C == 4:
        return torch.cat([out_rgb, out_a], dim=-1).clamp(0, 1)
    return out_rgb.clamp(0, 1)


def decode(data):
    arr = np.frombuffer(data, np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_UNCHANGED)
    if img is None: raise ValueError("无法解码")
    if img.ndim == 2: img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    elif img.shape[2] == 3:
        img = np.concatenate([img, np.full((*img.shape[:2],1),255,np.uint8)], axis=-1)
    return cv2.cvtColor(img, cv2.COLOR_BGRA2RGBA)

def encode(arr):
    ok, buf = cv2.imencode(".png", cv2.cvtColor(arr, cv2.COLOR_RGBA2BGRA))
    if not ok: raise RuntimeError("编码失败")
    return buf.tobytes()


# ── HTML ────────────────────────────────────────────────────────────────────
HTML = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Cherry 帧序列工具</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{background:#1a1a2e;color:#e0e0e0;font-family:'Segoe UI',sans-serif;
  min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:32px 16px}
h1{font-size:1.5rem;font-weight:600;margin-bottom:4px;color:#c9a0ff}
.sub{font-size:.85rem;color:#888;margin-bottom:28px}

#dropzone{
  width:100%;max-width:680px;border:2px dashed #5a4080;border-radius:16px;
  padding:40px 24px;text-align:center;cursor:pointer;transition:.2s;background:#16122a}
#dropzone.over{border-color:#c9a0ff;background:#1f1840}
#dropzone.has-files{border-color:#7c5cbf;background:#1a1535}
#drop-icon{font-size:2.6rem;margin-bottom:10px;user-select:none}
#drop-text{font-size:.95rem;color:#aaa}
#drop-count{font-size:.85rem;color:#c9a0ff;margin-top:6px;min-height:1.2em}
#file-input{display:none}
.btn-select{display:inline-block;margin-top:12px;padding:7px 18px;border-radius:8px;
  background:#3d2a6b;color:#ddd;cursor:pointer;font-size:.88rem;transition:.2s}
.btn-select:hover{background:#5a3d99}

/* 模块卡片 */
.module{width:100%;max-width:680px;background:#16122a;border-radius:12px;
  margin:16px 0 0;overflow:hidden;border:1.5px solid #2a2050}
.module.active{border-color:#6a3fb5}
.module-header{display:flex;align-items:center;gap:12px;padding:14px 20px;cursor:pointer;
  user-select:none;transition:.15s}
.module-header:hover{background:#1d1840}
.module-title{font-size:1rem;font-weight:600;flex:1}
.toggle-pill{width:44px;height:24px;border-radius:12px;background:#333;position:relative;
  transition:.2s;flex-shrink:0;cursor:pointer}
.toggle-pill.on{background:#7c3aed}
.toggle-pill::after{content:'';position:absolute;width:18px;height:18px;border-radius:9px;
  background:#fff;top:3px;left:3px;transition:.2s}
.toggle-pill.on::after{left:23px}
.module-body{padding:0 20px 18px;display:none}
.module.active .module-body{display:block}

.param-row{display:flex;align-items:center;gap:12px;margin-top:14px}
.param-row label{width:110px;font-size:.87rem;color:#ccc;flex-shrink:0}
.param-row input[type=range]{flex:1;accent-color:#9b6dff}
.param-row .val{width:48px;font-size:.87rem;color:#c9a0ff;text-align:right}
.param-row input[type=checkbox]{width:17px;height:17px;accent-color:#9b6dff;cursor:pointer}
.param-row .check-label{font-size:.87rem;color:#ccc}
.tip{font-size:.76rem;color:#555;margin-top:3px;padding-left:122px}

#btn-process{
  width:100%;max-width:680px;padding:14px;border:none;border-radius:12px;
  background:linear-gradient(135deg,#7c3aed,#4f46e5);color:#fff;
  font-size:1rem;font-weight:600;cursor:pointer;transition:.2s;margin:20px 0}
#btn-process:hover:not(:disabled){filter:brightness(1.15)}
#btn-process:disabled{opacity:.45;cursor:not-allowed}

#progress-wrap{width:100%;max-width:680px;display:none;margin-bottom:16px}
#progress-bar-bg{background:#2a2050;border-radius:8px;height:10px;overflow:hidden}
#progress-bar{height:100%;width:0%;background:linear-gradient(90deg,#7c3aed,#c9a0ff);transition:.15s}
#progress-text{font-size:.82rem;color:#888;margin-top:6px;text-align:center}

#result{width:100%;max-width:680px;display:none;background:#16122a;border-radius:12px;padding:20px 24px}
#result h2{font-size:.92rem;color:#aaa;margin-bottom:12px}
.stat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px}
.stat-box{background:#1f1840;border-radius:8px;padding:11px 14px}
.stat-box .slabel{font-size:.75rem;color:#888;margin-bottom:4px}
.stat-box .sval{font-size:1.05rem;font-weight:600;color:#c9a0ff}
.stat-box .ssub{font-size:.75rem;color:#7a5ab0;margin-top:2px}
#btn-download{width:100%;padding:12px;border:none;border-radius:10px;
  background:#2a5c2a;color:#9dff9d;font-size:.95rem;font-weight:600;cursor:pointer;transition:.2s}
#btn-download:hover{background:#357535}
#error-msg{color:#ff7070;font-size:.87rem;margin-top:10px;display:none}
</style>
</head>
<body>

<h1>Cherry — 帧序列处理工具</h1>
<p class="sub">拖入文件夹批量后处理 · Z字形流程：去噪 → 模糊自叠加 → 缩小① → 锐化① → 缩小② → 锐化②（每步可单独开关）</p>

<div id="dropzone">
  <div id="drop-icon">🗂️</div>
  <div id="drop-text">拖入帧文件夹，或点击选择文件</div>
  <div id="drop-count"></div>
  <label class="btn-select" for="file-input">选择文件夹 / 多个文件</label>
  <input type="file" id="file-input" multiple accept="image/png,image/webp,image/tiff">
</div>

<!-- ① 去除外部噪点 -->
<div class="module active" id="mod-denoise">
  <div class="module-header" onclick="toggleModule('denoise')">
    <span class="module-title">① 去除外部噪点</span>
    <span style="font-size:.8rem;color:#888;flex:1">清除 alpha 杂点，阈值以下强制透明</span>
    <div class="toggle-pill on" id="pill-denoise"></div>
  </div>
  <div class="module-body">
    <div class="param-row">
      <label>阈值</label>
      <input type="range" id="p-dn-thresh" min="0.0" max="1.0" step="0.01" value="0.06">
      <span class="val" id="v-dn-thresh">0.06</span>
    </div>
    <div class="tip">alpha 低于此值强制为 0；默认 0.06 ≈ 15/255</div>
    <div class="param-row">
      <label>平滑半径</label>
      <input type="range" id="p-dn-radius" min="0" max="20" step="1" value="0">
      <span class="val" id="v-dn-radius">0</span>
    </div>
    <div class="tip">清理后对 alpha 边缘做高斯平滑，0 = 不平滑</div>
  </div>
</div>

<!-- ② 透明图模糊自叠加 -->
<div class="module active" id="mod-blur">
  <div class="module-header" onclick="toggleModule('blur')">
    <span class="module-title">② 透明图模糊自叠加</span>
    <span style="font-size:.8rem;color:#888;flex:1">同图模糊垫底再叠清晰原图，柔光/虚化</span>
    <div class="toggle-pill on" id="pill-blur"></div>
  </div>
  <div class="module-body">
    <div class="param-row">
      <label>模糊半径</label>
      <input type="range" id="p-blur-radius" min="0" max="100" step="1" value="1">
      <span class="val" id="v-blur-radius">1</span>
    </div>
    <div class="tip">底层模糊半径，越大光晕越宽</div>
    <div class="param-row">
      <label>模糊强度</label>
      <input type="range" id="p-blur-sigma" min="0.1" max="50" step="0.1" value="10.0">
      <span class="val" id="v-blur-sigma">10.0</span>
    </div>
    <div class="tip">高斯 sigma，越大越柔越散</div>
  </div>
</div>

<!-- ③ 缩小① -->
<div class="module active" id="mod-resize1">
  <div class="module-header" onclick="toggleModule('resize1')">
    <span class="module-title">③ 缩小①（PS Bicubic Sharper）</span>
    <span style="font-size:.8rem;color:#888;flex:1">等比缩小，透明区填充</span>
    <div class="toggle-pill on" id="pill-resize1"></div>
  </div>
  <div class="module-body">
    <div class="param-row">
      <label>目标宽度</label>
      <input type="number" id="p-rw1" value="768" min="1" max="8192" step="1"
        style="width:90px;background:#1f1840;border:1px solid #4a3580;border-radius:6px;
               color:#c9a0ff;padding:4px 8px;font-size:.9rem">
      <span style="font-size:.85rem;color:#888">px</span>
    </div>
    <div class="param-row" style="margin-top:10px">
      <label>目标高度</label>
      <input type="number" id="p-rh1" value="1024" min="1" max="8192" step="1"
        style="width:90px;background:#1f1840;border:1px solid #4a3580;border-radius:6px;
               color:#c9a0ff;padding:4px 8px;font-size:.9rem">
      <span style="font-size:.85rem;color:#888">px</span>
    </div>
    <div class="tip" style="padding-left:0;margin-top:8px">等比缩放到框内，多余空间透明填充</div>
  </div>
</div>

<!-- ④ 锐化① -->
<div class="module active" id="mod-sharp1">
  <div class="module-header" onclick="toggleModule('sharp1')">
    <span class="module-title">④ 锐化①</span>
    <span style="font-size:.8rem;color:#888;flex:1">USM 反锐化掩模，透明区自动保护</span>
    <div class="toggle-pill on" id="pill-sharp1"></div>
  </div>
  <div class="module-body">
    <div class="param-row">
      <label>强度</label>
      <input type="range" id="p-sharp1-amount" min="0.1" max="5.0" step="0.1" value="1.0">
      <span class="val" id="v-sharp1-amount">1.0</span>
    </div>
    <div class="tip">1.0 = 标准；2.0 = 明显；0.5 = 轻微</div>
    <div class="param-row">
      <label>半径</label>
      <input type="range" id="p-sharp1-radius" min="1" max="8" step="1" value="2">
      <span class="val" id="v-sharp1-radius">2</span>
    </div>
    <div class="tip">模糊半径；发丝细节建议 1~2</div>
    <div class="param-row">
      <label>阈值</label>
      <input type="range" id="p-sharp1-thresh" min="0.0" max="0.3" step="0.005" value="0.02">
      <span class="val" id="v-sharp1-thresh">0.020</span>
    </div>
    <div class="tip">差异低于此值不锐化，保护平坦区域</div>
    <div class="param-row">
      <label>内缩像素</label>
      <input type="range" id="p-sharp1-shrink" min="0" max="50" step="1" value="0">
      <span class="val" id="v-sharp1-shrink">0</span>
    </div>
    <div class="tip">对 alpha 腐蚀 N 像素只锐化实心内部；0 = 改用透明区保护(alpha≥0.05)</div>
  </div>
</div>

<!-- ⑤ 缩小② -->
<div class="module active" id="mod-resize2">
  <div class="module-header" onclick="toggleModule('resize2')">
    <span class="module-title">⑤ 缩小②（PS Bicubic Sharper）</span>
    <span style="font-size:.8rem;color:#888;flex:1">第二段等比缩小，透明区填充</span>
    <div class="toggle-pill on" id="pill-resize2"></div>
  </div>
  <div class="module-body">
    <div class="param-row">
      <label>目标宽度</label>
      <input type="number" id="p-rw2" value="384" min="1" max="8192" step="1"
        style="width:90px;background:#1f1840;border:1px solid #4a3580;border-radius:6px;
               color:#c9a0ff;padding:4px 8px;font-size:.9rem">
      <span style="font-size:.85rem;color:#888">px</span>
    </div>
    <div class="param-row" style="margin-top:10px">
      <label>目标高度</label>
      <input type="number" id="p-rh2" value="512" min="1" max="8192" step="1"
        style="width:90px;background:#1f1840;border:1px solid #4a3580;border-radius:6px;
               color:#c9a0ff;padding:4px 8px;font-size:.9rem">
      <span style="font-size:.85rem;color:#888">px</span>
    </div>
    <div class="tip" style="padding-left:0;margin-top:8px">等比缩放到框内，多余空间透明填充</div>
  </div>
</div>

<!-- ⑥ 锐化② -->
<div class="module active" id="mod-sharp2">
  <div class="module-header" onclick="toggleModule('sharp2')">
    <span class="module-title">⑥ 锐化②</span>
    <span style="font-size:.8rem;color:#888;flex:1">第二段锐化，默认内缩 5px</span>
    <div class="toggle-pill on" id="pill-sharp2"></div>
  </div>
  <div class="module-body">
    <div class="param-row">
      <label>强度</label>
      <input type="range" id="p-sharp2-amount" min="0.1" max="5.0" step="0.1" value="1.0">
      <span class="val" id="v-sharp2-amount">1.0</span>
    </div>
    <div class="tip">1.0 = 标准；2.0 = 明显；0.5 = 轻微</div>
    <div class="param-row">
      <label>半径</label>
      <input type="range" id="p-sharp2-radius" min="1" max="8" step="1" value="2">
      <span class="val" id="v-sharp2-radius">2</span>
    </div>
    <div class="tip">模糊半径；发丝细节建议 1~2</div>
    <div class="param-row">
      <label>阈值</label>
      <input type="range" id="p-sharp2-thresh" min="0.0" max="0.3" step="0.005" value="0.02">
      <span class="val" id="v-sharp2-thresh">0.020</span>
    </div>
    <div class="tip">差异低于此值不锐化，保护平坦区域</div>
    <div class="param-row">
      <label>内缩像素</label>
      <input type="range" id="p-sharp2-shrink" min="0" max="50" step="1" value="5">
      <span class="val" id="v-sharp2-shrink">5</span>
    </div>
    <div class="tip">对 alpha 腐蚀 N 像素，只锐化实心内部，边缘半透明区不参与 → 动画不闪烁</div>
  </div>
</div>

<!-- ⑦ 时序平滑 -->
<div class="module" id="mod-smooth">
  <div class="module-header" onclick="toggleModule('smooth')">
    <span class="module-title">⑦ 时序 Alpha 平滑</span>
    <span style="font-size:.8rem;color:#888;flex:1">消除帧间 alpha 闪烁（动画序列才需要）</span>
    <div class="toggle-pill" id="pill-smooth"></div>
  </div>
  <div class="module-body">
    <div class="param-row">
      <label>平滑方式</label>
      <select id="p-method" style="flex:1;background:#1f1840;border:1px solid #4a3580;color:#ddd;border-radius:6px;padding:4px 8px">
        <option value="中值+高斯" selected>中值+高斯（最稳，推荐去抖动）</option>
        <option value="时序中值">时序中值（最干净，适合稀疏脉冲）</option>
        <option value="高斯平均">高斯平均（旧行为，只变淡）</option>
      </select>
    </div>
    <div class="tip">去逐帧抖动选含"中值"的方式；中值能把个别跳变帧拉回稳定值且不糊发丝</div>
    <div class="param-row">
      <label>窗口大小</label>
      <input type="range" id="p-window" min="3" max="21" step="2" value="7">
      <span class="val" id="v-window">7</span>
    </div>
    <div class="tip">覆盖 ±(窗口-1)/2 帧；抖动明显可调到 9~11</div>
    <div class="param-row">
      <label>平滑强度</label>
      <input type="range" id="p-sigma" min="0.1" max="5.0" step="0.1" value="1.5">
      <span class="val" id="v-sigma">1.5</span>
    </div>
    <div class="tip">高斯 sigma（仅影响含高斯的方式）；值越大邻帧权重越均匀</div>
    <div class="param-row" style="margin-top:10px">
      <input type="checkbox" id="p-fillgap" checked>
      <span class="check-label">填充闪烁缺口（用前后帧补上闪掉的粒子/发丝，关闭=旧行为会保留闪烁）</span>
    </div>
    <div class="param-row">
      <label>背景阈值</label>
      <input type="range" id="p-bgthresh" min="0.0" max="0.30" step="0.01" value="0.02">
      <span class="val" id="v-bgthresh">0.02</span>
    </div>
    <div class="tip">整个时间窗内 alpha 最大值低于此值才判为真背景并清零；越小越能保留细弱发丝</div>
    <div class="param-row">
      <label>最小 Alpha</label>
      <input type="range" id="p-minalpha" min="0.01" max="0.30" step="0.01" value="0.05">
      <span class="val" id="v-minalpha">0.05</span>
    </div>
    <div class="tip">低于此值的像素跳过 RGB 平滑</div>
    <div class="param-row" style="margin-top:14px">
      <input type="checkbox" id="p-syncrgb">
      <span class="check-label">同步平滑 RGB（开启产生动态模糊效果，关闭仅平滑 Alpha）</span>
    </div>
    <div class="param-row" style="margin-top:10px">
      <label>环形宽度</label>
      <input type="range" id="p-ring" min="0" max="60" step="1" value="10">
      <span class="val" id="v-ring">10</span>
    </div>
    <div class="tip">动态模糊只作用在 alpha 边缘向内 N 像素的环形区域，面部/眼睛等内部不受影响。0 = 全区域</div>
  </div>
</div>

<button id="btn-process" disabled>开始处理</button>

<div id="progress-wrap">
  <div id="progress-bar-bg"><div id="progress-bar"></div></div>
  <div id="progress-text">准备中…</div>
</div>

<div id="result">
  <h2>处理结果</h2>
  <div class="stat-grid">
    <div class="stat-box">
      <div class="slabel">处理帧数</div>
      <div class="sval" id="s-frames">—</div>
    </div>
    <div class="stat-box">
      <div class="slabel">Alpha 闪烁降低</div>
      <div class="sval" id="s-reduction">—</div>
      <div class="ssub" id="s-flicker"></div>
    </div>
    <div class="stat-box">
      <div class="slabel">执行步骤</div>
      <div class="sval" id="s-steps">—</div>
    </div>
  </div>
  <button id="btn-download">⬇ 下载处理结果 (ZIP)</button>
  <div id="error-msg"></div>
</div>

<script>
// ── 模块开关 ──────────────────────────────────────────────────────────────
const moduleState = { denoise:true, blur:true, resize1:true, sharp1:true, resize2:true, sharp2:true, smooth:false };

function toggleModule(id) {
  moduleState[id] = !moduleState[id];
  document.getElementById('mod-'+id).classList.toggle('active', moduleState[id]);
  document.getElementById('pill-'+id).classList.toggle('on', moduleState[id]);
  updateBtn();
}

function updateBtn() {
  const hasFiles = collectedFiles.length > 0;
  const hasStep  = Object.values(moduleState).some(v=>v);
  document.getElementById('btn-process').disabled = !(hasFiles && hasStep);
}

// ── 滑块联动 ──────────────────────────────────────────────────────────────
function linkSlider(id, valId, dec=0) {
  const el=document.getElementById(id), vl=document.getElementById(valId);
  el.addEventListener('input',()=>{ vl.textContent=parseFloat(el.value).toFixed(dec); });
}
linkSlider('p-dn-thresh','v-dn-thresh',2);
linkSlider('p-dn-radius','v-dn-radius',0);
linkSlider('p-blur-radius','v-blur-radius',0);
linkSlider('p-blur-sigma','v-blur-sigma',1);
linkSlider('p-sharp1-amount','v-sharp1-amount',1);
linkSlider('p-sharp1-radius','v-sharp1-radius',0);
linkSlider('p-sharp1-thresh','v-sharp1-thresh',3);
linkSlider('p-sharp1-shrink','v-sharp1-shrink',0);
linkSlider('p-sharp2-amount','v-sharp2-amount',1);
linkSlider('p-sharp2-radius','v-sharp2-radius',0);
linkSlider('p-sharp2-thresh','v-sharp2-thresh',3);
linkSlider('p-sharp2-shrink','v-sharp2-shrink',0);
linkSlider('p-window','v-window',0);
linkSlider('p-sigma','v-sigma',1);
linkSlider('p-bgthresh','v-bgthresh',2);
linkSlider('p-minalpha','v-minalpha',2);
linkSlider('p-ring','v-ring',0);

// ── 文件收集 ──────────────────────────────────────────────────────────────
let collectedFiles = [], resultBlob = null, folderName = '';
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('file-input');

// 递归遍历，返回 [{file, relPath}]，保留完整相对路径
async function traverseEntry(entry, parentPath='') {
  const curPath = parentPath ? `${parentPath}/${entry.name}` : entry.name;
  if (entry.isFile) return new Promise(r=>entry.file(f=>r([{file:f, relPath:curPath}])));
  if (entry.isDirectory) {
    const reader = entry.createReader(), all = [];
    await new Promise(r=>{ function read(){ reader.readEntries(res=>{ if(!res.length){r();return;} all.push(...res);read(); }); } read(); });
    return (await Promise.all(all.map(e=>traverseEntry(e, curPath)))).flat();
  }
  return [];
}

function setFiles(items, name='') {
  folderName = name;
  collectedFiles = items
    .filter(({file})=>/\.(png|webp|tif|tiff)$/i.test(file.name))
    .sort((a,b)=>a.relPath.localeCompare(b.relPath, undefined, {numeric:true}));
  const dirs = new Set(collectedFiles.map(({relPath})=>{
    const parts = relPath.split('/'); return parts.length>1 ? parts.slice(0,-1).join('/') : '';
  }));
  const dirCount = [...dirs].filter(Boolean).length;
  document.getElementById('drop-count').textContent =
    collectedFiles.length
      ? `已识别 ${collectedFiles.length} 张图片${dirCount>1?' / '+dirCount+' 个子文件夹':name?' ('+name+')':''}`
      : '未找到图片，请重新拖入';
  dropzone.classList.toggle('has-files', collectedFiles.length>0);
  document.getElementById('result').style.display='none';
  resultBlob=null;
  updateBtn();
}

dropzone.addEventListener('dragover', e=>{e.preventDefault();dropzone.classList.add('over');});
dropzone.addEventListener('dragleave', ()=>dropzone.classList.remove('over'));
dropzone.addEventListener('drop', async e=>{
  e.preventDefault(); dropzone.classList.remove('over');
  const items = [...e.dataTransfer.items];
  let name = '';
  for(const item of items){
    const entry = item.webkitGetAsEntry();
    if(entry && entry.isDirectory){ name = entry.name; break; }
  }
  const all = (await Promise.all(items.map(i=>traverseEntry(i.webkitGetAsEntry())))).flat();
  setFiles(all, name);
});
fileInput.addEventListener('change', ()=>{
  const files = [...fileInput.files];
  let name = '';
  if(files.length && files[0].webkitRelativePath)
    name = files[0].webkitRelativePath.split('/')[0];
  const items = files.map(f=>({
    file: f,
    relPath: f.webkitRelativePath || f.name
  }));
  setFiles(items, name);
});
dropzone.addEventListener('click', e=>{ if(e.target.closest('.btn-select')||e.target.tagName==='LABEL') return; fileInput.click(); });

// ── 处理 ──────────────────────────────────────────────────────────────────
document.getElementById('btn-process').addEventListener('click', async ()=>{
  if(!collectedFiles.length) return;
  const btnProc = document.getElementById('btn-process');
  const progWrap = document.getElementById('progress-wrap');
  const progBar  = document.getElementById('progress-bar');
  const progText = document.getElementById('progress-text');
  const errMsg   = document.getElementById('error-msg');

  btnProc.disabled=true;
  progWrap.style.display='block';
  document.getElementById('result').style.display='none';
  errMsg.style.display='none';
  progBar.style.width='0%';
  progText.textContent=`准备上传 ${collectedFiles.length} 张…`;

  const fd = new FormData();
  const V = id => document.getElementById(id).value;
  fd.append('folder_name',  folderName);
  // ① 去噪
  fd.append('use_denoise',  moduleState.denoise?'1':'0');
  fd.append('dn_thresh',    V('p-dn-thresh'));
  fd.append('dn_radius',    V('p-dn-radius'));
  // ② 模糊自叠加
  fd.append('use_blur',     moduleState.blur?'1':'0');
  fd.append('blur_radius',  V('p-blur-radius'));
  fd.append('blur_sigma',   V('p-blur-sigma'));
  // ③ 缩小①
  fd.append('use_resize1',  moduleState.resize1?'1':'0');
  fd.append('resize1_w',    V('p-rw1'));
  fd.append('resize1_h',    V('p-rh1'));
  // ④ 锐化①
  fd.append('use_sharp1',   moduleState.sharp1?'1':'0');
  fd.append('sharp1_amount',V('p-sharp1-amount'));
  fd.append('sharp1_radius',V('p-sharp1-radius'));
  fd.append('sharp1_thresh',V('p-sharp1-thresh'));
  fd.append('sharp1_shrink',V('p-sharp1-shrink'));
  // ⑤ 缩小②
  fd.append('use_resize2',  moduleState.resize2?'1':'0');
  fd.append('resize2_w',    V('p-rw2'));
  fd.append('resize2_h',    V('p-rh2'));
  // ⑥ 锐化②
  fd.append('use_sharp2',   moduleState.sharp2?'1':'0');
  fd.append('sharp2_amount',V('p-sharp2-amount'));
  fd.append('sharp2_radius',V('p-sharp2-radius'));
  fd.append('sharp2_thresh',V('p-sharp2-thresh'));
  fd.append('sharp2_shrink',V('p-sharp2-shrink'));
  // ⑦ 时序平滑
  fd.append('use_smooth',   moduleState.smooth?'1':'0');
  fd.append('window_size',  V('p-window'));
  fd.append('sigma',        V('p-sigma'));
  fd.append('smooth_method',V('p-method'));
  fd.append('fill_gap',     document.getElementById('p-fillgap').checked?'1':'0');
  fd.append('bg_thresh',    V('p-bgthresh'));
  fd.append('min_alpha',    V('p-minalpha'));
  fd.append('sync_rgb',     document.getElementById('p-syncrgb').checked?'1':'0');
  fd.append('ring_width',   V('p-ring'));

  let up=0;
  for(const {file, relPath} of collectedFiles){
    fd.append('images', file, relPath); up++;
    if(up%5===0||up===collectedFiles.length){
      progBar.style.width=Math.round(up/collectedFiles.length*30)+'%';
      progText.textContent=`上传中 ${up}/${collectedFiles.length}…`;
      await new Promise(r=>setTimeout(r,0));
    }
  }
  progBar.style.width='35%'; progText.textContent='服务器处理中…';

  try {
    const resp = await fetch('/process',{method:'POST',body:fd});
    if(!resp.ok){ const j=await resp.json().catch(()=>({})); throw new Error(j.error||resp.statusText); }
    progBar.style.width='90%'; progText.textContent='下载中…';
    const blob = await resp.blob();
    const stats = JSON.parse(resp.headers.get('X-Stats')||'{}');
    progBar.style.width='100%'; progText.textContent='完成！';
    resultBlob=blob;

    document.getElementById('s-frames').textContent = collectedFiles.length+' 帧';
    document.getElementById('s-reduction').textContent =
      stats.reduction_pct!=null ? stats.reduction_pct.toFixed(1)+'%' : '—';
    document.getElementById('s-flicker').textContent =
      stats.flicker_before!=null ? `${stats.flicker_before.toFixed(5)} → ${stats.flicker_after.toFixed(5)}` : '';
    document.getElementById('s-steps').textContent = stats.steps || '—';
    document.getElementById('result').style.display='block';
  } catch(e){
    progText.textContent='处理失败';
    errMsg.textContent='错误：'+e.message; errMsg.style.display='block';
  } finally { btnProc.disabled=false; updateBtn(); }
});

document.getElementById('btn-download').addEventListener('click',()=>{
  if(!resultBlob) return;
  const url=URL.createObjectURL(resultBlob);
  const a=document.createElement('a'); a.href=url; a.download='cherry_processed.zip'; a.click();
  setTimeout(()=>URL.revokeObjectURL(url),5000);
});
</script>
</body>
</html>
"""


# ── 路由 ────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template_string(HTML)


def run_pipeline(form, files):
    """核心后处理流水线。

    form  : dict[str,str]   表单参数（缺省用默认值）
    files : list[(filename, bytes)]  上传的图片
    返回   : (zip_bytes: bytes, stats: dict)  stats 含 folder_name/steps 等
    """
    def g(k, d=""):
        v = form.get(k)
        return d if v is None else v

    folder_name = str(g("folder_name", "")).strip()
    if not folder_name:
        first = files[0][0] if files else ""
        parts = first.replace("\\", "/").split("/")
        folder_name = parts[0] if len(parts) > 1 else (os.path.splitext(parts[0])[0] if parts[0] else "cherry_output")

    # ① 去除外部噪点
    use_denoise  = g("use_denoise", "1") == "1"
    dn_thresh    = float(g("dn_thresh", 0.06))
    dn_radius    = int(g("dn_radius", 0))
    # ② 透明图模糊自叠加
    use_blur     = g("use_blur", "1") == "1"
    blur_radius  = int(g("blur_radius", 1))
    blur_sigma   = float(g("blur_sigma", 10.0))
    # ③ 缩小①
    use_resize1  = g("use_resize1", "1") == "1"
    resize1_w    = int(g("resize1_w", 768))
    resize1_h    = int(g("resize1_h", 1024))
    # ④ 锐化①
    use_sharp1   = g("use_sharp1", "1") == "1"
    sharp1_amount= float(g("sharp1_amount", 1.0))
    sharp1_radius= int(g("sharp1_radius", 2))
    sharp1_thresh= float(g("sharp1_thresh", 0.02))
    sharp1_shrink= int(g("sharp1_shrink", 0))
    # ⑤ 缩小②
    use_resize2  = g("use_resize2", "1") == "1"
    resize2_w    = int(g("resize2_w", 384))
    resize2_h    = int(g("resize2_h", 512))
    # ⑥ 锐化②
    use_sharp2   = g("use_sharp2", "1") == "1"
    sharp2_amount= float(g("sharp2_amount", 1.0))
    sharp2_radius= int(g("sharp2_radius", 2))
    sharp2_thresh= float(g("sharp2_thresh", 0.02))
    sharp2_shrink= int(g("sharp2_shrink", 5))
    # ⑦ 时序 Alpha 平滑（默认关闭）
    use_smooth   = g("use_smooth", "0") == "1"
    window_size  = int(g("window_size", 5))
    sigma        = float(g("sigma", 1.0))
    min_alpha    = float(g("min_alpha", 0.05))
    sync_rgb     = g("sync_rgb", "0") == "1"
    ring_width   = int(g("ring_width", 25))
    smooth_method = g("smooth_method", "中值+高斯")
    fill_gap     = g("fill_gap", "1") == "1"
    bg_thresh    = float(g("bg_thresh", 0.02))

    if not files:
        raise ValueError("没有收到图片")

    # 按目录分组，每个目录单独作为一批
    from collections import defaultdict
    groups = defaultdict(list)
    for fname, data in files:
        rel = fname.replace("\\", "/")
        dir_part  = os.path.dirname(rel)
        base_name = os.path.basename(rel)
        groups[dir_part].append((base_name, data))

    def mean_flicker(t):
        if t.shape[0] < 2: return 0.0
        return float((t[..., 3][1:] - t[..., 3][:-1]).abs().mean())

    flicker_before_all, flicker_after_all = [], []
    zip_buf = io.BytesIO()

    with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        for dir_part, file_list in sorted(groups.items()):
            file_list.sort(key=lambda x: x[0])
            frames = [decode(data) for _, data in file_list]
            batch = torch.from_numpy(np.stack(frames, axis=0).astype(np.float32) / 255.0)

            flicker_before_all.append(mean_flicker(batch))
            print(f"[pipeline] Z字形: denoise={use_denoise} blur={use_blur} "
                  f"resize1={use_resize1} sharp1={use_sharp1} "
                  f"resize2={use_resize2} sharp2={use_sharp2} smooth={use_smooth}", flush=True)

            if use_denoise:
                batch = alpha_denoise(batch, dn_thresh, dn_radius)
            if use_blur:
                batch = blur_under_composite(batch, blur_radius, blur_sigma)
            if use_resize1:
                batch = ps_bicubic_sharper(batch, resize1_w, resize1_h)
            if use_sharp1:
                batch = sharpen(batch, sharp1_amount, sharp1_radius, sharp1_thresh, sharp1_shrink)
            if use_resize2:
                batch = ps_bicubic_sharper(batch, resize2_w, resize2_h)
            if use_sharp2:
                batch = sharpen(batch, sharp2_amount, sharp2_radius, sharp2_thresh, sharp2_shrink)
            if use_smooth:
                batch = temporal_smooth(batch, window_size, sigma, sync_rgb, min_alpha, ring_width,
                                        smooth_method, fill_gap, bg_thresh)

            flicker_after_all.append(mean_flicker(batch))

            result_u8 = (batch.numpy() * 255).clip(0, 255).astype(np.uint8)
            for i, (name, _) in enumerate(file_list):
                base = os.path.splitext(name)[0]
                out_path = f"{dir_part}/{base}.png" if dir_part else f"{folder_name}/{base}.png"
                zf.writestr(out_path, encode(result_u8[i]))

    flicker_before = float(np.mean(flicker_before_all)) if flicker_before_all else 0.0
    flicker_after  = float(np.mean(flicker_after_all))  if flicker_after_all  else 0.0
    reduction_pct  = (1 - flicker_after / max(flicker_before, 1e-9)) * 100
    steps = (["去噪"]*use_denoise + ["模糊自叠加"]*use_blur
             + [f"缩小{resize1_w}x{resize1_h}"]*use_resize1 + ["锐化①"]*use_sharp1
             + [f"缩小{resize2_w}x{resize2_h}"]*use_resize2 + ["锐化②"]*use_sharp2
             + ["时序平滑"]*use_smooth)
    stats = {
        "folder_name":    folder_name,
        "flicker_before": flicker_before,
        "flicker_after":  flicker_after,
        "reduction_pct":  reduction_pct,
        "steps": " → ".join(steps) if steps else "无",
    }
    return zip_buf.getvalue(), stats


@app.route("/process", methods=["POST"])
def process():
    try:
        form = {k: request.form.get(k) for k in request.form.keys()}
        files = [(f.filename, f.read())
                 for f in sorted(request.files.getlist("images"), key=lambda f: f.filename)]
        zip_bytes, stats = run_pipeline(form, files)
        resp = send_file(io.BytesIO(zip_bytes), mimetype="application/zip",
                         as_attachment=True, download_name=f"{stats['folder_name']}.zip")
        resp.headers["X-Stats"] = json.dumps(stats)
        resp.headers["Access-Control-Expose-Headers"] = "X-Stats"
        return resp
    except Exception:
        traceback.print_exc()
        return jsonify(error=traceback.format_exc().splitlines()[-1]), 500


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print(f"\n Cherry 帧序列处理工具")
    print(f" 浏览器打开: http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
