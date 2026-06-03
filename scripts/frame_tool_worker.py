from __future__ import annotations

import json
import sys
from pathlib import Path


def main() -> int:
    config_path = Path(sys.argv[1]).resolve()
    tool_dir = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else config_path.parent
    sys.path.insert(0, str(tool_dir))
    from workflow import Workflow, load_config

    cfg = load_config(str(config_path))

    def log(message: str) -> None:
        print(json.dumps({"event": "log", "message": str(message)}, ensure_ascii=False), flush=True)

    wf = Workflow(cfg, logger=log)
    wf.run()
    print(json.dumps({"event": "finished"}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
