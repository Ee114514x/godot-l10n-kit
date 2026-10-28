import hashlib
import os
import struct
import tempfile
import unittest
import zipfile

from glk import apk, font, gdscript, pck, projectbin, rsrc, smaz, translation
from glk.cli import main


def make_apk(path):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("AndroidManifest.xml", b"\x03\x00\x08\x00manifest")
        z.writestr("classes.dex", b"dex\n035\x00" + b"x" * 2048)
        z.writestr(zipfile.ZipInfo("lib/arm64-v8a/libfoo.so"), b"\x7fELF" + b"y" * 512,
                   compress_type=zipfile.ZIP_STORED)
        z.writestr("assets/pack.bin", b"original-pack")
        z.writestr("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n")
        z.writestr("META-INF/CERT.RSA", b"fake-signature")


def make_font(path):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    order = [".notdef", "A", "uni4E2D"]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap({0x41: "A", 0x4E2D: "uni4E2D"})
    builder.setupGlyf({name: TTGlyphPen(None).glyph() for name in order})
    builder.setupHorizontalMetrics({name: (500, 0) for name in order})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "KitTest", "styleName": "Regular"})
    builder.setupOS2()
    builder.setupPost()
    builder.save(path)


class ProjectBinaryTest(unittest.TestCase):
    def test_roundtrip_types(self):
        entries = [
            ["application/config/name", projectbin.string("Demo")],
            ["autoload/Foo", projectbin.string("*res://foo.gd")],
            ["application/config/use_custom_user_dir", projectbin.boolean(True)],
            ["display/window/size/viewport_width", projectbin.int32(1280)],
            ["display/window/stretch/scale", projectbin.float32(1.5)],
        ]
        parsed = projectbin.parse(projectbin.build(entries))
        self.assertEqual([e[0] for e in parsed], [e[0] for e in entries])
        self.assertEqual(projectbin.decode(parsed[0][1]), ("string", "Demo"))
        self.assertEqual(projectbin.decode(parsed[2][1]), ("bool", True))
        self.assertEqual(projectbin.decode(parsed[3][1]), ("int", 1280))
        self.assertAlmostEqual(projectbin.decode(parsed[4][1])[1], 1.5, places=5)

    def test_string_padding(self):
        for text in ("", "a", "ab", "abc", "abcd", "\u6c49\u5b57"):
            value = projectbin.string(text)
            self.assertEqual(len(value) % 4, 0)
            self.assertEqual(projectbin.decode(value), ("string", text))

    def test_set_and_remove(self):
        entries = [["a", projectbin.string("1")]]
        projectbin.set(entries, "b", projectbin.string("2"))
        projectbin.set(entries, "a", projectbin.string("3"))
        self.assertEqual([e[0] for e in entries], ["a", "b"])
        self.assertEqual(len(projectbin.remove(entries, "a")), 1)

    def test_bad_magic(self):
        with self.assertRaises(projectbin.FormatError):
            projectbin.parse(b"NOPE" + b"\x00" * 8)


class PackTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def _path(self, name):
        return os.path.join(self.dir, name)

    def test_sparse_add(self):
        pack = pck.create(sparse=True)
        pack.add_file("mod/entry.gd", b"extends Node\n")
        path = self._path("pack.sparsepck")
        pack.write(path)

        loaded = pck.Pack.load(path)
        self.assertTrue(loaded.sparse)
        entry = loaded.find("mod/entry.gd")
        self.assertIsNotNone(entry)
        self.assertEqual(entry.offset, 0)
        self.assertEqual(entry.size, len(b"extends Node\n"))
        self.assertEqual(entry.md5, hashlib.md5(b"extends Node\n").digest())
        with self.assertRaises(pck.FormatError):
            loaded.read_file(entry)

    def test_packed_add_is_readable(self):
        pack = pck.create(sparse=False)
        pack.add_file("res/a.txt", b"hello")
        path = self._path("pack.pck")
        pack.write(path)

        loaded = pck.Pack.load(path)
        self.assertFalse(loaded.sparse)
        self.assertEqual(loaded.read_file(loaded.find("res/a.txt")), b"hello")

    def test_second_write_keeps_old_data(self):
        path = self._path("pack.pck")
        pack = pck.create(sparse=False)
        pack.add_file("res/a.txt", b"alpha")
        pack.write(path)

        reopened = pck.Pack.load(path)
        reopened.add_file("res/b.txt", b"beta-beta-beta")
        reopened.write(path)

        final = pck.Pack.load(path)
        self.assertEqual(final.read_file(final.find("res/a.txt")), b"alpha")
        self.assertEqual(final.read_file(final.find("res/b.txt")), b"beta-beta-beta")

    def test_replace_and_remove(self):
        path = self._path("pack.pck")
        pack = pck.create(sparse=False)
        pack.add_file("res/a.txt", b"alpha")
        pack.write(path)

        reopened = pck.Pack.load(path)
        reopened.add_file("res/a.txt", b"changed")
        reopened.remove_file("res/a.txt")
        reopened.write(path)

        final = pck.Pack.load(path)
        entry = final.find("res/a.txt")
        self.assertTrue(entry.flags & pck.FILE_REMOVAL)
        self.assertEqual(entry.size, 0)

    def test_bad_magic(self):
        path = self._path("bad.pck")
        with open(path, "wb") as f:
            f.write(b"NOPE" + b"\x00" * 64)
        with self.assertRaises(pck.FormatError):
            pck.Pack.load(path)


class ApkTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.src = os.path.join(self.dir, "base.apk")
        make_apk(self.src)
        self.out = os.path.join(self.dir, "out.apk")

    def test_repack_replaces_and_strips_signature(self):
        script = os.path.join(self.dir, "entry.gd")
        with open(script, "wb") as f:
            f.write(b"extends Node\n")
        pack = os.path.join(self.dir, "pack.bin")
        with open(pack, "wb") as f:
            f.write(b"replaced-pack" * 100)

        repacker = apk.Repacker(self.src)
        repacker.replace("assets/pack.bin", pack)
        repacker.add("assets/mod/entry.gd", script)
        repacker.write(self.out)

        with zipfile.ZipFile(self.out) as z:
            self.assertIsNone(z.testzip())
            names = set(z.namelist())
            self.assertNotIn("META-INF/CERT.RSA", names)
            self.assertNotIn("META-INF/MANIFEST.MF", names)
            self.assertEqual(z.read("assets/pack.bin"), b"replaced-pack" * 100)
            self.assertEqual(z.read("assets/mod/entry.gd"), b"extends Node\n")
            self.assertEqual(z.read("classes.dex"), b"dex\n035\x00" + b"x" * 2048)
            info = z.getinfo("lib/arm64-v8a/libfoo.so")
            self.assertEqual(info.compress_type, zipfile.ZIP_STORED)
            self.assertEqual(z.read("lib/arm64-v8a/libfoo.so"), b"\x7fELF" + b"y" * 512)
        with open(self.out, "rb") as f:
            f.seek(info.header_offset)
            header = f.read(30)
        signature = struct.unpack_from("<I", header, 0)[0]
        nlen, elen = struct.unpack_from("<HH", header, 26)
        self.assertEqual(signature, 0x04034B50)
        data_start = info.header_offset + 30 + nlen + elen
        self.assertEqual(data_start % 16384, 0)

    def test_sign_and_verify(self):
        repacker = apk.Repacker(self.src)
        repacker.write(self.out)
        key, cert, pub = apk.keygen(os.path.join(self.dir, "keys"))

        apk.sign_v2(self.out, key, cert, pub)
        result = apk.verify_v2(self.out, pub)
        self.assertTrue(result["signed"])
        self.assertTrue(result["digest_match"])
        self.assertTrue(result["signature_valid"])
        self.assertTrue(result["pubkey_match"])
        self.assertTrue(result["block_before_cd"])

        with zipfile.ZipFile(self.out) as z:
            self.assertIsNone(z.testzip())

    def test_compare(self):
        repacker = apk.Repacker(self.src)
        repacker.write(self.out)
        report = apk.compare(self.src, self.out)
        self.assertEqual(report["missing"], [])
        self.assertEqual(report["extra"], [])
        self.assertEqual(report["resized"], [])

    def test_unsigned_has_no_signature(self):
        repacker = apk.Repacker(self.src)
        repacker.write(self.out)
        self.assertFalse(apk.verify_v2(self.out, self.src)["signed"])


class FontTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.font_path = os.path.join(self.dir, "test.ttf")
        make_font(self.font_path)

    def test_carve(self):
        with open(self.font_path, "rb") as f:
            raw = f.read()
        padded = b"\x00" * 37 + raw
        self.assertEqual(font.carve(padded), raw)

    def test_carve_missing(self):
        with self.assertRaises(font.FontError):
            font.carve(b"\x00" * 64)

    def test_info(self):
        data = font.info(self.font_path)
        self.assertEqual(data["family"], "KitTest")
        self.assertEqual(data["cjk"], 1)

    def test_subset(self):
        out = os.path.join(self.dir, "sub.ttf")
        font.subset(self.font_path, out, text="A")
        data = font.info(out)
        self.assertEqual(data["cjk"], 0)
        self.assertEqual(data["glyphs"], 1)


def _ustring(text):
    raw = text.encode("utf-8") + b"\x00"
    return struct.pack("<I", len(raw)) + raw


def _variant_string(text):
    return struct.pack("<I", 5) + _ustring(text)


def _variant_bytes(blob):
    return struct.pack("<II", 31, len(blob)) + blob


def _variant_int32(values):
    return struct.pack("<II", 32, len(values)) + struct.pack("<%di" % len(values), *values)


def build_rsrc(type_name, properties, string_table):
    body = bytearray(_ustring(type_name))
    body += struct.pack("<I", len(properties))
    for name, value in properties:
        body += struct.pack("<I", string_table.index(name)) + value

    head = bytearray(b"RSRC")
    head += struct.pack("<II", 0, 0)
    head += struct.pack("<III", 4, 5, 6)
    head += _ustring(type_name)
    head += struct.pack("<Q", 0)
    head += struct.pack("<I", 3)
    head += struct.pack("<Q", 0)
    head += b"\x00" * (11 * 4)
    head += struct.pack("<I", len(string_table))
    for name in string_table:
        head += _ustring(name)
    head += struct.pack("<I", 0)
    head += struct.pack("<I", 1)
    head += _ustring("local://test") + struct.pack("<Q", 0)
    offset = len(head)
    head[-8:] = struct.pack("<Q", offset)
    return bytes(head + body)


class TranslationTest(unittest.TestCase):
    def test_gtrn(self):
        pairs = [("Hello", "\u4f60\u597d"), ("Start\nGame", "\u5f00\u59cb\n\u6e38\u620f")]
        data = b"GTRN" + struct.pack("<II", 1, len(pairs))
        for msgid, msgstr in pairs:
            for text in (msgid, msgstr):
                raw = text.encode("utf-8")
                data += struct.pack("<I", len(raw)) + raw
        version, messages = translation.parse_gtrn(data)
        self.assertEqual(version, 1)
        self.assertEqual(messages, pairs)

    def test_bad_magic(self):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "x.translation")
        with open(path, "wb") as f:
            f.write(b"XXXX" + b"\x00" * 8)
        with self.assertRaises(translation.FormatError):
            translation.load(path)

    def test_optimized_translation(self):
        strings = b"Hi\x00" + b"* Mercy\x00"
        props = [
            ("bucket_table", _variant_int32([2, 1, 12345, 0, 3, 3, 67890, 3, 8, 8])),
            ("strings", _variant_bytes(strings)),
        ]
        data = build_rsrc("OptimizedTranslation", props,
                          ["bucket_table", "strings", "locale"])
        sets = translation.parse_rsrc(data)
        self.assertEqual(len(sets), 1)
        item = sets[0]
        self.assertEqual(item.format, "optimized_translation")
        self.assertEqual([m[1] for m in item.messages], ["Hi", "* Mercy"])
        self.assertTrue(all(m[0] is None for m in item.messages))
        self.assertTrue(item.notes)

    def test_optimized_translation_smaz(self):
        compressed = b"\xfeH\x08\xfe\x00"
        props = [
            ("bucket_table", _variant_int32([1, 1, 7, 0, 5, 3])),
            ("strings", _variant_bytes(compressed)),
        ]
        data = build_rsrc("OptimizedTranslation", props, ["bucket_table", "strings"])
        self.assertEqual(translation.parse_rsrc(data)[0].messages[0][1], "Hi")

    def test_plain_translation(self):
        messages = struct.pack("<I", 1) + _variant_string("Start") + _variant_string("\u5f00\u59cb")
        props = [("messages", struct.pack("<I", 26) + messages)]
        data = build_rsrc("Translation", props, ["messages"])
        item = translation.parse_rsrc(data)[0]
        self.assertEqual(item.format, "translation")
        self.assertEqual(item.messages, [("Start", "\u5f00\u59cb")])

    def test_rsrc_unsupported_variant(self):
        props = [("weird", struct.pack("<I", 10) + b"\x00" * 8)]
        data = build_rsrc("Translation", props, ["weird"])
        with self.assertRaises(rsrc.FormatError):
            rsrc.parse(data)

    def test_compressed_resource(self):
        with self.assertRaises(rsrc.FormatError):
            rsrc.parse(b"RSCC" + b"\x00" * 16)


