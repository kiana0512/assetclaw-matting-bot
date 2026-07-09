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


def _shadow_softedge_v14_reserved(shadow_a, body_mask, alpha下限):
    """保留版 shadow-ellipse-v14-softedge：暂不挂前端，后面可回收。"""
    base_shadow = shadow_a.copy()
    edge_core = (base_shadow > max(0.018, float(alpha下限) * 1.15)).astype(np.uint8)
    if edge_core.any():
        clean_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        rounded = cv2.morphologyEx(edge_core, cv2.MORPH_CLOSE, clean_kernel)
        rounded = cv2.morphologyEx(
            rounded, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        )
        rounded_f = cv2.GaussianBlur(rounded.astype(np.float32), (0, 0), sigmaX=2.0, sigmaY=2.0)
        rounded = (rounded_f > 0.22).astype(np.uint8)
        jump_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
        free_area = (body_mask == 0).astype(np.uint8)
        support = cv2.dilate(rounded, jump_kernel)
        support = (support & free_area).astype(np.uint8)
        inner = cv2.erode(rounded, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        inner = (inner & free_area).astype(np.float32)
        dist_in = cv2.distanceTransform(support.astype(np.uint8), cv2.DIST_L2, 5)
        edge_px = 3.0
        fade = np.clip(dist_in / edge_px, 0.0, 1.0)
        fade = fade * fade * (3.0 - 2.0 * fade)
        smooth_a = cv2.GaussianBlur(base_shadow, (15, 15), sigmaX=2.2, sigmaY=2.2)
        soft_edge = smooth_a * fade * support.astype(np.float32)
        shadow_a = base_shadow * inner + soft_edge * (1.0 - inner)
    return np.clip(shadow_a, 0.0, 1.0)


def _shadow_softedge_v16_slowfade(shadow_a, body_mask, alpha下限):
    """当前版：外缘跳出 alpha 后，用更宽的高斯渐隐慢慢淡出。"""
    base_shadow = shadow_a.copy()
    edge_core = (base_shadow > max(0.018, float(alpha下限) * 1.15)).astype(np.uint8)
    if edge_core.any():
        clean_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
        rounded = cv2.morphologyEx(edge_core, cv2.MORPH_CLOSE, clean_kernel)
        rounded = cv2.morphologyEx(
            rounded, cv2.MORPH_OPEN,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        )
        rounded_f = cv2.GaussianBlur(rounded.astype(np.float32), (0, 0), sigmaX=2.0, sigmaY=2.0)
        rounded = (rounded_f > 0.22).astype(np.uint8)
        jump_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (15, 15))
        free_area = (body_mask == 0).astype(np.uint8)
        support = cv2.dilate(rounded, jump_kernel)
        support = (support & free_area).astype(np.uint8)
        inner = cv2.erode(rounded, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5)))
        inner = (inner & free_area).astype(np.float32)
        dist_in = cv2.distanceTransform(support.astype(np.uint8), cv2.DIST_L2, 5)
        edge_px = 7.0
        fade = np.clip(dist_in / edge_px, 0.0, 1.0)
        fade = fade * fade * (3.0 - 2.0 * fade)
        smooth_a = cv2.GaussianBlur(base_shadow, (25, 25), sigmaX=4.0, sigmaY=4.0)
        soft_edge = smooth_a * fade * support.astype(np.float32)
        shadow_a = base_shadow * inner + soft_edge * (1.0 - inner)
    return np.clip(shadow_a, 0.0, 1.0)


def _shadow_softedge_v19_outerfade(shadow_a, alpha下限):
    """只处理阴影整片的外轮廓；人物/鞋子扣出的内圈边缘不参与羽化。"""
    base_shadow = shadow_a.copy()
    edge_core = (base_shadow > max(0.018, float(alpha下限) * 1.15)).astype(np.uint8)
    if not edge_core.any():
        return np.clip(shadow_a, 0.0, 1.0)

    clean_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    rounded = cv2.morphologyEx(edge_core, cv2.MORPH_CLOSE, clean_kernel)
    rounded = cv2.morphologyEx(
        rounded, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    )
    rounded_f = cv2.GaussianBlur(rounded.astype(np.float32), (0, 0), sigmaX=2.0, sigmaY=2.0)
    rounded = (rounded_f > 0.22).astype(np.uint8)

    contours, _ = cv2.findContours(rounded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    outer_fill = np.zeros_like(rounded)
    for cnt in contours:
        if cv2.contourArea(cnt) >= 8:
            cv2.drawContours(outer_fill, [cnt], -1, 1, -1)
    if not outer_fill.any():
        return np.clip(shadow_a, 0.0, 1.0)

    outer_px = 8.0
    inner_px = 3.0
    outer_k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (17, 17))
    outer_support = cv2.dilate(outer_fill, outer_k)
    dist_in = cv2.distanceTransform(outer_fill.astype(np.uint8), cv2.DIST_L2, 5)
    dist_out = cv2.distanceTransform((outer_fill == 0).astype(np.uint8), cv2.DIST_L2, 5)

    smooth_a = cv2.GaussianBlur(base_shadow, (29, 29), sigmaX=4.6, sigmaY=4.6)
    inside_band = ((outer_fill > 0) & (dist_in <= inner_px)).astype(np.float32)
    outside_band = ((outer_support > 0) & (outer_fill == 0) & (dist_out <= outer_px)).astype(np.float32)

    inside_t = np.clip(1.0 - dist_in / inner_px, 0.0, 1.0)
    inside_t = inside_t * inside_t * (3.0 - 2.0 * inside_t)
    outside_t = np.clip(1.0 - dist_out / outer_px, 0.0, 1.0)
    outside_t = outside_t * outside_t * (3.0 - 2.0 * outside_t)

    shadow_a = base_shadow.copy()
    shadow_a = shadow_a * (1.0 - inside_band * inside_t) + smooth_a * (inside_band * inside_t)
    shadow_a = np.where(outside_band > 0, np.maximum(shadow_a, smooth_a * outside_t), shadow_a)
    return np.clip(shadow_a, 0.0, 1.0)


