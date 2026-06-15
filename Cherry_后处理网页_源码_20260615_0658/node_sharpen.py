"""Cherry - 锐化"""

import cv2
import numpy as np
import torch
import torch.nn.functional as F


def _gaussian_kernel(radius: int, sigma: float, device, dtype) -> torch.Tensor:
    size = radius * 2 + 1
    coords = torch.arange(size, dtype=torch.float32) - radius
    g = torch.exp(-0.5 * (coords / sigma) ** 2)
    g = g / g.sum()
    kernel = g.unsqueeze(0) * g.unsqueeze(1)
    return kernel.to(device=device, dtype=dtype)


def _erode_alpha_mask(alpha: torch.Tensor, pixels: int) -> torch.Tensor:
    """
    对 alpha > 0.5 的区域做形态学腐蚀，返回内缩后的 mask (B, H, W, 1) float。
    """
    B, H, W = alpha.shape
    kernel = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (pixels * 2 + 1, pixels * 2 + 1)
    )
    masks = []
    alpha_np = (alpha.cpu().numpy() > 0.5).astype(np.uint8)
    for b in range(B):
        eroded = cv2.erode(alpha_np[b], kernel)
        masks.append(eroded)
    result = torch.from_numpy(np.stack(masks, axis=0)).float()   # (B, H, W)
    return result.unsqueeze(-1).to(alpha.device)                 # (B, H, W, 1)


class CherrySharpen:
    """
    USM（反锐化掩模）锐化。

    内缩像素 > 0 时：对 alpha 做形态学腐蚀，只锐化内缩后的实心区域，
    完全避开半透明边缘 → 动画序列不会引入帧间闪烁。

    内缩像素 = 0 时：退回透明区保护模式（alpha 阈值控制）。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "图像": ("IMAGE",),
                "强度": ("FLOAT", {
                    "default": 1.0,
                    "min": 0.0,
                    "max": 5.0,
                    "step": 0.1,
                    "tooltip": "锐化强度。1.0 = 标准；2.0 = 明显；0.5 = 轻微。",
                }),
                "半径": ("INT", {
                    "default": 2,
                    "min": 1,
                    "max": 8,
                    "step": 1,
                    "tooltip": "模糊半径，值越大锐化范围越宽，建议 1~3。",
                }),
                "阈值": ("FLOAT", {
                    "default": 0.02,
                    "min": 0.0,
                    "max": 0.3,
                    "step": 0.005,
                    "tooltip": "差异低于此值的区域不锐化（保护平坦背景）。",
                }),
                "内缩像素": ("INT", {
                    "default": 4,
                    "min": 0,
                    "max": 50,
                    "step": 1,
                    "tooltip": (
                        "对 alpha 做形态学腐蚀 N 像素，只锐化内缩后的实心区域。\n"
                        "动画推荐此模式：边缘半透明区不参与锐化，不引入帧间闪烁。\n"
                        "0 = 关闭内缩，改用下方「透明区保护」阈值模式。"
                    ),
                }),
                "透明区保护": ("FLOAT", {
                    "default": 0.05,
                    "min": 0.0,
                    "max": 0.5,
                    "step": 0.01,
                    "tooltip": "内缩像素=0 时生效：alpha 低于此值的像素不锐化。0 = 全图锐化。",
                }),
            },
        }

    RETURN_TYPES = ("IMAGE",)
    RETURN_NAMES = ("图像",)
    FUNCTION = "sharpen"
    CATEGORY = "Cherry_lizi"

    def sharpen(self, 图像, 强度, 半径, 阈值, 内缩像素, 透明区保护):
        imgs = 图像.float().clamp(0.0, 1.0)
        B, H, W, C = imgs.shape
        has_alpha = (C == 4)
        rgb = imgs[..., :3]

        # ── 确定锐化区域 mask (B, H, W, 1) ─────────────────────────────
        if has_alpha and 内缩像素 > 0:
            sharp_mask = _erode_alpha_mask(imgs[..., 3], 内缩像素)
            mode = f"内缩{内缩像素}px"
        elif has_alpha and 透明区保护 > 0:
            sharp_mask = (imgs[..., 3:4] >= 透明区保护).float()
            mode = f"alpha≥{透明区保护}"
        else:
            sharp_mask = torch.ones(B, H, W, 1, device=imgs.device)
            mode = "全图"

        # ── 高斯模糊（reflect padding，避免边缘锯齿）────────────────────
        sigma   = max(半径 / 2.0, 0.5)
        kernel  = _gaussian_kernel(半径, sigma, rgb.device, rgb.dtype)
        ksize   = kernel.shape[0]
        pad     = 半径
        k4d     = kernel.unsqueeze(0).unsqueeze(0).expand(3, 1, ksize, ksize)
        x       = rgb.permute(0, 3, 1, 2)
        blurred = F.conv2d(F.pad(x, [pad, pad, pad, pad], mode="reflect"), k4d, groups=3)

        # ── USM ──────────────────────────────────────────────────────────
        diff     = x - blurred
        thr_mask = (diff.abs() > 阈值).float()
        sharpened = (x + 强度 * diff * thr_mask).clamp(0.0, 1.0)

        # 只在 sharp_mask 区域应用锐化，其余还原原始值
        sm       = sharp_mask.permute(0, 3, 1, 2)          # (B, 1, H, W)
        out_x    = sharpened * sm + x * (1.0 - sm)
        rgb_out  = out_x.permute(0, 2, 3, 1)

        if has_alpha:
            result = torch.cat([rgb_out, imgs[..., 3:4]], dim=-1)
        else:
            result = rgb_out

        gain = (diff * thr_mask * sm).abs().mean().item()
        print(f"[Cherry 锐化] 强度={强度}  半径={半径}  模式={mode}  锐化量={gain:.4f}")
        return (result,)


NODE_CLASS_MAPPINGS = {
    "CherrySharpen": CherrySharpen,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CherrySharpen": "Cherry - 锐化",
}