class SmazTest(unittest.TestCase):
    def test_verbatim(self):
        self.assertEqual(smaz.decompress(b"\xfeZ"), b"Z")
        self.assertEqual(smaz.decompress(b"\xff\x02abc"), b"abc")

    def test_codebook(self):
        self.assertEqual(smaz.decompress(b"\x00"), b" ")
        self.assertEqual(smaz.decompress(b"\x01"), b"the")

    def test_truncated(self):
        with self.assertRaises(smaz.SmazError):
            smaz.decompress(b"\xfe")
        with self.assertRaises(smaz.SmazError):
            smaz.decompress(b"\xff\x05ab")


class GdScriptTest(unittest.TestCase):
    def test_clean(self):
        problems, structure = gdscript.check('extends Node\n\nfunc _ready() -> void:\n\tprint("ok")\n')
        self.assertEqual(problems, [])
        self.assertEqual(structure, [])

    def test_unbalanced(self):
        problems, _ = gdscript.check("func f():\n\tvar a = [1, 2\n")
        self.assertTrue(problems)

    def test_space_indent(self):
        _, structure = gdscript.check("func f():\n    pass\n")
        self.assertTrue(structure)


class CliTest(unittest.TestCase):
    def test_pbin_autoload(self):
        directory = tempfile.mkdtemp()
        src = os.path.join(directory, "project.binary")
        out = os.path.join(directory, "out.binary")
        with open(src, "wb") as f:
            f.write(projectbin.build([["application/config/name", projectbin.string("Demo")]]))

        code = main(["pbin", "autoload", src, "L10n", "*res://mod/entry.gd", "-o", out])
        self.assertEqual(code, 0)
        with open(out, "rb") as f:
            entries = projectbin.parse(f.read())
        self.assertEqual(projectbin.get(entries, "autoload/L10n"), projectbin.string("*res://mod/entry.gd"))

    def test_pck_list(self):
        directory = tempfile.mkdtemp()
        path = os.path.join(directory, "p.pck")
        pack = pck.create(sparse=True)
        pack.add_file("a.gd", b"x")
        pack.write(path)
        self.assertEqual(main(["pck", "list", path]), 0)

    def test_pbin_set_types(self):
        directory = tempfile.mkdtemp()
        src = os.path.join(directory, "project.binary")
        out = os.path.join(directory, "out.binary")
        with open(src, "wb") as f:
            f.write(projectbin.build([]))
        self.assertEqual(main(["pbin", "set", src, "a/b", "7", "--type", "int", "-o", out]), 0)
        with open(out, "rb") as f:
            entries = projectbin.parse(f.read())
        self.assertEqual(projectbin.decode(projectbin.get(entries, "a/b")), ("int", 7))

    def test_apk_info(self):
        directory = tempfile.mkdtemp()
        apk_path = os.path.join(directory, "a.apk")
        make_apk(apk_path)
        self.assertEqual(main(["apk", "info", apk_path, "--filter", "classes"]), 0)


if __name__ == "__main__":
    unittest.main()
