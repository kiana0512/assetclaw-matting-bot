"""Cherry - 透明图模糊自叠加（同一张图模糊后垫底再叠清晰原图）

只需一张「扣完的图」（带 alpha 的 RGBA）：
    · 底层 = 这张图的高斯模糊版本（默认半径 10），作为柔光/虚化底
    · 顶层 = 原始清晰图

合成用标准 alpha-over（Porter-Duff over）：
    out_a   = top_a + bot_a * (1 - top_a)
    out_rgb = (top_rgb*top_a + bot_rgb*bot_a*(1-top_a)) / out_a

底层模糊采用「预乘 alpha」方式，避免透明区 RGB 渗入边缘形成黑边/脏边。
"""
import torch
import torch.nn.functional as F


def _gaussian_blur(img, radius, sigma):
    """对 (B,H,W,C) 图像做可分离高斯模糊，逐通道处理。"""
    if radius <= 0 or sigma <= 0:
        return img

    device = img.device
    dtype = img.dtype
    channels = img.shape[3]

    ax = torch.arange(-radius, radius + 1, device=device, dtype=dtype)
    k = torch.exp(-(ax * ax) / (2.0 * sigma * sigma))
    k = k / k.sum()

    x = img.permute(0, 3, 1, 2)  # -> B,C,H,W

    k_h = k.view(1, 1, 1, -1).repeat(channels, 1, 1, 1)
    x = F.pad(x, (radius, radius, 0, 0), mode="replicate")
    x = F.conv2d(x, k_h, groups=channels)

    k_v = k.view(1, 1, -1, 1).repeat(channels, 1, 1, 1)
    x = F.pad(x, (0, 0, radius, radius), mode="replicate")
    x = F.conv2d(x, k_v, groups=channels)

    return x.permute(0, 2, 3, 1)


def _match_size(src, H, W):
    """把 (B,H,W,C) 缩放到目标 H,W（双线性）。"""
    if src.shape[1] == H and src.shape[2] == W:
        return src
    x = src.permute(0, 3, 1, 2)
    x = F.interpolate(x, size=(H, W), mode="bilinear", align_corners=False)
    return x.permute(0, 2, 3, 1)


def _split_rgba(img, mask, H, W, B):
    """从 IMAGE(+可选 MASK) 取出 (rgb, alpha)，对齐到 (B,H,W,*)。"""
    img = _match_size(img, H, W)
    rgb = img[..., :3]
    if mask is not None:
        a = mask.float().clamp(0.0, 1.0).to(img.device)
        if a.ndim == 2:
            a = a.unsqueeze(0)
        a = a.unsqueeze(1)                                   # (Bm,1,Hm,Wm)
        if a.shape[2] != H or a.shape[3] != W:
            a = F.interpolate(a, size=(H, W), mode="bilinear", align_corners=False)
        a = a.permute(0, 2, 3, 1)                            # (Bm,H,W,1)
    elif img.shape[3] == 4:
        a = img[..., 3:4]
    else:
        a = torch.ones(img.shape[0], H, W, 1, device=img.device)

    # 批次对齐到 B
    if rgb.shape[0] == 1 and B > 1:
        rgb = rgb.expand(B, -1, -1, -1)
    if a.shape[0] == 1 and B > 1:
        a = a.expand(B, -1, -1, -1)
    return rgb, a


class CherryBlurUnderComposite:
    """
    Cherry - 透明图模糊自叠加

    只需一张「扣完的图」（RGBA）：内部复制一份做高斯模糊垫在底层，
    清晰原图叠在上方，做出柔光/虚化光晕。
    """

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "扣完的图": ("IMAGE", {"tooltip": "一张「扣完的图」（RGBA）。内部会复制一份模糊后垫底，清晰原图叠在上方"}),
                "模糊半径": ("INT", {"default": 10, "min": 0, "max": 200, "step": 1,
                                     "tooltip": "底层高斯模糊半径，越大光晕越宽。默认 10"}),
                "模糊强度": ("FLOAT", {"default": 5.0, "min": 0.1, "max": 100.0, "step": 0.1,
                                       "tooltip": "高斯 sigma，越大越柔越散"}),
            },
            "optional": {
                "蒙版": ("MASK", {"tooltip": "可选。扣图 alpha；接了优先于图像自带 alpha 通道"}),
            },
        }

    RETURN_TYPES = ("IMAGE", "MASK")
    RETURN_NAMES = ("图像(RGBA)", "蒙版")
    FUNCTION = "blur_under_composite"
    CATEGORY = "Cherry_lizi"

    def blur_under_composite(self, 扣完的图, 模糊半径, 模糊强度, 蒙版=None):
        img = 扣完的图.float().clamp(0.0, 1.0)
        B, H, W = img.shape[0], img.shape[1], img.shape[2]
        rgb, a = _split_rgba(img, 蒙版, H, W, B)

        radius, sigma = int(模糊半径), float(模糊强度)

        # 底层 = 同一张图的模糊版；预乘 alpha 后模糊，避免透明区 RGB 渗边
        premult = torch.cat([rgb * a, a], dim=-1)
        blur = _gaussian_blur(premult, radius, sigma)
        bb_a = blur[..., 3:4].clamp(0.0, 1.0)
        bb_rgb = torch.where(bb_a > 1e-6, blur[..., :3] / bb_a.clamp(min=1e-6),
                             torch.zeros_like(blur[..., :3]))

        # alpha-over：清晰原图盖在模糊底层上
        out_a = a + bb_a * (1.0 - a)
        out_premult = rgb * a + bb_rgb * bb_a * (1.0 - a)
        out_rgb = torch.where(out_a > 1e-6, out_premult / out_a.clamp(min=1e-6),
                              torch.zeros_like(out_premult))

        out = torch.cat([out_rgb, out_a], dim=-1).clamp(0.0, 1.0)
        mask = out_a[..., 0].clamp(0.0, 1.0)
        return (out, mask)


NODE_CLASS_MAPPINGS = {
    "CherryBlurUnderComposite": CherryBlurUnderComposite,
}

NODE_DISPLAY_NAME_MAPPINGS = {
    "CherryBlurUnderComposite": "Cherry - 透明图模糊自叠加",
}
