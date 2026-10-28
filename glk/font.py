"""字体处理：从 Godot 的 .fontdata 中取出原始字体，按字符集裁剪。

.fontdata 是 Godot 导入缓存，形如 [元数据头][原始 TTF/OTF 数据]，各版本头部长度不固定，
因此按版本 signature 定位起点，再校验 sfnt 表数量是否自洽。
"""

import struct

SFNT_SIGNATURES = (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1")


class FontError(ValueError):
    pass


def _sfnt_table_count(data, offset):
    if len(data) < offset + 12:
        return -1
    return struct.unpack_from(">H", data, offset + 4)[0]


def carve(data):
    """返回 .fontdata 里内嵌的字体字节；找不到时抛 FontError。"""
    for signature in SFNT_SIGNATURES:
        start = 0
        while True:
            idx = data.find(signature, start)
            if idx < 0:
                break
            tables = _sfnt_table_count(data, idx)
            if 0 < tables < 100:
                return data[idx:]
            start = idx + 1
    raise FontError("没有找到内嵌的 TTF/OTF 数据")


def info(font_path):
    from fontTools.ttLib import TTFont

    font = TTFont(font_path, lazy=True)
    cmap = font.getBestCmap() or {}
    cjk = [code for code in cmap if 0x4E00 <= code <= 0x9FFF]
    name = "?"
    if "name" in font:
        name = font["name"].getDebugName(1) or "?"
    font.close()
    return {"family": name, "glyphs": len(cmap), "cjk": len(cjk)}


def subset(src_path, out_path, text=None, unicodes=None):
    from fontTools import subset as fts
    from fontTools.ttLib import TTFont

    font = TTFont(src_path)
    options = fts.Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.notdef_outline = True
    subsetter = fts.Subsetter(options=options)
    if unicodes:
        subsetter.populate(unicodes=unicodes)
    elif text:
        subsetter.populate(text=text)
    else:
        raise FontError("需要 text 或 unicodes")
    subsetter.subset(font)
    font.save(out_path)
    font.close()
    return out_path
