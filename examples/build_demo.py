"""生成一个完全合成的演示包，跑通 glk 的完整流程。

只用到 Python 标准库和 fontTools，产物全部是造的假数据，不包含任何真实游戏内容。
"""

import os
import shutil
import struct
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(HERE, "out")
sys.path.insert(0, ROOT)

from glk import pck, projectbin  # noqa: E402

def run(*args):
    print("\n$ glk " + " ".join(args), flush=True)
    env = dict(os.environ, PYTHONPATH=ROOT)
    subprocess.run([sys.executable, "-m", "glk", *args], cwd=ROOT, env=env, check=True)


def ustring(text):
    raw = text.encode("utf-8") + b"\x00"
    return struct.pack("<I", len(raw)) + raw


def build_project_binary(path):
    entries = [
        ["application/config/name", projectbin.string("glk demo")],
        ["application/config/features", projectbin.string("4.4")],
        ["application/run/max_fps", projectbin.int32(60)],
        ["display/window/size/viewport_width", projectbin.int32(1280)],
        ["display/window/size/viewport_height", projectbin.int32(720)],
    ]
    with open(path, "wb") as f:
        f.write(projectbin.build(entries))


def build_font(path):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    order = [".notdef", "A", "uni4F60", "uni597D"]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({0x41: "A", 0x4F60: "uni4F60", 0x597D: "uni597D"})
    builder.setupGlyf({name: TTGlyphPen(None).glyph() for name in order})
    builder.setupHorizontalMetrics({name: (500, 0) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "GlkDemo", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    builder.save(path)


def build_fontdata(source, path):
    with open(source, "rb") as f:
        raw = f.read()
    with open(path, "wb") as f:
        f.write(b"\x00\x07\x00\x00" + b"\x00" * 92 + raw)


def build_pack(path):
    pack = pck.create(sparse=True, engine=(4, 4, 0))
    pack.add_file("project.binary", b"")
    pack.add_file("demo/hello.txt", "你好，世界\nHello, world\n".encode("utf-8"))
    pack.write(path)


def build_translation(path):
    messages = [
        ("Hello, world", "你好，世界"),
        ("Start Game", "开始游戏"),
        ("Your earned {gold} Gold.", "你获得了 {gold} 金币。"),
    ]
    data = b"GTRN" + struct.pack("<II", 1, len(messages))
    for msgid, msgstr in messages:
        for text in (msgid, msgstr):
            raw = text.encode("utf-8")
            data += struct.pack("<I", len(raw)) + raw
    with open(path, "wb") as f:
        f.write(data)


def build_apk(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("AndroidManifest.xml", b"\x03\x00\x08\x00" + b"\x00" * 64)
        z.writestr("classes.dex", b"dex\n035\x00" + b"\x00" * 512)
        z.writestr(zipfile.ZipInfo("lib/arm64-v8a/libdemo.so"), b"\x7fELF" + b"\x00" * 256,
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("assets/project.binary", b"")
        z.writestr("assets/assets.sparsepck", b"")
        z.writestr("assets/demo/hello.txt", "你好，世界\n".encode("utf-8"))
        z.writestr("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n")
        z.writestr("META-INF/CERT.RSA", b"demo-signature")


def make_sources():
    os.makedirs(os.path.join(OUT, "src"), exist_ok=True)
    build_project_binary(os.path.join(OUT, "src", "project.binary"))
    build_pack(os.path.join(OUT, "src", "assets.sparsepck"))
    build_translation(os.path.join(OUT, "src", "demo.zh.translation"))
    build_apk(os.path.join(OUT, "src", "demo.apk"))
    with open(os.path.join(OUT, "src", "entry.gd"), "w", encoding="utf-8") as f:
        f.write('extends Node\n\n\nfunc _ready() -> void:\n\tprint("glk demo loaded")\n')
    font = os.path.join(OUT, "src", "demo.ttf")
    build_font(font)
    build_fontdata(font, os.path.join(OUT, "src", "demo.ttf-1a2b3c.fontdata"))


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(os.path.join(OUT, "build"), exist_ok=True)
    make_sources()
    print("已生成合成素材：%s" % os.path.join(OUT, "src"), flush=True)

    run("pbin", "list", os.path.join(OUT, "src", "project.binary"))
    run("pck", "list", os.path.join(OUT, "src", "assets.sparsepck"))
    run("apk", "info", os.path.join(OUT, "src", "demo.apk"))
    run("tr", "list", os.path.join(OUT, "src", "demo.zh.translation"))

    run("font", "carve", os.path.join(OUT, "src", "demo.ttf-1a2b3c.fontdata"),
        "-o", os.path.join(OUT, "build", "carved.ttf"))
    run("font", "info", os.path.join(OUT, "build", "carved.ttf"))
    run("font", "subset", os.path.join(OUT, "build", "carved.ttf"), "--chars", "你好A",
        "-o", os.path.join(OUT, "build", "font.ttf"))

    run("pck", "add", os.path.join(OUT, "src", "assets.sparsepck"),
        "--file", "mod/entry.gd=%s" % os.path.join(OUT, "src", "entry.gd"),
        "--file", "mod/font.ttf=%s" % os.path.join(OUT, "build", "font.ttf"),
        "-o", os.path.join(OUT, "build", "assets.sparsepck"))
    run("pbin", "autoload", os.path.join(OUT, "src", "project.binary"), "DemoMod",
        "*res://mod/entry.gd", "-o", os.path.join(OUT, "build", "project.binary"))
    run("apk", "keygen", "-o", os.path.join(OUT, "keys"), "--cn", "glk demo")

    run("apk", "repack", os.path.join(OUT, "src", "demo.apk"),
        "-o", os.path.join(OUT, "build", "demo-signed.apk"),
        "--replace", "assets/project.binary=%s" % os.path.join(OUT, "build", "project.binary"),
        "--replace", "assets/assets.sparsepck=%s" % os.path.join(OUT, "build", "assets.sparsepck"),
        "--add", "assets/mod/entry.gd=%s" % os.path.join(OUT, "src", "entry.gd"),
        "--add", "assets/mod/font.ttf=%s" % os.path.join(OUT, "build", "font.ttf"),
        "--keydir", os.path.join(OUT, "keys"))

    run("apk", "verify", os.path.join(OUT, "build", "demo-signed.apk"),
        "--pub", os.path.join(OUT, "keys", "pub.der"))
    print("\n全部完成，产物在 %s" % os.path.join(OUT, "build"), flush=True)


if __name__ == "__main__":
    main()
