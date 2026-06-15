"""
Cherry - 去除外部噪点

对应同事的 Alpha 杂点清除工具：
    if alpha < 阈值: alpha = 0   (RGB 保持不变，避免色边)

适用于抠图后透明背景上残留的微弱 alpha 杂点清理。
可选平滑半径：对清理后的 alpha 边缘做高斯模糊，减少颗粒感。
"""

import torch
import numpy as np
import cv2


class CherryAlphaDenoise:

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "图像": ("IMAGE",),
                "阈值": ("FLOAT", {
                    "default": 0.06,
                    "min": 0.0,
                    "max": 1.0,
                    "step": 0.01,
                    "tooltip": "alpha 严格小于此值的像素 → alpha = 0（完全透明）。默认 0.06 ≈ 15/255。",
                }),
                "平滑半径": ("INT", {
                    "default": 0,
                    "min": 0,
                    "max": 20,
                    "step": 1,
                    "tooltip": "对 alpha 边缘做高斯模糊，减少颗粒感。0 = 不模糊；建议从 1~3 开始调。",
                }),
            },
            "optional": {
                "蒙版": ("MASK",),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("图像(RGBA)", "蒙版")
    FUNCTION = "denoise"
    CATEGORY = "Cherry_lizi"

    def denoise(self, 图像, 阈值, 平滑半径, 蒙版=None):
        imgs = 图像.cpu().float()
        B, H, W, C = imgs.shape

        out_imgs, out_masks = [], []

        for i in range(B):
            img = imgs[i]

            if C == 4:
                rgb = img[:, :, :3]
                alpha = img[:, :, 3]
            elif 蒙版 is not None:
                idx = min(i, 蒙版.shape[0] - 1)
                rgb = img[:, :, :3] if C >= 3 else img.unsqueeze(-1).expand(H, W, 3)
                alpha = 蒙版[idx].cpu().float()
            else:
                rgb = img[:, :, :3] if C >= 3 else img.unsqueeze(-1).expand(H, W, 3)
                alpha = torch.ones(H, W)

            # Step1: 阈值清零
            cleaned_alpha = torch.where(
                alpha < 阈值,
                torch.zeros_like(alpha),
                alpha,
            )

            # Step2: 高斯平滑边缘（可选）
            if 平滑半径 > 0:
                ksize = 平滑半径 * 2 + 1
                alpha_np = cleaned_alpha.numpy()
                alpha_np = cv2.GaussianBlur(alpha_np, (ksize, ksize), sigmaX=0)
                # 平滑只影响边缘过渡，核心实心区域恢复原值（避免整体变透）
                core_mask = cleaned_alpha.numpy() > 0.5
                alpha_np = np.where(core_mask, np.maximum(alpha_np, cleaned_alpha.numpy()), alpha_np)
                cleaned_alpha = torch.from_numpy(alpha_np.astype(np.float32))

            out_img = torch.cat([rgb, cleaned_alpha.unsqueeze(-1)], dim=-1)
            out_imgs.append(out_img)
            out_masks.append(cleaned_alpha)

        return (
            torch.stack(out_imgs).to(图像.device),
            torch.stack(out_masks).to(图像.device),
        )


NODE_CLASS_MAPPINGS = {
    "CherryAlphaDenoise": CherryAlphaDenoise,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CherryAlphaDenoise": "Cherry - 去除外部噪点",
}
