"""Godot 翻译资源读取：裸 GTRN 与 RSRC 封装（Translation / OptimizedTranslation）。

GTRN：magic "GTRN" | version(u32) | count(u32) | 重复 [msgid(长度+字节) msgstr(长度+字节)]

RSRC 里的 OptimizedTranslation 只保存哈希表和 smaz 压缩后的译文：msgid 被哈希掉了，
无法还原，因此这类文件只能列出译文本身。
"""

import struct
from dataclasses import dataclass, field

from . import rsrc, smaz

MAGIC = b"GTRN"


class FormatError(ValueError):
    pass


@dataclass
class TranslationSet:
    format: str
    locale: str
    messages: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def pairs(self):
        return [m for m in self.messages if m[0] is not None]


def parse_gtrn(data):
    if data[:4] != MAGIC:
        raise FormatError("不是 Godot 翻译文件：缺少 GTRN 头")
    version, count = struct.unpack_from("<II", data, 4)
    pos = 12
    messages = []
    for _ in range(count):
        n = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        msgid = data[pos:pos + n]
        pos += n
        m = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        msgstr = data[pos:pos + m]
        pos += m
        messages.append((_decode(msgid), _decode(msgstr)))
    return version, messages


def _decode(raw):
    return raw.rstrip(b"\x00").decode("utf-8", "replace")


def _optimized(resource):
    props = resource.properties
    locale = props.get("locale") or ""
    blob = props.get("strings") or b""
    bucket = props.get("bucket_table") or []
    messages = []
    pos = 0
    while pos + 2 <= len(bucket):
        count = bucket[pos]
        pos += 2
        if count < 0 or pos + count * 4 > len(bucket):
            raise FormatError("bucket_table 结构损坏：offset %d" % pos)
        for _ in range(count):
            offset, size, orig_len = bucket[pos + 1], bucket[pos + 2], bucket[pos + 3]
            pos += 4
            chunk = blob[offset:offset + size]
            # comp_size == uncomp_size 时存的是原文（含结尾 NUL），否则是 smaz 压缩流
            text = chunk if size == orig_len else smaz.decompress(chunk)[:orig_len]
            messages.append((None, text.split(b"\x00", 1)[0].decode("utf-8", "replace")))
    return TranslationSet(
        format="optimized_translation",
        locale=locale,
        messages=messages,
        notes=["msgid 在 OptimizedTranslation 中只保留哈希，无法还原"],
    )


def _plain(resource):
    props = resource.properties
    raw = props.get("messages") or {}
    messages = [(str(key), str(value)) for key, value in raw.items()]
    return TranslationSet(format="translation", locale=props.get("locale") or "", messages=messages)


def parse_rsrc(data):
    parsed = rsrc.parse(data)
    sets = []
    for resource in parsed.resources:
        if resource.type == "OptimizedTranslation":
            sets.append(_optimized(resource))
        elif resource.type == "Translation":
            sets.append(_plain(resource))
    if not sets:
        raise FormatError("资源里没有 Translation 或 OptimizedTranslation：%s" % parsed.type)
    return sets


def load(path):
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] == MAGIC:
        version, messages = parse_gtrn(data)
        return [TranslationSet(format="gtrn", locale="", messages=messages, notes=["GTRN 版本 %d" % version])]
    if data[:4] in (rsrc.MAGIC, rsrc.COMPRESSED_MAGIC):
        return parse_rsrc(data)
    raise FormatError("无法识别的翻译文件格式：%r" % bytes(data[:4]))
