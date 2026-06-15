"""Cherry - 时序 Alpha 平滑"""

import cv2
import numpy as np
import torch
import torch.nn.functional as F


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


class CherryTemporalSmooth:
    """
    对一批连续帧的 alpha 通道做时序加权平均，抑制逐帧闪烁。

    输入：RGBA 图像批（按时间顺序排列，如 B=24 即一秒动画）
    输出：平滑后的 RGBA 图像批（RGB 可选同步修正）

    平滑公式（对帧 t）：
        alpha_out[t] = Σ w[i] × alpha[t+i]  / Σ w[i]
        其中 i ∈ [-r, r]，r = (窗口大小-1)/2

    窗口权重：中心帧权重最高，向两端按高斯衰减。
    边界处理：只使用实际存在的帧，权重重新归一化。

    RGB 修正（仅在开启时）：
        对于 alpha < 0.05 的近透明区跳过（除以接近0会放大噪声）
        对于其余像素，将 RGB 同步做时序平均，减少发丝颜色跳变。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "图像":     ("IMAGE", {"tooltip": "RGBA 图像批，按时间顺序排列"}),
                "窗口大小": ("INT", {
                    "default": 5,
                    "min": 3,
                    "max": 11,
                    "step": 2,
                    "tooltip": "时序窗口帧数（必须为奇数）。5帧=24fps下覆盖±83ms，7帧覆盖±125ms。",
                }),
                "平滑强度": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.1,
                    "max": 3.0,
                    "step": 0.1,
                    "tooltip": "高斯权重的 sigma 值。值越大中心帧与邻帧权重越接近（更强平滑），值越小中心帧权重越突出（更保细节）。",
                }),
                "同步修正RGB": ("BOOLEAN", {
                    "default": False,
                    "label_on":  "⚠️ 已开启 —— 仅限静止素材，动画会模糊！",
                    "label_off": "✅ 已关闭 —— 动画请保持此状态",
                }),
                "最小Alpha保护": ("FLOAT", {
                    "default": 0.05,
                    "min": 0.0,
                    "max": 0.3,
                    "step": 0.01,
                    "tooltip": "alpha 低于此值的像素跳过 RGB 平滑（避免放大透明区噪声）。",
                }),
                "环形宽度": ("INT", {
                    "default": 25,
                    "min": 0,
                    "max": 60,
                    "step": 1,
                    "tooltip": "动态模糊只作用在 alpha 边缘向内 N 像素的环形区域，面部/眼睛等内部不受影响。0 = 全区域。",
                }),
            },
        }

    RETURN_TYPES  = ("IMAGE",)
    RETURN_NAMES  = ("图像",)
    FUNCTION      = "smooth"
    CATEGORY      = "Cherry_lizi"

    def smooth(self, 图像, 窗口大小, 平滑强度, 同步修正RGB, 最小Alpha保护, 环形宽度):
        imgs = 图像.float().clamp(0.0, 1.0)  # (B, H, W, C)
        B, H, W, C = imgs.shape

        if C < 4:
            print("[Cherry 时序平滑] 输入不含 Alpha 通道，已跳过，原样输出")
            return (imgs,)

        r = (窗口大小 - 1) // 2

        # 高斯权重（中心=0，半径=r）
        coords = torch.arange(-r, r + 1, dtype=torch.float32)
        sigma  = max(平滑强度, 0.1)
        weights_base = torch.exp(-0.5 * (coords / sigma) ** 2)  # (W_size,)

        rgb   = imgs[..., :3]  # (B, H, W, 3)
        alpha = imgs[..., 3]   # (B, H, W)

        alpha_out = torch.zeros_like(alpha)
        rgb_out   = rgb.clone()

        # 预计算环形 mask
        ring = None
        if 同步修正RGB and 环形宽度 > 0:
            ring = _ring_mask(alpha, int(环形宽度))  # (B,H,W)

        for t in range(B):
            t_start = max(0, t - r)
            t_end   = min(B, t + r + 1)

            # 对应权重切片（补偿边界偏移）
            w_start = t_start - (t - r)
            w_end   = w_start + (t_end - t_start)
            w = weights_base[w_start:w_end]             # (n,)
            w = w / w.sum()                              # 归一化

            # 加权平均 alpha
            # alpha[t_start:t_end] shape: (n, H, W)
            a_slice = alpha[t_start:t_end]               # (n, H, W)
            w_view  = w.view(-1, 1, 1).to(a_slice.device)
            alpha_out[t] = (a_slice * w_view).sum(0)
            # 当前帧 alpha=0 的背景像素不向外扩散 alpha，防止产生黑色暗晕
            alpha_out[t] = torch.where(alpha[t] > 0, alpha_out[t], torch.zeros_like(alpha_out[t]))

            if 同步修正RGB:
                r_slice = rgb[t_start:t_end]                   # (n, H, W, 3)
                a_slice = alpha[t_start:t_end]                  # (n, H, W)
                w3      = w.view(-1, 1, 1, 1).to(r_slice.device)
                pre_avg = (r_slice * a_slice.unsqueeze(-1) * w3).sum(0)
                a_denom = alpha_out[t].unsqueeze(-1).clamp(min=1e-6)
                rgb_avg = (pre_avg / a_denom).clamp(0, 1)

                # 当前帧 alpha >= 0.5 才做 RGB 混合，外边缘半透明像素不参与
                solid = alpha[t] >= 0.5
                if ring is not None:
                    mask = (solid & (ring[t] > 0)).unsqueeze(-1)
                else:
                    mask = (solid & (alpha[t] >= 最小Alpha保护)).unsqueeze(-1)
                rgb_out[t] = torch.where(mask, rgb_avg, rgb[t])

        result = torch.cat([rgb_out, alpha_out.unsqueeze(-1)], dim=-1).clamp(0.0, 1.0)
        print(f"[Cherry 时序平滑] {B}帧  窗口={窗口大小}  sigma={平滑强度:.1f}  RGB修正={'开' if 同步修正RGB else '关'}")
        return (result,)


NODE_CLASS_MAPPINGS = {
    "CherryTemporalSmooth": CherryTemporalSmooth,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CherryTemporalSmooth": "Cherry - 时序 Alpha 平滑",
}
