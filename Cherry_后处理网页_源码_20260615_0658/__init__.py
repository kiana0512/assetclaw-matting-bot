import os, json, time

NODE_CLASS_MAPPINGS        = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

def _try_import(module_name):
    try:
        import importlib
        mod = importlib.import_module(f".{module_name}", package=__name__)
        NODE_CLASS_MAPPINGS.update(getattr(mod, "NODE_CLASS_MAPPINGS", {}))
        NODE_DISPLAY_NAME_MAPPINGS.update(getattr(mod, "NODE_DISPLAY_NAME_MAPPINGS", {}))
    except Exception as e:
        print(f"[Cherry_lizi] ⚠️  {module_name} 加载失败: {e}")

_try_import("node_gradient_mask")
_try_import("node_gradient_gen")
_try_import("node_gradient_bank")
_try_import("node_threshold_clean")
_try_import("node_image_resize")
_try_import("node_folder_io")
_try_import("node_utility")
_try_import("node_scene_levels")
_try_import("node_keying_extras")
_try_import("node_composite")
_try_import("node_composite2")
_try_import("node_align_pair")
_try_import("node_batch_rename")
_try_import("node_pair_split")
_try_import("node_flatten_alpha")
_try_import("node_trim_border")
_try_import("node_easy_size")
_try_import("node_tile_1024")
_try_import("node_color_match_alpha")
_try_import("node_item_shadow")
_try_import("node_depth_shadow")
_try_import("node_holdout_simple")
_try_import("node_item_color_restore")
_try_import("node_alpha_denoise")
_try_import("node_inner_outer_blend")
_try_import("node_green_holdout")
_try_import("node_white_normalize")
_try_import("node_smart_resize")
_try_import("node_temporal_smooth")
_try_import("node_black_unpremultiply")
_try_import("node_sharpen")
_try_import("node_blur_stack")
_try_import("node_blur_under_composite")
_try_import("node_ps_resize")

WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS"]

# ── 文件级渐变历史 API ────────────────────────────────────────────────
_HISTORY_FILE = os.path.join(os.path.dirname(__file__), "gradient_history.json")


