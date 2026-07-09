"""命令行运行入口（无界面 / 服务器环境用）。

用法：
  python run.py                # 使用同目录 config.json
  python run.py my_config.json # 指定配置文件
"""

import os
import sys

from workflow import Workflow, load_config

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "config.json")
    if not os.path.exists(cfg_path):
        print(f"找不到配置文件: {cfg_path}\n请先复制 config.example.json 为 config.json 并填写凭证。")
        sys.exit(1)
    cfg = load_config(cfg_path)
    wf = Workflow(cfg, logger=print)
    wf.run()


if __name__ == "__main__":
    main()
