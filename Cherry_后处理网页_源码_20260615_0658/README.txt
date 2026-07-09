Cherry 后处理网页 源码
=======================

核心文件：
  web_temporal_smooth.py  —— 全部算法 + 网页HTML + run_pipeline 流水线
                             (可独立运行: python web_temporal_smooth.py [端口])
  __init__.py             —— 末尾把网页挂到 ComfyUI 服务 /cherry/postprocess

各步算法的 ComfyUI 节点版(可单独参考)：
  node_alpha_denoise.py / node_blur_under_composite.py /
  node_ps_resize.py / node_sharpen.py / node_temporal_smooth.py

Z字形流程顺序：去噪 → 模糊自叠加 → 缩小① → 锐化① → 缩小② → 锐化②
访问地址：<ComfyUI地址>/cherry/postprocess  例如 http://10.3.2.59:49255/cherry/postprocess
