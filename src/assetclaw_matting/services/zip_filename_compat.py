from __future__ import annotations

import binascii
import struct
import zipfile


UNICODE_PATH_EXTRA_ID = 0x7075


def zip_member_name(info: zipfile.ZipInfo) -> str:
    """Return a ZIP member name using its Unicode metadata when available.

    Windows archive tools commonly store GBK bytes in the legacy filename
    field and the real UTF-8 name in Info-ZIP's Unicode Path extra field.
    Python 3.11 ignores that extra field and exposes CP437 mojibake.
    """

    raw_name = _legacy_name_bytes(info)
    unicode_name = _unicode_path_extra(info.extra, raw_name)
    if unicode_name:
        return unicode_name
    if info.flag_bits & 0x800:
        return info.filename
    if raw_name is not None:
        for encoding in ("utf-8", "gb18030"):
            try:
                return raw_name.decode(encoding)
            except UnicodeDecodeError:
                continue
    return info.filename


def _legacy_name_bytes(info: zipfile.ZipInfo) -> bytes | None:
    legacy_name = str(getattr(info, "orig_filename", "") or info.filename)
    try:
        return legacy_name.encode("cp437")
    except UnicodeEncodeError:
        return None


def _unicode_path_extra(extra: bytes, raw_name: bytes | None) -> str:
    offset = 0
    while offset + 4 <= len(extra):
        header_id, size = struct.unpack_from("<HH", extra, offset)
        offset += 4
        payload = extra[offset : offset + size]
        offset += size
        if header_id != UNICODE_PATH_EXTRA_ID or len(payload) < 6 or payload[0] != 1:
            continue
        expected_crc = struct.unpack_from("<I", payload, 1)[0]
        if raw_name is not None and expected_crc != (binascii.crc32(raw_name) & 0xFFFFFFFF):
            continue
        try:
            return payload[5:].decode("utf-8")
        except UnicodeDecodeError:
            continue
    return ""