def _shadow_softedge_v21_keep_range(shadow_a, alpha下限):
    """只在原始阴影范围内平滑外轮廓，不外扩、不重画阴影范围。"""
    base_shadow = shadow_a.copy()
    original_range = base_shadow > 1e-7
    edge_core = (base_shadow > max(0.012, float(alpha下限) * 0.75)).astype(np.uint8)
    if not edge_core.any():
        return np.clip(shadow_a, 0.0, 1.0)

    clean_kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7, 7))
    rounded = cv2.morphologyEx(edge_core, cv2.MORPH_CLOSE, clean_kernel)
    rounded = cv2.morphologyEx(
        rounded, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    )
    contours, _ = cv2.findContours(rounded, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    outer_fill = np.zeros_like(rounded)
    for cnt in contours:
        if cv2.contourArea(cnt) >= 8:
            cv2.drawContours(outer_fill, [cnt], -1, 1, -1)
    if not outer_fill.any():
        return np.clip(shadow_a, 0.0, 1.0)

    edge_px = 6.0
    dist_in = cv2.distanceTransform(outer_fill.astype(np.uint8), cv2.DIST_L2, 5)
    edge_band = ((outer_fill > 0) & (dist_in <= edge_px) & original_range).astype(np.float32)
    t = np.clip(1.0 - dist_in / edge_px, 0.0, 1.0)
    t = t * t * (3.0 - 2.0 * t)
    smooth_a = cv2.GaussianBlur(base_shadow, (21, 21), sigmaX=3.2, sigmaY=3.2)
    shadow_a = base_shadow * (1.0 - edge_band * t) + smooth_a * (edge_band * t)
    shadow_a = np.where(original_range, shadow_a, 0.0)
    return np.clip(shadow_a, 0.0, 1.0)


def shadow_separate(imgs, 灰度上限=0.35, 保护半径=0, alpha下限=0.02, 阴影增强=1.0,
                    阴影模糊半径=2, 阴影模糊强度=2.4, item_alpha=None,
                    item_rgb=None):
    """阴影分离：把 RGBA 图拆成「人物（去阴影）」和「阴影层」。

    核心策略：
    1) 输入先用 0.01 去噪，得到人物脚下粘连的半透明阴影轮廓；
    2) 根据脚底附近的横向候选块找到原图阴影连通域；
    3) 直接沿用原图阴影 alpha 范围；
    4) 删除竖向、窄高、类似包裹人物腿部的残留。
    """
    imgs = imgs.float().clamp(0, 1)
    B, H, W, C = imgs.shape
    if C < 4:
        return imgs, torch.zeros_like(imgs)
    s_hi   = float(灰度上限)
    a_lo   = float(alpha下限)
    boost  = float(阴影增强)
    pr     = int(保护半径)
    blur_r = int(阴影模糊半径)
    blur_s = float(阴影模糊强度)

    rgb = imgs[..., :3].cpu().numpy()
    A   = imgs[..., 3].cpu().numpy()
    IA  = item_alpha.detach().cpu().numpy() if item_alpha is not None else None
    IR  = item_rgb.detach().cpu().numpy() if item_rgb is not None else None
    char_out   = imgs.cpu().numpy().copy()
    shadow_out = imgs.cpu().numpy().copy()

    close_k = np.ones((7, 7), np.uint8)
    open_k  = np.ones((3, 3), np.uint8)
    rim_k   = np.ones((pr * 2 + 1, pr * 2 + 1), np.uint8) if pr > 0 else None

    for b in range(B):
        a  = A[b]
        ia = IA[b] if IA is not None else None
        ir = IR[b] if IR is not None else None
        im = rgb[b]
        mx = im.max(-1); mn = im.min(-1)
        S  = np.where(mx > 1e-6, (mx - mn) / np.clip(mx, 1e-6, 1), 0.0)

        # 用纯人物 alpha 定位脚和身体；没有传入时退回到合成图 alpha。
        solid_src = ia if ia is not None else a
        solid = (solid_src > max(0.18, a_lo * 2.0)).astype(np.uint8)
        if solid.sum() == 0:
            # 整帧无实心人物：没有可参照的轮廓，不拆，阴影层留空
            char_out[b, ..., 3]   = a
            shadow_out[b, ..., 3] = 0.0
            continue
        solid = cv2.morphologyEx(solid, cv2.MORPH_CLOSE, close_k)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(solid, 8)
        if n > 1:
            solid = (lab == (1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA])))).astype(np.uint8)
        # 填补人物内部的洞（白袜、领子等近透明内部区）
        inv = ((solid == 0).astype(np.uint8)) * 255
        flood = inv.copy()
        cv2.floodFill(flood, np.zeros((H + 2, W + 2), np.uint8), (0, 0), 0)
        solid = ((solid > 0) | (flood > 0)).astype(np.uint8)
        rim = cv2.dilate(solid, rim_k) if rim_k is not None else solid

        ys, xs = np.where(solid > 0)
        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())
        char_h = max(1, y1 - y0 + 1)
        char_w = max(1, x1 - x0 + 1)

        body_k = abs(pr)
        body_kernel = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (body_k * 2 + 1, body_k * 2 + 1)
        ) if body_k > 0 else None
        if pr < 0 and body_kernel is not None:
            body_mask = cv2.erode(solid, body_kernel)
        elif pr > 0 and body_kernel is not None:
            body_mask = cv2.dilate(solid, body_kernel)
        else:
            body_mask = solid.copy()
        extract_body_mask = body_mask
        if pr == 0:
            gap_px = max(1, min(2, int(round(char_h * 0.004))))
            gap_k = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (gap_px * 2 + 1, gap_px * 2 + 1)
            )
            extract_body_mask = cv2.erode(body_mask, gap_k)
        yy = np.arange(H, dtype=np.float32)[:, None]
        lower_mask = yy >= float(y1 - max(12, int(0.10 * char_h)))
        color_ok = (S < s_hi) | (a < 0.65)
        raw_shadow = np.clip(a * (1.0 - extract_body_mask.astype(np.float32)), 0.0, 1.0)
        cand = ((raw_shadow > a_lo) & lower_mask & color_ok).astype(np.uint8)

        n2, lab2, stats2, _ = cv2.connectedComponentsWithStats(cand, 8)
        keep = np.zeros_like(cand)
        min_area = max(10, int(H * W * 0.000015))
        for k in range(1, n2):
            area = int(stats2[k, cv2.CC_STAT_AREA])
            if area < min_area:
                continue
            x, y, w, h = [int(stats2[k, idx]) for idx in (
                cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP, cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT)]
            # 删掉竖着包裹人物的窄高残留。
            if h > max(10, w * 1.7) and y < y1 + 4:
                continue
            # 保留脚下横向候选，后面只作为“原图阴影连通域”的种子。
            if y <= y1 + max(18, int(0.08 * char_h)) and w >= max(5, h):
                keep[lab2 == k] = 1

        support_y_mask = yy >= float(y1 - max(48, int(0.24 * char_h)))
        raw_support = ((raw_shadow > max(0.003, a_lo * 0.05)) & support_y_mask & color_ok).astype(np.uint8)
        shadow_region = np.zeros_like(raw_support)
        if raw_support.any():
            n3, lab3, stats3, _ = cv2.connectedComponentsWithStats(raw_support, 8)
            seed_labels = [int(x) for x in np.unique(lab3[keep > 0]) if int(x) > 0] if keep.any() else []
            if seed_labels:
                for lab_id in seed_labels:
                    shadow_region[lab3 == lab_id] = 1
            if not shadow_region.any():
                # 没找到强种子时，退回到脚底附近的原始阴影连通块，仍不画新形状。
                for lab_id in range(1, n3):
                    area = int(stats3[lab_id, cv2.CC_STAT_AREA])
                    if area < min_area:
                        continue
                    x, y, w, h = [int(stats3[lab_id, idx]) for idx in (
                        cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP,
                        cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT)]
                    if y <= y1 + max(18, int(0.08 * char_h)) and w >= max(5, h):
                        shadow_region[lab3 == lab_id] = 1

        shadow_a = raw_shadow * shadow_region.astype(np.float32)
        row_counts = solid.sum(axis=1)
        lower_start = max(y0, y1 - max(36, int(0.24 * char_h)))
        lower_counts = row_counts[lower_start:y1 + 1]
        foot_width = int(lower_counts.max()) if lower_counts.size else int(row_counts[y1])
        leg_clean_top = y1 - max(64, int(0.18 * char_h))
        if foot_width > 0:
            narrow_limit = max(4, int(foot_width * 0.56))
            for yy_idx in range(y1, lower_start - 1, -1):
                if row_counts[yy_idx] <= narrow_limit:
                    leg_clean_top = yy_idx - max(8, int(0.025 * char_h))
                    break
        leg_clean_top = max(y0, min(y1, int(leg_clean_top)))
        near_px = max(14, min(36, int(round(char_h * 0.06))))
        near_body = cv2.dilate(
            solid,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (near_px * 2 + 1, near_px * 2 + 1))
        )
        leg_cleanup = (near_body > 0) & (yy < float(leg_clean_top)) & (shadow_a < 0.28)
        shadow_a = np.where(leg_cleanup, 0.0, shadow_a)
        shadow_a = np.clip(shadow_a * boost, 0.0, 1.0)
        shadow_a = _shadow_softedge_v21_keep_range(shadow_a, a_lo)
        shadow_a = np.clip(shadow_a, 0.0, 1.0)
        char_out[b, ..., 3] = np.clip(a - shadow_a, 0.0, 1.0)
        shadow_out[b, ..., :3] = 0.0
        shadow_out[b, ..., 3] = shadow_a

    return torch.from_numpy(char_out), torch.from_numpy(shadow_out)