def _load_history() -> list:
    try:
        with open(_HISTORY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_history(data: list) -> None:
    with open(_HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


try:
    from server import PromptServer
    from aiohttp import web

    @PromptServer.instance.routes.get("/cherry/gradient_history")
    async def _gh_get(request):
        return web.json_response(_load_history())

    @PromptServer.instance.routes.post("/cherry/gradient_history")
    async def _gh_post(request):
        try:
            body = await request.json()
            hist = _load_history()
            hist.insert(0, {
                "name":  body.get("name", "未命名"),
                "stops": body.get("stops", []),
                "time":  time.time(),
            })
            hist = hist[:200]
            _save_history(hist)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    @PromptServer.instance.routes.delete("/cherry/gradient_history/{idx}")
    async def _gh_delete(request):
        try:
            idx  = int(request.match_info["idx"])
            hist = _load_history()
            if 0 <= idx < len(hist):
                hist.pop(idx)
                _save_history(hist)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

    # ── 可视化裁剪：自动主体检测 ──────────────────────────────────────
    @PromptServer.instance.routes.post("/cherry/detect_crop")
    async def _detect_crop(request):
        try:
            import folder_paths
            import numpy as np
            from PIL import Image

            body     = await request.json()
            uid      = str(body.get("unique_id", ""))
            crop_w   = int(body.get("crop_w", 512))
            crop_h   = int(body.get("crop_h", 512))
            padding  = float(body.get("padding", 0.15))   # 边距比例

            src_name = f"cherry_crop_src_{uid}.png"
            src_path = os.path.join(folder_paths.get_temp_directory(), src_name)
            if not os.path.exists(src_path):
                return web.json_response(
                    {"ok": False, "error": "源图像未找到，请先运行一次节点"})

            img = Image.open(src_path)
            iw, ih = img.size

            # ── 生成主体 mask ──────────────────────────────────────────
            if img.mode == "RGBA":
                alpha = np.array(img)[:, :, 3]
                mask  = alpha > 20
            else:
                arr  = np.array(img.convert("RGB")).astype(np.float32)
                # 取四角采样背景色
                cs   = max(4, min(iw, ih) // 20)
                bg   = np.concatenate([
                    arr[:cs, :cs].reshape(-1, 3),
                    arr[:cs, -cs:].reshape(-1, 3),
                    arr[-cs:, :cs].reshape(-1, 3),
                    arr[-cs:, -cs:].reshape(-1, 3),
                ]).mean(axis=0)
                diff = np.abs(arr - bg).mean(axis=2)
                mask = diff > 18   # 亮度差阈值

            if not mask.any():
                return web.json_response(
                    {"ok": True, "offset_x": 0, "offset_y": 0, "scale": 1.0,
                     "msg": "未检测到主体，已居中"})

            rows = np.where(mask.any(axis=1))[0]
            cols = np.where(mask.any(axis=0))[0]
            rmin, rmax = int(rows[0]),  int(rows[-1])
            cmin, cmax = int(cols[0]),  int(cols[-1])

            cx_sub  = (cmin + cmax) / 2.0
            cy_sub  = (rmin + rmax) / 2.0
            sub_w   = cmax - cmin + 1
            sub_h   = rmax - rmin + 1

            offset_x = round(cx_sub - iw / 2)
            offset_y = round(cy_sub - ih / 2)

            # 计算缩放：让主体 + padding 恰好填满输出尺寸
            long_side  = max(sub_w, sub_h)
            out_short  = min(crop_w, crop_h)
            scale      = round(
                max(0.1, min(10.0, out_short / (long_side * (1 + 2 * padding)))),
                2)

            # ── 边界修正 ───────────────────────────────────────────────
            # 采样区域尺寸（图像像素）= 输出尺寸 / 缩放
            sW = crop_w / scale
            sH = crop_h / scale

            # 若采样区域本身比图像还大，强制放大缩放使其缩小
            if sW > iw:
                scale = round(crop_w / iw, 2)
            if sH > ih:
                scale = round(max(scale, crop_h / ih), 2)
            sW = crop_w / scale
            sH = crop_h / scale

            # 把裁剪中心夹紧到图像内部，保证四边都不越界
            cx = iw / 2 + offset_x
            cy = ih / 2 + offset_y
            cx = max(sW / 2, min(iw - sW / 2, cx))
            cy = max(sH / 2, min(ih - sH / 2, cy))
            offset_x = round(cx - iw / 2)
            offset_y = round(cy - ih / 2)

            print(f"[Cherry 自动检测] 主体 bbox=({cmin},{rmin},{cmax},{rmax}) "
                  f"→ offset=({offset_x},{offset_y})  scale={scale}")
            return web.json_response(
                {"ok": True, "offset_x": offset_x, "offset_y": offset_y, "scale": scale})

        except Exception as e:
            return web.json_response({"ok": False, "error": str(e)}, status=400)

except Exception as _e:
    print(f"[Cherry_lizi] 无法注册历史记录 API 路由: {_e}")


# ── 后处理网页：挂到 ComfyUI 服务上，复用 ComfyUI 端口（无需另开端口）──────────
try:
    import asyncio as _asyncio
    import functools as _functools
    import json as _pp_json
    import traceback as _pp_tb
    from server import PromptServer
    from aiohttp import web as _pp_web
    from .web_temporal_smooth import HTML as _PP_HTML, run_pipeline as _pp_run_pipeline

    # 页面里的提交地址从独立 Flask 的 /process 改成挂载后的子路径
    _PP_HTML_MOUNTED = _PP_HTML.replace("fetch('/process'", "fetch('/cherry/postprocess/run'")

    @PromptServer.instance.routes.get("/cherry/postprocess")
    async def _pp_page(request):
        return _pp_web.Response(text=_PP_HTML_MOUNTED, content_type="text/html")

    @PromptServer.instance.routes.post("/cherry/postprocess/run")
    async def _pp_process(request):
        try:
            reader = await request.multipart()
            form, files = {}, []
            async for part in reader:
                if part.name == "images":
                    data = await part.read(decode=False)
                    files.append((part.filename or "", bytes(data)))
                else:
                    raw = await part.read(decode=False)
                    form[part.name] = raw.decode("utf-8", "ignore")
            files.sort(key=lambda x: x[0])

            loop = _asyncio.get_event_loop()
            zip_bytes, stats = await loop.run_in_executor(
                None, _functools.partial(_pp_run_pipeline, form, files))

            safe_name = stats.get("folder_name", "").encode("ascii", "ignore").decode() or "cherry_processed"
            return _pp_web.Response(
                body=zip_bytes,
                headers={
                    "Content-Type": "application/zip",
                    "Content-Disposition": f'attachment; filename="{safe_name}.zip"',
                    "X-Stats": _pp_json.dumps(stats),   # ensure_ascii → header 安全
                    "Access-Control-Expose-Headers": "X-Stats",
                },
            )
        except Exception as e:
            _pp_tb.print_exc()
            return _pp_web.json_response({"error": str(e)}, status=500)

    print("[Cherry_lizi] ✅ 后处理网页已挂载: <ComfyUI地址>/cherry/postprocess")
except Exception as _e2:
    print(f"[Cherry_lizi] ⚠️  后处理网页挂载失败: {_e2}")
