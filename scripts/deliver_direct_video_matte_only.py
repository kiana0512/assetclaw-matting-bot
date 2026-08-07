from __future__ import annotations

import json
import sys

from assetclaw_matting.config import settings
from assetclaw_matting.db.schema import create_tables
from assetclaw_matting.db.sqlite import init_db
from assetclaw_matting.skills.direct_video_skills import deliver_matte_only


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: deliver_direct_video_matte_only.py VID_<id>", file=sys.stderr)
        return 2
    init_db(settings.data_db_path)
    create_tables()
    result = deliver_matte_only(sys.argv[1], resend=True)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
