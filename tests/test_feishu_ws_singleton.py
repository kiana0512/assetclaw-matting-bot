from pathlib import Path

from assetclaw_matting.feishu.ws_receiver import (
    _acquire_instance_lock,
    _release_instance_lock,
)


def test_ws_receiver_instance_lock_rejects_duplicate(tmp_path: Path) -> None:
    path = tmp_path / "feishu_ws.lock"
    first = _acquire_instance_lock(path)
    assert first is not None
    try:
        second = _acquire_instance_lock(path)
        assert second is None
    finally:
        _release_instance_lock(first)

    third = _acquire_instance_lock(path)
    assert third is not None
    _release_instance_lock(third)