def shadow_separate_v5(imgs, 灰度上限=0.35, 保护半径=0, alpha下限=0.02, 阴影增强=1.0,
                       阴影模糊半径=2, 阴影模糊强度=2.4, item_alpha=None,
                       item_rgb=None):
    """shadow-ellipse-v5-groundgate：用地面门控限制阴影高度，避免裤腿残影被羽化放大。"""
    imgs = imgs.float().clamp(0, 1)
    B, H, W, C = imgs.shape
    if C < 4:
        return imgs, torch.zeros_like(imgs)

    s_hi = float(灰度上限)
    a_lo = float(alpha下限)
    boost = float(阴影增强)
    pr = int(保护半径)
    blur_r = int(阴影模糊半径)
    blur_s = float(阴影模糊强度)

    rgb = imgs[..., :3].cpu().numpy()
    A = imgs[..., 3].cpu().numpy()
    IA = item_alpha.detach().cpu().numpy() if item_alpha is not None else None
    char_out = imgs.cpu().numpy().copy()
    shadow_out = imgs.cpu().numpy().copy()

    close_k = np.ones((7, 7), np.uint8)

    for b in range(B):
        a = A[b]
        ia = IA[b] if IA is not None else None
        im = rgb[b]
        mx = im.max(-1)
        mn = im.min(-1)
        S = np.where(mx > 1e-6, (mx - mn) / np.clip(mx, 1e-6, 1), 0.0)

        solid_src = ia if ia is not None else a
        solid = (solid_src > max(0.18, a_lo * 2.0)).astype(np.uint8)
        if solid.sum() == 0:
            char_out[b, ..., 3] = a
            shadow_out[b, ..., :3] = 0.0
            shadow_out[b, ..., 3] = 0.0
            continue

        solid = cv2.morphologyEx(solid, cv2.MORPH_CLOSE, close_k)
        n, lab, stats, _ = cv2.connectedComponentsWithStats(solid, 8)
        if n > 1:
            solid = (lab == (1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA])))).astype(np.uint8)

        inv = ((solid == 0).astype(np.uint8)) * 255
        flood = inv.copy()
        cv2.floodFill(flood, np.zeros((H + 2, W + 2), np.uint8), (0, 0), 0)
        solid = ((solid > 0) | (flood > 0)).astype(np.uint8)

        ys, xs = np.where(solid > 0)
        y0, y1 = int(ys.min()), int(ys.max())
        x0, x1 = int(xs.min()), int(xs.max())
        char_h = max(1, y1 - y0 + 1)
        char_w = max(1, x1 - x0 + 1)

        body_k = max(1, abs(pr), int(0.02 * char_h))
        body_kernel = np.ones((body_k * 2 + 1, body_k * 2 + 1), np.uint8)
        if pr < 0:
            body_mask = cv2.erode(solid, body_kernel)
        else:
            body_mask = cv2.dilate(solid, body_kernel)

        yy = np.arange(H, dtype=np.float32)[:, None]
        lower_mask = yy >= float(y1 - max(12, int(0.10 * char_h)))
        color_ok = (S < s_hi) | (a < 0.65)
        raw_shadow = np.clip(a * (1.0 - body_mask.astype(np.float32)), 0.0, 1.0)
        cand = ((raw_shadow > a_lo) & lower_mask & color_ok).astype(np.uint8)

        n2, lab2, stats2, _ = cv2.connectedComponentsWithStats(cand, 8)
        keep = np.zeros_like(cand)
        min_area = max(10, int(H * W * 0.000015))
        for k in range(1, n2):
            area = int(stats2[k, cv2.CC_STAT_AREA])
            if area < min_area:
                continue
            x, y, w, h = [int(stats2[k, idx]) for idx in (
                cv2.CC_STAT_LEFT, cv2.CC_STAT_TOP,
                cv2.CC_STAT_WIDTH, cv2.CC_STAT_HEIGHT
            )]
            if h > max(10, w * 1.7) and y < y1 + 4:
                continue
            if y <= y1 + max(18, int(0.08 * char_h)) and w >= max(5, h):
                keep[lab2 == k] = 1

        if keep.any():
            ky, kx = np.where(keep > 0)
            ex0, ex1 = int(kx.min()), int(kx.max())
            ey0, ey1 = int(ky.min()), int(ky.max())
            cx = int(round((ex0 + ex1) / 2))
            cy = int(round((ey0 + ey1) / 2))
            ax_len = max(12, int((ex1 - ex0 + 1) * 0.72), int(char_w * 0.20))
            ay_len = max(5, int((ey1 - ey0 + 1) * 0.85), int(char_h * 0.035))
        else:
            cx = int(round((x0 + x1) / 2))
            cy = min(H - 1, y1 + max(3, int(char_h * 0.015)))
            ax_len = max(12, int(char_w * 0.28))
            ay_len = max(5, int(char_h * 0.035))

        ellipse = np.zeros((H, W), np.uint8)
        cv2.ellipse(ellipse, (cx, cy), (ax_len, ay_len), 0, 0, 360, 1, -1)
        ellipse = (ellipse & (body_mask == 0)).astype(np.uint8)
        sh_f = ellipse.astype(np.float32)

        shadow_a = raw_shadow * sh_f
        if shadow_a.max() <= 1e-6:
            shadow_a = sh_f * max(a_lo, 0.08)
        shadow_a = np.clip(shadow_a * boost, 0.0, 1.0)

        shadow_range = shadow_a > 1e-7
        stamp_mask = np.zeros_like(shadow_range, dtype=bool)
        no_blur_mask = np.zeros_like(shadow_range, dtype=bool)
        char_suppress_mask = np.zeros_like(shadow_range, dtype=bool)
        contact_core = np.zeros_like(shadow_range, dtype=bool)
        broad_near_body = cv2.dilate(
            solid,
            cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (101, 91))
        ).astype(bool)
        repair_y = (
            (yy >= float(y1 - max(140, int(0.42 * char_h)))) &
            (yy <= float(y1 - max(42, int(0.12 * char_h))))
        )
        if shadow_range.any():
            near_px = max(10, min(42, int(round(char_h * 0.075))))
            near_body = cv2.dilate(
                solid,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (near_px * 2 + 1, near_px * 2 + 1))
            ).astype(bool)
            vert_h = max(15, min(55, int(round(char_h * 0.13))))
            if vert_h % 2 == 0:
                vert_h += 1
            vertical_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, vert_h))
            vertical_part = cv2.morphologyEx(
                (shadow_a * 255.0).clip(0, 255).astype(np.uint8),
                cv2.MORPH_OPEN,
                vertical_kernel
            ).astype(np.float32) / 255.0
            local_bg = cv2.GaussianBlur(shadow_a, (49, 49), sigmaX=9.0, sigmaY=9.0)
            horizontal_bg = cv2.GaussianBlur(shadow_a, (31, 31), sigmaX=10.0, sigmaY=2.0)
            contact_core = (
                (yy >= float(y1 - max(72, int(0.18 * char_h)))) &
                ((shadow_a > 0.11) | (shadow_a > local_bg + 0.045))
            )
            upper_repair_y = (
                (yy >= float(y1 - max(150, int(0.44 * char_h)))) &
                (yy <= float(y1 - max(58, int(0.16 * char_h))))
            )
            upper_halo = shadow_range & upper_repair_y & near_body & (~contact_core)
            no_blur_mask |= upper_halo
            line_stroke = (
                shadow_range & repair_y & near_body &
                (~contact_core) &
                (shadow_a > 0.03) &
                (vertical_part > local_bg + 0.02) &
                (vertical_part > horizontal_bg + 0.012)
            )
            pants_stroke = (
                shadow_range & upper_repair_y & near_body &
                (~contact_core) &
                (shadow_a > max(0.008, a_lo * 0.35)) &
                (shadow_a > local_bg + 0.004)
            )
            stroke = line_stroke | pants_stroke
            if upper_halo.any():
                upper_clean = np.minimum(shadow_a, local_bg * 0.18)
                shadow_a = np.where(upper_halo, upper_clean, shadow_a)
            if stroke.any():
                stroke = cv2.dilate(
                    stroke.astype(np.uint8),
                    cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
                ).astype(bool)
                stroke &= ~contact_core
                no_blur_mask |= stroke | (shadow_range & upper_repair_y & near_body)
                valid = (shadow_range & (~stroke)).astype(np.float32)
                num = cv2.GaussianBlur(shadow_a * valid, (51, 51), sigmaX=10.0, sigmaY=10.0)
                den = cv2.GaussianBlur(valid, (51, 51), sigmaX=10.0, sigmaY=10.0)
                clone_a = np.where(den > 1e-5, num / np.clip(den, 1e-5, 1.0), local_bg)
                clean_clone = np.minimum(clone_a, local_bg * 0.16)
                shadow_a = np.where(pants_stroke & stroke, clean_clone, shadow_a)
                shadow_a = np.where(line_stroke & stroke, clone_a, shadow_a)
                stamp_mask = stroke

        magic_sel = (shadow_a > max(0.006, a_lo * 0.3)).astype(np.uint8)
        if magic_sel.any():
            magic_sel = cv2.morphologyEx(
                magic_sel, cv2.MORPH_CLOSE,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
            )
            inv_sel = ((magic_sel == 0).astype(np.uint8)) * 255
            flood = inv_sel.copy()
            cv2.floodFill(flood, np.zeros((H + 2, W + 2), np.uint8), (0, 0), 0)
            filled_shape = ((magic_sel > 0) | (flood > 0)).astype(np.uint8)

            inner_keep = cv2.erode(
                filled_shape,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (21, 21))
            ).astype(np.float32)
            blur_support = cv2.dilate(
                filled_shape,
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))
            ).astype(np.float32)
            no_blur = cv2.dilate(
                (stamp_mask | no_blur_mask).astype(np.uint8),
                np.ones((7, 7), np.uint8)
            ).astype(np.float32)
            edge_mix = ((1.0 - inner_keep) * blur_support * (1.0 - no_blur)).clip(0.0, 1.0)
            blurred_shadow = cv2.GaussianBlur(shadow_a, (41, 41), sigmaX=7.0, sigmaY=7.0)
            shadow_a = shadow_a * (1.0 - edge_mix) + blurred_shadow * edge_mix

        final_zone = (
            (yy >= float(y1 - max(240, int(0.62 * char_h)))) &
            (yy <= float(y1 - max(48, int(0.145 * char_h))))
        )
        final_stamp = (
            (no_blur_mask | ((shadow_a > max(0.00035, a_lo * 0.015)) & final_zone)) &
            (~contact_core)
        )
        if final_stamp.any():
            final_stamp = cv2.dilate(
                final_stamp.astype(np.uint8),
                cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 7))
            ).astype(bool)
            final_stamp &= final_zone & (~contact_core)
            shadow_a = np.where(final_stamp, 0.0, shadow_a)
            char_suppress_mask |= final_stamp

        ground_top = int(round(cy - max(2, ay_len * 0.62)))
        ground_fade = max(8, int(round(ay_len * 0.55)))
        ground_gate = np.clip((yy - float(ground_top)) / float(ground_fade), 0.0, 1.0)
        upper_residual = (
            (yy < float(ground_top + ground_fade)) &
            (a > max(0.00035, a_lo * 0.015)) &
            (~contact_core)
        )
        shadow_a *= ground_gate
        char_suppress_mask |= upper_residual & (ground_gate < 0.35)

        if ia is not None:
            item_mask = (ia > 0.01).astype(np.uint8)
            item_mask = cv2.erode(item_mask, np.ones((7, 7), np.uint8))
            shadow_a *= (1.0 - item_mask.astype(np.float32))

        shadow_a = np.clip(shadow_a, 0.0, 1.0)
        clean_char_a = np.clip(a - shadow_a, 0.0, 1.0)
        clean_char_a = np.where(char_suppress_mask, 0.0, clean_char_a)
        char_out[b, ..., 3] = clean_char_a
        shadow_out[b, ..., :3] = 0.0
        shadow_out[b, ..., 3] = shadow_a

    return torch.from_numpy(char_out), torch.from_numpy(shadow_out)


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
.ver{font-size:.72rem;color:#7a6aa8;margin-left:8px;font-weight:400}
.sub{font-size:.85rem;color:#888;margin-bottom:28px}

#dropzone,.dropzone-secondary{
  width:100%;max-width:680px;border:2px dashed #5a4080;border-radius:16px;
  padding:40px 24px;text-align:center;cursor:pointer;transition:.2s;background:#16122a}
#dropzone.over,.dropzone-secondary.over{border-color:#c9a0ff;background:#1f1840}
#dropzone.has-files,.dropzone-secondary.has-files{border-color:#7c5cbf;background:#1a1535}
#drop-icon{font-size:2.6rem;margin-bottom:10px;user-select:none}
#drop-text{font-size:.95rem;color:#aaa}
#drop-count,#item-drop-count{font-size:.85rem;color:#c9a0ff;margin-top:6px;min-height:1.2em}
#file-input{display:none}
.dropzone-secondary{display:none;margin-top:12px;padding:24px}
.dropzone-secondary .dz-title{font-size:.95rem;color:#ddd;margin-bottom:4px}
.dropzone-secondary .dz-sub{font-size:.78rem;color:#777}
#item-file-input{display:none}
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
.module.dragging{opacity:.4;border-color:#c9a0ff}
#modules.drag-active .module-body{display:none}
.drag-handle{cursor:grab;color:#6a5a9a;font-size:1.1rem;line-height:1;
  padding:2px 4px;border-radius:5px;user-select:none;flex-shrink:0}
.drag-handle:hover{color:#c9a0ff;background:#241b4a}
.drag-handle:active{cursor:grabbing}
.reorder-hint{width:100%;max-width:680px;font-size:.78rem;color:#7a6aa8;margin:18px 0 -4px;padding-left:4px}
.reorder-hint b{color:#c9a0ff}

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

<h1>Cherry — 帧序列处理工具 <span class="ver">shadow-ellipse-v5-groundgate</span></h1>
<p class="sub">拖入文件夹批量后处理 · ①去噪(主线) + ①-1并行阴影分离 → 其后继续缩小/锐化（每步可单独开关、可拖拽排序）</p>

<div id="dropzone">
  <div id="drop-icon">🗂️</div>
  <div id="drop-text">拖入「人物+阴影」帧文件夹，或点击选择文件</div>
  <div id="drop-count"></div>
  <label class="btn-select" for="file-input">选择文件夹 / 多个文件</label>
  <input type="file" id="file-input" multiple accept="image/png,image/webp,image/tiff">
</div>

<div id="item-dropzone" class="dropzone-secondary">
  <div class="dz-title">纯人物参考图（无阴影）</div>
  <div class="dz-sub">已改为自动使用①去噪结果；此区域保留不用上传</div>
  <div id="item-drop-count"></div>
  <label class="btn-select" for="item-file-input">选择参考文件夹 / 多个文件</label>
  <input type="file" id="item-file-input" multiple accept="image/png,image/webp,image/tiff">
</div>

<div class="reorder-hint">拖动左侧 <b>⠿</b> 手柄可调整处理顺序（从上到下依次执行）</div>
<div id="modules">

<!-- ①-1 阴影分离 -->
<div class="module active" id="mod-shadowsep">
  <div class="module-header" onclick="toggleModule('shadowsep')">
    <span class="module-title">①-1 阴影分离（并行分支）</span>
    <span style="font-size:.8rem;color:#888;flex:1">与①并行：固定先去噪0.01，再按脚下椭圆 mask 分离阴影；输出 _shadow/_merged</span>
    <div class="toggle-pill on" id="pill-shadowsep"></div>
  </div>
  <div class="module-body">
    <div class="param-row">
      <label>灰度上限</label>
      <input type="range" id="p-sep-gray" min="0.05" max="0.8" step="0.01" value="0.35">
      <span class="val" id="v-sep-gray">0.35</span>
    </div>
    <div class="tip">只把彩度低于此值的灰像素当阴影，越小越严格（避免吃掉彩色边缘）</div>
    <div class="param-row">
      <label>保护半径</label>
      <input type="range" id="p-sep-protect" min="-80" max="80" step="1" value="-70">
      <span class="val" id="v-sep-protect">-70</span>
    </div>
    <div class="tip">负数=人物 mask 内缩，正数=人物 mask 外扩；当前默认 -70</div>
    <div class="param-row">
      <label>阴影增强</label>
      <input type="range" id="p-sep-boost" min="0.5" max="3.0" step="0.1" value="1.0">
      <span class="val" id="v-sep-boost">1.0</span>
    </div>
    <div class="tip">提取出的阴影 alpha 倍率，1.0 = 原样，>1 让淡阴影更明显</div>
  </div>
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
      <input type="range" id="p-dn-thresh" min="0.0" max="1.0" step="0.005" value="0.85">
      <span class="val" id="v-dn-thresh">0.85</span>
    </div>
    <div class="tip">alpha 低于此值强制为 0；默认 0.85 ≈ 217/255</div>
    <div class="param-row">
      <label>平滑半径</label>
      <input type="range" id="p-dn-radius" min="0" max="20" step="1" value="0">
      <span class="val" id="v-dn-radius">0</span>
    </div>
    <div class="tip">清理后对 alpha 边缘做高斯平滑，0 = 不平滑</div>
  </div>
</div>

<!-- ③ 透明图模糊自叠加 -->
<div class="module active" id="mod-blur">
  <div class="module-header" onclick="toggleModule('blur')">
    <span class="module-title">③ 透明图模糊自叠加</span>
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

<!-- ④ 缩小① -->
<div class="module active" id="mod-resize1">
  <div class="module-header" onclick="toggleModule('resize1')">
    <span class="module-title">④ 缩小①（PS Bicubic Sharper）</span>
    <span style="font-size:.8rem;color:#888;flex:1">等比缩小，透明区填充</span>
    <div class="toggle-pill on" id="pill-resize1"></div>
  </div>
  <div class="module-body">
    <div class="param-row">
      <label>目标宽度</label>
      <input type="number" id="p-rw1" value="384" min="1" max="8192" step="1"
        style="width:90px;background:#1f1840;border:1px solid #4a3580;border-radius:6px;
               color:#c9a0ff;padding:4px 8px;font-size:.9rem">
      <span style="font-size:.85rem;color:#888">px</span>
    </div>
    <div class="param-row" style="margin-top:10px">
      <label>目标高度</label>
      <input type="number" id="p-rh1" value="512" min="1" max="8192" step="1"
        style="width:90px;background:#1f1840;border:1px solid #4a3580;border-radius:6px;
               color:#c9a0ff;padding:4px 8px;font-size:.9rem">
      <span style="font-size:.85rem;color:#888">px</span>
    </div>
    <div class="tip" style="padding-left:0;margin-top:8px">等比缩放到框内，多余空间透明填充</div>
  </div>
</div>

<!-- ⑤ 锐化① -->
<div class="module active" id="mod-sharp1">
  <div class="module-header" onclick="toggleModule('sharp1')">
    <span class="module-title">⑤ 锐化①</span>
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

<!-- ⑥ 缩小② -->
<div class="module active" id="mod-resize2">
  <div class="module-header" onclick="toggleModule('resize2')">
    <span class="module-title">⑥ 缩小②（PS Bicubic Sharper）</span>
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

<!-- ⑦ 锐化② -->
<div class="module active" id="mod-sharp2">
  <div class="module-header" onclick="toggleModule('sharp2')">
    <span class="module-title">⑦ 锐化②</span>
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

<!-- ⑧ 时序平滑 -->
<div class="module" id="mod-smooth">
  <div class="module-header" onclick="toggleModule('smooth')">
    <span class="module-title">⑧ 时序 Alpha 平滑</span>
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

</div><!-- /#modules -->

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
const moduleState = { shadowsep:true, denoise:true, blur:true, resize1:true, sharp1:true, resize2:true, sharp2:true, smooth:false };

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
linkSlider('p-sep-gray','v-sep-gray',2);
linkSlider('p-sep-protect','v-sep-protect',0);
linkSlider('p-sep-boost','v-sep-boost',1);
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

// ── 模块拖拽排序 ──────────────────────────────────────────────────────────
let dragEl = null;
const modCont = document.getElementById('modules');
const modDenoise = document.getElementById('mod-denoise');
const modShadowsep = document.getElementById('mod-shadowsep');
if (modDenoise && modShadowsep) {
  modCont.insertBefore(modDenoise, modShadowsep);
}
document.querySelectorAll('#modules .module-header').forEach(h=>{
  const handle = document.createElement('span');
  handle.className = 'drag-handle';
  handle.textContent = '⠿';
  handle.title = '拖动调整顺序';
  handle.setAttribute('draggable','true');
  handle.addEventListener('click', e=>e.stopPropagation());
  handle.addEventListener('dragstart', e=>{
    dragEl = h.closest('.module');
    dragEl.classList.add('dragging');
    modCont.classList.add('drag-active');
    e.dataTransfer.effectAllowed = 'move';
    e.dataTransfer.setData('text/plain', dragEl.id);
  });
  handle.addEventListener('dragend', ()=>{
    if(dragEl) dragEl.classList.remove('dragging');
    modCont.classList.remove('drag-active');
    dragEl = null;
  });
  h.insertBefore(handle, h.firstChild);
});
modCont.addEventListener('dragover', e=>{
  if(!dragEl) return;
  e.preventDefault();
  const cards = [...modCont.querySelectorAll('.module:not(.dragging)')];
  let ref = null;
  for(const c of cards){
    const r = c.getBoundingClientRect();
    if(e.clientY < r.top + r.height/2){ ref = c; break; }
  }
  if(ref) modCont.insertBefore(dragEl, ref); else modCont.appendChild(dragEl);
});
function currentOrder(){
  return [...modCont.querySelectorAll('.module')].map(m=>m.id.replace('mod-',''));
}

// ── 文件收集 ──────────────────────────────────────────────────────────────
let collectedFiles = [], itemFiles = [], resultBlob = null, folderName = '';
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('file-input');
const itemDropzone = document.getElementById('item-dropzone');
const itemFileInput = document.getElementById('item-file-input');

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

function setItemFiles(items) {
  itemFiles = items
    .filter(({file})=>/\.(png|webp|tif|tiff)$/i.test(file.name))
    .sort((a,b)=>a.relPath.localeCompare(b.relPath, undefined, {numeric:true}));
  document.getElementById('item-drop-count').textContent =
    itemFiles.length ? `已识别 ${itemFiles.length} 张参考图` : '未找到参考图';
  itemDropzone.classList.toggle('has-files', itemFiles.length>0);
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

itemDropzone.addEventListener('dragover', e=>{e.preventDefault();itemDropzone.classList.add('over');});
itemDropzone.addEventListener('dragleave', ()=>itemDropzone.classList.remove('over'));
itemDropzone.addEventListener('drop', async e=>{
  e.preventDefault(); itemDropzone.classList.remove('over');
  const items = [...e.dataTransfer.items];
  const all = (await Promise.all(items.map(i=>traverseEntry(i.webkitGetAsEntry())))).flat();
  setItemFiles(all);
});
itemFileInput.addEventListener('change', ()=>{
  const files = [...itemFileInput.files];
  setItemFiles(files.map(f=>({file: f, relPath: f.webkitRelativePath || f.name})));
});
itemDropzone.addEventListener('click', e=>{ if(e.target.closest('.btn-select')||e.target.tagName==='LABEL') return; itemFileInput.click(); });

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
  progText.textContent=`准备上传 ${collectedFiles.length} 张，参考 ${itemFiles.length} 张…`;

  const fd = new FormData();
  const V = id => document.getElementById(id).value;
  fd.append('folder_name',  folderName);
  fd.append('order',        currentOrder().join(','));
  // ⓪ 阴影分离
  fd.append('use_shadowsep',moduleState.shadowsep?'1':'0');
  fd.append('sep_gray',     V('p-sep-gray'));
  fd.append('sep_protect',  V('p-sep-protect'));
  fd.append('sep_boost',    V('p-sep-boost'));
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


def run_pipeline(form, files, item_files=None):
    """核心后处理流水线。

    form  : dict[str,str]   表单参数（缺省用默认值）
    files : list[(filename, bytes)]  上传的“人物+阴影”图片
    item_files : list[(filename, bytes)]  可选，纯人物参考图片
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

    # ② 阴影分离
    use_shadowsep = g("use_shadowsep", "0") == "1"
    sep_gray      = float(g("sep_gray", 0.35))
    sep_protect   = int(g("sep_protect", 0))
    sep_boost     = float(g("sep_boost", 1.0))
    sep_blur_radius = int(g("sep_blur_radius", 2))
    sep_blur_sigma  = float(g("sep_blur_sigma", 2.4))
    # ① 去除外部噪点
    use_denoise  = g("use_denoise", "1") == "1"
    dn_thresh    = float(g("dn_thresh", 0.85))
    dn_radius    = int(g("dn_radius", 0))
    # ② 透明图模糊自叠加
    use_blur     = g("use_blur", "1") == "1"
    blur_radius  = int(g("blur_radius", 1))
    blur_sigma   = float(g("blur_sigma", 10.0))
    # ③ 缩小①
    use_resize1  = g("use_resize1", "1") == "1"
    resize1_w    = int(g("resize1_w", 384))
    resize1_h    = int(g("resize1_h", 512))
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

    item_files = list(item_files or [])
    main_files = []
    for fname, data in files:
        rel = fname.replace("\\", "/")
        if rel.startswith("__item__/"):
            item_files.append((rel[len("__item__/"):], data))
        else:
            main_files.append((fname, data))
    files = main_files

    if not files:
        raise ValueError("没有收到图片")

    # ── 执行顺序（前端可拖拽排序，order=以逗号分隔的步骤 id）─────────────────
    DEFAULT_ORDER = ["denoise", "shadowsep", "blur", "resize1", "sharp1", "resize2", "sharp2", "smooth"]
    order = [s for s in g("order", "").split(",") if s]
    for s in DEFAULT_ORDER:          # 补上漏传的步骤，保持默认相对顺序
        if s not in order:
            order.append(s)
    order = [s for s in order if s in DEFAULT_ORDER]   # 去掉非法 id

    step_fns = {
        # shadowsep 为特殊两输出步骤，在循环里单独处理，这里只登记开关
        "shadowsep": (use_shadowsep, None),
        "denoise": (use_denoise, lambda b: alpha_denoise(b, dn_thresh, dn_radius)),
        "blur":    (use_blur,    lambda b: blur_under_composite(b, blur_radius, blur_sigma)),
        "resize1": (use_resize1, lambda b: ps_bicubic_sharper(b, resize1_w, resize1_h)),
        "sharp1":  (use_sharp1,  lambda b: sharpen(b, sharp1_amount, sharp1_radius, sharp1_thresh, sharp1_shrink)),
        "resize2": (use_resize2, lambda b: ps_bicubic_sharper(b, resize2_w, resize2_h)),
        "sharp2":  (use_sharp2,  lambda b: sharpen(b, sharp2_amount, sharp2_radius, sharp2_thresh, sharp2_shrink)),
        "smooth":  (use_smooth,  lambda b: temporal_smooth(b, window_size, sigma, sync_rgb, min_alpha,
                                                           ring_width, smooth_method, fill_gap, bg_thresh)),
    }
    step_names = {
        "shadowsep": "①-1阴影分离",
        "denoise": "去噪", "blur": "模糊自叠加",
        "resize1": f"缩小{resize1_w}x{resize1_h}", "sharp1": "锐化①",
        "resize2": f"缩小{resize2_w}x{resize2_h}", "sharp2": "锐化②",
        "smooth": "时序平滑",
    }

    # 按目录分组，每个目录单独作为一批
    from collections import defaultdict
    groups = defaultdict(list)
    for fname, data in files:
        rel = fname.replace("\\", "/")
        dir_part  = os.path.dirname(rel)
        base_name = os.path.basename(rel)
        groups[dir_part].append((base_name, data))

    item_by_base = {}
    for fname, data in item_files:
        rel = fname.replace("\\", "/")
        item_by_base[os.path.basename(rel)] = data

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
            item_batch = None
            if item_by_base:
                item_frames = []
                missing_items = []
                for name, _ in file_list:
                    data = item_by_base.get(name)
                    if data is None:
                        missing_items.append(name)
                    else:
                        item_frames.append(decode(data))
                if missing_items:
                    raise ValueError(f"纯人物参考图缺少 {len(missing_items)} 张，例如: {missing_items[0]}")
                item_batch = torch.from_numpy(np.stack(item_frames, axis=0).astype(np.float32) / 255.0)

            flicker_before_all.append(mean_flicker(batch))
            print(f"[pipeline] 顺序: {' → '.join(s for s in order if step_fns[s][0]) or '（全部关闭）'}", flush=True)

            shadow_batch = None
            shadow_save_batch = None
            base_batch_for_branch = batch.clone()
            item_batch_for_branch = item_batch.clone() if item_batch is not None else None
            for sid in order:
                enabled, fn = step_fns[sid]
                if not enabled:
                    continue
                if sid == "shadowsep":
                    # ①-1 并行分支：
                    # combined = 低阈值保留“人物+阴影”。
                    # item = ①去噪后的纯人物结果；透明区按白底补齐 RGB，作为无阴影参考。
                    branch = alpha_denoise(base_batch_for_branch.clone(), 0.01, 0)
                    if item_batch_for_branch is not None:
                        item_branch = alpha_denoise(item_batch_for_branch.clone(), 0.01, 0)
                        if item_branch.shape[1:3] != branch.shape[1:3]:
                            item_branch = ps_bicubic_sharper(
                                item_branch, branch.shape[2], branch.shape[1]
                            )
                    else:
                        item_branch = alpha_denoise(base_batch_for_branch.clone(), dn_thresh, dn_radius)
                    item_a = item_branch[..., 3:4].clamp(0.0, 1.0)
                    item_rgb_ref = (item_branch[..., :3] * item_a + (1.0 - item_a)).clamp(0.0, 1.0)
                    char_branch, shadow_batch = shadow_separate_v5(
                        branch, sep_gray, sep_protect, 0.1,
                        sep_boost, sep_blur_radius, sep_blur_sigma,
                        item_alpha=item_branch[..., 3],
                        item_rgb=item_rgb_ref)
                    # 与主线当前尺寸对齐，并从主线 alpha 扣除阴影，得到 _char
                    if char_branch.shape[1:3] != batch.shape[1:3]:
                        char_branch = ps_bicubic_sharper(
                            char_branch, batch.shape[2], batch.shape[1]
                        )
                    if shadow_batch.shape[1:3] != batch.shape[1:3]:
                        shadow_batch = ps_bicubic_sharper(
                            shadow_batch, batch.shape[2], batch.shape[1]
                        )
                    item_for_shadow = item_branch
                    if item_for_shadow.shape[1:3] != shadow_batch.shape[1:3]:
                        item_for_shadow = ps_bicubic_sharper(
                            item_for_shadow, shadow_batch.shape[2], shadow_batch.shape[1]
                        )
                    item_cut = (item_for_shadow[..., 3:4] > 0.65).float()
                    shadow_save_batch = shadow_batch.clone()
                    shadow_save_batch = torch.cat([
                        shadow_save_batch[..., :3],
                        shadow_save_batch[..., 3:4] * (1.0 - item_cut)
                    ], dim=-1)
                    # 主体保护：优先用纯人物 alpha，避免鞋边/脚跟抗锯齿被扣成镂空。
                    if item_batch_for_branch is not None:
                        protect_src = item_for_shadow[..., 3]
                        solid_now = (protect_src > 0.01).cpu().numpy().astype(np.uint8)
                    else:
                        ba = batch[..., 3]
                        solid_now = (ba > 0.45).cpu().numpy().astype(np.uint8)
                    k = np.ones((3, 3), np.uint8)
                    solid_now = np.stack([cv2.dilate(solid_now[i], k) for i in range(solid_now.shape[0])], axis=0)
                    protect = torch.from_numpy(solid_now.astype(np.float32)).to(batch.device).unsqueeze(-1)
                    new_a = torch.where(
                        protect > 0.5,
                        batch[..., 3:4],
                        torch.minimum(
                            torch.clamp(batch[..., 3:4] - shadow_batch[..., 3:4], 0.0, 1.0),
                            char_branch[..., 3:4].clamp(0.0, 1.0),
                        ),
                    )
                    batch = torch.cat([batch[..., :3], new_a], dim=-1)
                else:
                    batch = fn(batch)

            flicker_after_all.append(mean_flicker(batch))

            result_u8 = (batch.numpy() * 255).clip(0, 255).astype(np.uint8)
            for i, (name, _) in enumerate(file_list):
                base = os.path.splitext(name)[0]
                if shadow_batch is not None:
                    out_path = (f"{dir_part}/_char/{base}.png" if dir_part
                                else f"{folder_name}/_char/{base}.png")
                else:
                    out_path = f"{dir_part}/{base}.png" if dir_part else f"{folder_name}/{base}.png"
                zf.writestr(out_path, encode(result_u8[i]))

            # 阴影层单独存放，尺寸对齐当前输出（例如最终 384x512）
            if shadow_batch is not None:
                if shadow_batch.shape[1:3] != batch.shape[1:3]:
                    shadow_batch = ps_bicubic_sharper(
                        shadow_batch, batch.shape[2], batch.shape[1]
                    )
                shadow_to_save = shadow_save_batch if shadow_save_batch is not None else shadow_batch
                if shadow_to_save.shape[1:3] != batch.shape[1:3]:
                    shadow_to_save = ps_bicubic_sharper(
                        shadow_to_save, batch.shape[2], batch.shape[1]
                    )
                shadow_u8 = (shadow_to_save.numpy() * 255).clip(0, 255).astype(np.uint8)
                for i, (name, _) in enumerate(file_list):
                    base = os.path.splitext(name)[0]
                    sh_path = (f"{dir_part}/_shadow/{base}.png" if dir_part
                               else f"{folder_name}/_shadow/{base}.png")
                    zf.writestr(sh_path, encode(shadow_u8[i]))

                # 第三版：主体(处理后) + 阴影(提取层) 合成图
                ca = batch[..., 3:4]
                sa = shadow_batch[..., 3:4]
                c_rgb = batch[..., :3]
                s_rgb = shadow_batch[..., :3]
                out_a = ca + sa * (1.0 - ca)
                out_p = c_rgb * ca + s_rgb * sa * (1.0 - ca)
                out_rgb = torch.where(
                    out_a > 1e-6,
                    out_p / out_a.clamp(min=1e-6),
                    torch.zeros_like(out_p),
                )
                merged = torch.cat([out_rgb, out_a], dim=-1).clamp(0, 1)
                merged_u8 = (merged.numpy() * 255).clip(0, 255).astype(np.uint8)
                for i, (name, _) in enumerate(file_list):
                    base = os.path.splitext(name)[0]
                    mg_path = (f"{dir_part}/_merged/{base}.png" if dir_part
                               else f"{folder_name}/_merged/{base}.png")
                    zf.writestr(mg_path, encode(merged_u8[i]))

    flicker_before = float(np.mean(flicker_before_all)) if flicker_before_all else 0.0
    flicker_after  = float(np.mean(flicker_after_all))  if flicker_after_all  else 0.0
    reduction_pct  = (1 - flicker_after / max(flicker_before, 1e-9)) * 100
    steps = [step_names[s] for s in order if step_fns[s][0]]
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
        item_files = [(f.filename, f.read())
                      for f in sorted(request.files.getlist("item_images"), key=lambda f: f.filename)]
        zip_bytes, stats = run_pipeline(form, files, item_files)
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
