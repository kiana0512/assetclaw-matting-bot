"""Cherry - PS缩小 (Bicubic Sharper)

从网页工具迁移的 PS Bicubic Sharper 等比缩小逻辑：
    · a=-1.0 三次核（比标准 bicubic 的 -0.75 负叶更深，边缘更锐）
    · 缩小前高斯低通预滤波抗锯齿
    · 等比缩小到目标框内，多余空间居中透明填充（RGBA）/ 白色填充（RGB）

尺寸根据输入图自动等比匹配：scale = min(目标宽/W, 目标高/H)。
"""
import torch
import torch.nn.functional as F


def ps_bicubic_sharper(imgs: torch.Tensor, target_w: int, target_h: int) -> torch.Tensor:
    """PS Bicubic Sharper：a=-1.0 三次核，分离卷积实现，等比缩小+透明填充。"""
    B, H, W, C = imgs.shape
    scale = min(target_w / W, target_h / H)
    nW = max(1, round(W * scale))
    nH = max(1, round(H * scale))

    x = imgs.permute(0, 3, 1, 2).float()   # (B, C, H, W)
    a = -1.0

    def cubic_w(coords: torch.Tensor, src_size: int):
        i0 = coords.floor().long()
        frac = coords - i0.float()
        dist = torch.stack([1 + frac, frac, 1 - frac, 2 - frac], dim=1)
        t = dist.abs()
        w = torch.zeros_like(t)
        m1 = t <= 1
        m2 = (t > 1) & (t <= 2)
        w[m1] = (a + 2) * t[m1] ** 3 - (a + 3) * t[m1] ** 2 + 1
        w[m2] = a * t[m2] ** 3 - 5 * a * t[m2] ** 2 + 8 * a * t[m2] - 4 * a
        w = w / w.sum(dim=1, keepdim=True).clamp(min=1e-6)
        idx = torch.stack([
            (i0 - 1).clamp(0, src_size - 1),
            i0.clamp(0, src_size - 1),
            (i0 + 1).clamp(0, src_size - 1),
            (i0 + 2).clamp(0, src_size - 1),
        ], dim=1)
        return idx, w

    dev = x.device

    # 缩小时先高斯低通抗锯齿
    if scale < 1.0:
        sigma = 0.5 / scale
        r = max(1, int(sigma * 3))
        ks = r * 2 + 1
        coords1d = torch.arange(ks, dtype=torch.float32, device=dev) - r
        g1d = torch.exp(-0.5 * (coords1d / sigma) ** 2)
        g1d = g1d / g1d.sum()
        g2d = (g1d.unsqueeze(0) * g1d.unsqueeze(1)).unsqueeze(0).unsqueeze(0)
        g2d = g2d.expand(C, 1, ks, ks)
        x = F.conv2d(F.pad(x, [r, r, r, r], mode="reflect"), g2d, groups=C)

    # center-aligned 坐标映射（与 PS 对齐）
    y_src = (torch.arange(nH, dtype=torch.float32, device=dev) + 0.5) * H / nH - 0.5
    x_src = (torch.arange(nW, dtype=torch.float32, device=dev) + 0.5) * W / nW - 0.5

    yi, wy = cubic_w(y_src, H)
    xi, wx = cubic_w(x_src, W)

    xg = x[:, :, :, xi.reshape(-1)].view(B, C, H, nW, 4)
    inter = (xg * wx.view(1, 1, 1, nW, 4)).sum(-1)

    inter = inter.permute(0, 1, 3, 2)
    yg = inter[:, :, :, yi.reshape(-1)].view(B, C, nW, nH, 4)
    result = (yg * wy.view(1, 1, 1, nH, 4)).sum(-1)
    result = result.permute(0, 1, 3, 2).clamp(0, 1)

    # 画布：RGBA 透明，RGB 白色
    canvas = torch.ones(B, C, target_h, target_w, device=dev, dtype=imgs.dtype)
    if C == 4:
        canvas[:, 3] = 0.0
    top = (target_h - nH) // 2
    left = (target_w - nW) // 2
    canvas[:, :, top:top + nH, left:left + nW] = result

    # 覆盖蒙版：实际图像所在区域=1，透明填充区=0
    cover = torch.zeros(B, target_h, target_w, device=dev, dtype=imgs.dtype)
    cover[:, top:top + nH, left:left + nW] = 1.0

    out = canvas.permute(0, 2, 3, 1).clamp(0, 1)
    return out, cover


class CherryPSResize:
    """
    Cherry - PS缩小 (Bicubic Sharper)

    等比缩小到所选目标框内，居中后多余空间透明填充。
    缩小尺寸根据输入图自动等比匹配。
    """

    PRESETS = ["长方形 486×608", "正方形 256×256", "自定义"]

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "图像":     ("IMAGE", {"tooltip": "输入图像（RGB 或 RGBA）"}),
                "尺寸预设": (cls.PRESETS, {
                    "default": "长方形 486×608",
                    "tooltip": "长方形=486×608，正方形=256×256，自定义=使用下方宽高",
                }),
                "目标宽度": ("INT", {"default": 486, "min": 1, "max": 8192, "step": 1,
                                      "tooltip": "仅在“自定义”预设下生效"}),
                "目标高度": ("INT", {"default": 608, "min": 1, "max": 8192, "step": 1,
                                      "tooltip": "仅在“自定义”预设下生效"}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("图像", "覆盖蒙版")
    FUNCTION = "resize"
    CATEGORY = "Cherry_lizi"

    def resize(self, 图像, 尺寸预设, 目标宽度, 目标高度):
        if 尺寸预设 == "长方形 486×608":
            tw, th = 486, 608
        elif 尺寸预设 == "正方形 256×256":
            tw, th = 256, 256
        else:
            tw, th = int(目标宽度), int(目标高度)

        imgs = 图像.float().clamp(0.0, 1.0)
        out, cover = ps_bicubic_sharper(imgs, tw, th)

        # RGBA 时蒙版结合 alpha，否则仅用覆盖区域
        if out.shape[-1] == 4:
            mask = (cover * out[..., 3]).clamp(0.0, 1.0)
        else:
            mask = cover.clamp(0.0, 1.0)
        return (out, mask)


NODE_CLASS_MAPPINGS = {
    "CherryPSResize": CherryPSResize,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CherryPSResize": "Cherry - PS缩小(Bicubic Sharper)",
}
