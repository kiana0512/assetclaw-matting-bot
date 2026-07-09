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
def temporal_smooth(imgs, 窗口大小=5, 平滑强度=1.0, 同步修正RGB=False, 最小Alpha保护=0.05):
    imgs = imgs.float().clamp(0, 1)
    B, H, W, C = imgs.shape
    if C < 4: return imgs
    r = (窗口大小 - 1) // 2
    coords = torch.arange(-r, r+1, dtype=torch.float32)
    wb = torch.exp(-0.5 * (coords / max(float(平滑强度), 0.1)) ** 2)
    rgb, alpha = imgs[..., :3], imgs[..., 3]
    ao, ro = torch.zeros_like(alpha), rgb.clone()
    for t in range(B):
        s, e = max(0, t-r), min(B, t+r+1)
        ws = s-(t-r); w = wb[ws:ws+(e-s)]; w = w/w.sum()
        ao[t] = (alpha[s:e] * w.view(-1,1,1)).sum(0)
        if 同步修正RGB:
            # 预乘 alpha 再平均，避免半透明边缘混入背景色
            a_slice = alpha[s:e].unsqueeze(-1)           # (n,H,W,1)
            pre = rgb[s:e] * a_slice                     # 预乘
            pre_avg = (pre * w.view(-1,1,1,1)).sum(0)    # 加权平均预乘RGB
            a_avg = ao[t].unsqueeze(-1).clamp(min=1e-6)
            ra = (pre_avg / a_avg).clamp(0, 1)           # 反预乘
            mask = (ao[t] >= float(最小Alpha保护)).unsqueeze(-1)
            ro[t] = torch.where(mask, ra, rgb[t])
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
<p class="sub">拖入文件夹批量处理，支持时序 Alpha 平滑 + 锐化，可单独或组合使用</p>

<div id="dropzone">
  <div id="drop-icon">🗂️</div>
  <div id="drop-text">拖入帧文件夹，或点击选择文件</div>
  <div id="drop-count"></div>
  <label class="btn-select" for="file-input">选择文件夹 / 多个文件</label>
  <input type="file" id="file-input" multiple accept="image/png,image/webp,image/tiff">
</div>

<!-- 模块1：时序平滑 -->
<div class="module active" id="mod-smooth">
  <div class="module-header" onclick="toggleModule('smooth')">
    <span class="module-title">① 时序 Alpha 平滑</span>
    <span style="font-size:.8rem;color:#888;flex:1">消除帧间 alpha 闪烁</span>
    <div class="toggle-pill on" id="pill-smooth"></div>
  </div>
  <div class="module-body">
    <div class="param-row">
      <label>窗口大小</label>
      <input type="range" id="p-window" min="3" max="11" step="2" value="5">
      <span class="val" id="v-window">5</span>
    </div>
    <div class="tip">覆盖 ±(窗口-1)/2 帧；5帧@24fps ≈ ±83ms</div>
    <div class="param-row">
      <label>平滑强度</label>
      <input type="range" id="p-sigma" min="0.1" max="3.0" step="0.1" value="1.0">
      <span class="val" id="v-sigma">1.0</span>
    </div>
    <div class="tip">高斯 sigma；值越大邻帧权重越均匀</div>
    <div class="param-row">
      <label>最小 Alpha</label>
      <input type="range" id="p-minalpha" min="0.01" max="0.30" step="0.01" value="0.05">
      <span class="val" id="v-minalpha">0.05</span>
    </div>
    <div class="tip">低于此值的像素跳过 RGB 平滑</div>
    <div class="param-row" style="margin-top:14px">
      <input type="checkbox" id="p-syncrgb" checked>
      <span class="check-label">同步平滑 RGB（开启产生动态模糊效果，关闭仅平滑 Alpha）</span>
    </div>
  </div>
</div>

<!-- 模块2：缩放 -->
<div class="module active" id="mod-resize">
  <div class="module-header" onclick="toggleModule('resize')">
    <span class="module-title">② 缩放</span>
    <span style="font-size:.8rem;color:#888;flex:1">PS Bicubic Sharper，等比缩小，透明区填充</span>
    <div class="toggle-pill on" id="pill-resize"></div>
  </div>
  <div class="module-body">
    <div class="param-row">
      <label>目标宽度</label>
      <input type="number" id="p-rw" value="384" min="64" max="8192" step="2"
        style="width:90px;background:#1f1840;border:1px solid #4a3580;border-radius:6px;
               color:#c9a0ff;padding:4px 8px;font-size:.9rem">
      <span style="font-size:.85rem;color:#888">px</span>
    </div>
    <div class="param-row" style="margin-top:10px">
      <label>目标高度</label>
      <input type="number" id="p-rh" value="512" min="64" max="8192" step="2"
        style="width:90px;background:#1f1840;border:1px solid #4a3580;border-radius:6px;
               color:#c9a0ff;padding:4px 8px;font-size:.9rem">
      <span style="font-size:.85rem;color:#888">px</span>
    </div>
    <div class="tip" style="padding-left:0;margin-top:8px">等比缩放，多余空间透明填充；缩小用 area 插值，放大用 bicubic</div>
  </div>
