from __future__ import annotations

import binascii
import struct
import zipfile

from assetclaw_matting.services.zip_filename_compat import zip_member_name


def _unicode_path_extra(raw_name: bytes, unicode_name: str) -> bytes:
    payload = bytes([1]) + struct.pack("<I", binascii.crc32(raw_name) & 0xFFFFFFFF) + unicode_name.encode("utf-8")
    return struct.pack("<HH", 0x7075, len(payload)) + payload


def test_uses_infozip_unicode_path_for_legacy_gbk_name() -> None:
    expected = "jennifer_第一状态_闭眼/0000.png"
    raw_name = expected.encode("gbk")
    mojibake = raw_name.decode("cp437")
    info = zipfile.ZipInfo(mojibake)
    info.extra = _unicode_path_extra(raw_name, expected)

    assert zip_member_name(info) == expected


def test_falls_back_to_gb18030_when_unicode_extra_is_absent() -> None:
    expected = "dino_第一状态_往左看/0001.png"
    info = zipfile.ZipInfo(expected.encode("gbk").decode("cp437"))

    assert zip_member_name(info) == expected


def test_keeps_utf8_flagged_name_unchanged() -> None:
    expected = "角色/表情.png"
    info = zipfile.ZipInfo(expected)
    info.flag_bits |= 0x800

    assert zip_member_name(info) == expected
