"""Godot project.binary（ECFG 容器）的读写。

格式：magic "ECFG" | entry_count(u32) | 重复 [key_len(u32) key(utf8) value_len(u32) value(Variant)]
"""

import struct

MAGIC = b"ECFG"

TYPE_BOOL = 1
TYPE_INT = 2
TYPE_FLOAT = 3
TYPE_STRING = 4

TYPE_NAMES = {TYPE_BOOL: "bool", TYPE_INT: "int", TYPE_FLOAT: "float", TYPE_STRING: "string"}


class FormatError(ValueError):
    pass


def _read_string(buf, pos):
    n = struct.unpack_from("<I", buf, pos)[0]
    pos += 4
    end = pos + n
    if end > len(buf):
        raise FormatError("字符串越界：offset %d, len %d" % (pos, n))
    return buf[pos:end].decode("utf-8", "replace"), end


def parse(data):
    if data[:4] != MAGIC:
        raise FormatError("不是 project.binary：缺少 ECFG 头")
    count = struct.unpack_from("<I", data, 4)[0]
    pos = 8
    entries = []
    for _ in range(count):
        key, pos = _read_string(data, pos)
        vlen = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        entries.append([key, data[pos:pos + vlen]])
        pos += vlen
    if pos != len(data):
        raise FormatError("解析结束位置 %d 与文件长度 %d 不一致" % (pos, len(data)))
    return entries


def build(entries):
    out = bytearray(MAGIC)
    out += struct.pack("<I", len(entries))
    for key, value in entries:
        raw = key.encode("utf-8")
        out += struct.pack("<I", len(raw)) + raw
        out += struct.pack("<I", len(value)) + value
    return bytes(out)


def _string_variant(s):
    raw = s.encode("utf-8")
    return struct.pack("<II", TYPE_STRING, len(raw)) + raw + b"\x00" * (-len(raw) % 4)


def string(s):
    return _string_variant(s)


def boolean(value):
    return struct.pack("<II", TYPE_BOOL, 1 if value else 0)


def int32(value):
    return struct.pack("<Ii", TYPE_INT, value)


def float32(value):
    return struct.pack("<If", TYPE_FLOAT, value)


def decode(value):
    if len(value) < 8:
        raise FormatError("Variant 长度不足：%d" % len(value))
    kind = struct.unpack_from("<I", value, 0)[0] & 0xFF
    if kind == TYPE_STRING:
        n = struct.unpack_from("<I", value, 4)[0]
        return "string", value[8:8 + n].decode("utf-8", "replace")
    if kind == TYPE_BOOL:
        return "bool", bool(struct.unpack_from("<I", value, 4)[0])
    if kind == TYPE_INT:
        return "int", struct.unpack_from("<i", value, 4)[0]
    if kind == TYPE_FLOAT:
        return "float", struct.unpack_from("<f", value, 4)[0]
    return "type%d" % kind, value


def get(entries, key, default=None):
    for k, v in entries:
        if k == key:
            return v
    return default


def set(entries, key, value):
    for entry in entries:
        if entry[0] == key:
            entry[1] = value
            return entries
    entries.append([key, value])
    return entries


def remove(entries, key):
    return [entry for entry in entries if entry[0] != key]