</div>

<!-- 模块3：锐化 -->
<div class="module active" id="mod-sharp">
  <div class="module-header" onclick="toggleModule('sharp')">
    <span class="module-title">③ 锐化</span>
    <span style="font-size:.8rem;color:#888;flex:1">USM 反锐化掩模，透明区自动保护</span>
    <div class="toggle-pill on" id="pill-sharp"></div>
  </div>
  <div class="module-body">
    <div class="param-row">
      <label>强度</label>
      <input type="range" id="p-sharp-amount" min="0.1" max="5.0" step="0.1" value="2.0">
      <span class="val" id="v-sharp-amount">2.0</span>
    </div>
    <div class="tip">1.0 = 标准；2.0 = 明显；0.5 = 轻微</div>
    <div class="param-row">
      <label>半径</label>
      <input type="range" id="p-sharp-radius" min="1" max="8" step="1" value="2">
      <span class="val" id="v-sharp-radius">2</span>
    </div>
    <div class="tip">模糊半径；发丝细节建议 1~2</div>
    <div class="param-row">
      <label>阈值</label>
      <input type="range" id="p-sharp-thresh" min="0.0" max="0.3" step="0.005" value="0.02">
      <span class="val" id="v-sharp-thresh">0.020</span>
    </div>
    <div class="tip">差异低于此值不锐化，保护平坦区域</div>
    <div class="param-row">
      <label>内缩像素</label>
      <input type="range" id="p-sharp-shrink" min="0" max="50" step="1" value="4">
      <span class="val" id="v-sharp-shrink">4</span>
    </div>
    <div class="tip">对 alpha 腐蚀 N 像素，只锐化实心内部，边缘半透明区不参与 → 动画不闪烁</div>
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
const moduleState = { smooth: true, resize: true, sharp: true };

function toggleModule(id) {
  moduleState[id] = !moduleState[id];
  document.getElementById('mod-'+id).classList.toggle('active', moduleState[id]);
  document.getElementById('pill-'+id).classList.toggle('on', moduleState[id]);
  updateBtn();
}

function updateBtn() {
  const hasFiles = collectedFiles.length > 0;
  const hasStep  = moduleState.smooth || moduleState.resize || moduleState.sharp;
  document.getElementById('btn-process').disabled = !(hasFiles && hasStep);
}

// ── 滑块联动 ──────────────────────────────────────────────────────────────
function linkSlider(id, valId, dec=0) {
  const el=document.getElementById(id), vl=document.getElementById(valId);
  el.addEventListener('input',()=>{ vl.textContent=parseFloat(el.value).toFixed(dec); });
}
linkSlider('p-window','v-window',0);
linkSlider('p-sigma','v-sigma',1);
linkSlider('p-minalpha','v-minalpha',2);
linkSlider('p-sharp-amount','v-sharp-amount',1);
linkSlider('p-sharp-radius','v-sharp-radius',0);
linkSlider('p-sharp-thresh','v-sharp-thresh',3);
linkSlider('p-sharp-shrink','v-sharp-shrink',0);

// ── 文件收集 ──────────────────────────────────────────────────────────────
let collectedFiles = [], resultBlob = null;
const dropzone = document.getElementById('dropzone');
const fileInput = document.getElementById('file-input');

async function traverseEntry(entry) {
  if (entry.isFile) return new Promise(r=>entry.file(f=>r([f])));
  if (entry.isDirectory) {
    const reader = entry.createReader(), all = [];
    await new Promise(r=>{ function read(){ reader.readEntries(res=>{ if(!res.length){r();return;} all.push(...res);read(); }); } read(); });
    return (await Promise.all(all.map(traverseEntry))).flat();
  }
  return [];
}

function setFiles(files) {
  collectedFiles = files.filter(f=>/\.(png|webp|tif|tiff)$/i.test(f.name))
    .sort((a,b)=>a.name.localeCompare(b.name,undefined,{numeric:true}));
  document.getElementById('drop-count').textContent =
    collectedFiles.length ? `已识别 ${collectedFiles.length} 张图片` : '未找到图片，请重新拖入';
  dropzone.classList.toggle('has-files', collectedFiles.length>0);
  document.getElementById('result').style.display='none';
  resultBlob=null;
  updateBtn();
}

dropzone.addEventListener('dragover', e=>{e.preventDefault();dropzone.classList.add('over');});
dropzone.addEventListener('dragleave', ()=>dropzone.classList.remove('over'));
dropzone.addEventListener('drop', async e=>{
  e.preventDefault(); dropzone.classList.remove('over');
  const all = await Promise.all([...e.dataTransfer.items].map(i=>traverseEntry(i.webkitGetAsEntry())));
  setFiles(all.flat());
});
fileInput.addEventListener('change', ()=>setFiles([...fileInput.files]));
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
  fd.append('use_smooth',   moduleState.smooth?'1':'0');
  fd.append('use_resize',   moduleState.resize?'1':'0');
  fd.append('resize_w',     document.getElementById('p-rw').value);
  fd.append('resize_h',     document.getElementById('p-rh').value);
  fd.append('use_sharp',    moduleState.sharp?'1':'0');
  fd.append('window_size',  document.getElementById('p-window').value);
  fd.append('sigma',        document.getElementById('p-sigma').value);
  fd.append('min_alpha',    document.getElementById('p-minalpha').value);
  fd.append('sync_rgb',     document.getElementById('p-syncrgb').checked?'1':'0');
  fd.append('sharp_amount', document.getElementById('p-sharp-amount').value);
  fd.append('sharp_radius', document.getElementById('p-sharp-radius').value);
  fd.append('sharp_thresh', document.getElementById('p-sharp-thresh').value);
  fd.append('sharp_shrink', document.getElementById('p-sharp-shrink').value);

  let up=0;
  for(const f of collectedFiles){
    fd.append('images',f,f.name); up++;
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


@app.route("/process", methods=["POST"])
def process():
    try:
        use_smooth   = request.form.get("use_smooth",  "1") == "1"
        use_resize   = request.form.get("use_resize",  "0") == "1"
        resize_w     = int(request.form.get("resize_w", 384))
        resize_h     = int(request.form.get("resize_h", 512))
        use_sharp    = request.form.get("use_sharp",   "0") == "1"
        window_size  = int(request.form.get("window_size", 5))
        sigma        = float(request.form.get("sigma", 1.0))
        min_alpha    = float(request.form.get("min_alpha", 0.05))
        sync_rgb     = request.form.get("sync_rgb", "0") == "1"
        sharp_amount = float(request.form.get("sharp_amount", 1.0))
        sharp_radius = int(request.form.get("sharp_radius", 2))
        sharp_thresh = float(request.form.get("sharp_thresh", 0.02))
        sharp_shrink = int(request.form.get("sharp_shrink", 4))

        files = sorted(request.files.getlist("images"), key=lambda f: f.filename)
        if not files:
            return jsonify(error="没有收到图片"), 400

        frames, names = [], []
        for f in files:
            frames.append(decode(f.read()))
            names.append(os.path.basename(f.filename))

        batch = torch.from_numpy(
            np.stack(frames, axis=0).astype(np.float32) / 255.0
        )

        def mean_flicker(t):
            if t.shape[0] < 2: return 0.0
            return float((t[...,3][1:] - t[...,3][:-1]).abs().mean())

        flicker_before = mean_flicker(batch)
        steps = []

        if use_smooth:
            batch = temporal_smooth(batch, window_size, sigma, sync_rgb, min_alpha)
            steps.append("时序平滑")

        if use_resize:
            batch = ps_bicubic_sharper(batch, resize_w, resize_h)
            steps.append(f"缩放→{resize_w}×{resize_h}")

        if use_sharp:
            batch = sharpen(batch, sharp_amount, sharp_radius, sharp_thresh, sharp_shrink)
            steps.append("锐化")

        flicker_after = mean_flicker(batch)
        reduction_pct = (1 - flicker_after / max(flicker_before, 1e-9)) * 100

        result_u8 = (batch.numpy() * 255).clip(0, 255).astype(np.uint8)
        zip_buf = io.BytesIO()
        with zipfile.ZipFile(zip_buf, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
            for i, name in enumerate(names):
                zf.writestr(name, encode(result_u8[i]))
        zip_buf.seek(0)

        resp = send_file(zip_buf, mimetype="application/zip",
                         as_attachment=True, download_name="cherry_processed.zip")
        resp.headers["X-Stats"] = json.dumps({
            "flicker_before": flicker_before,
            "flicker_after":  flicker_after,
            "reduction_pct":  reduction_pct,
            "steps": " → ".join(steps) if steps else "无",
        })
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
