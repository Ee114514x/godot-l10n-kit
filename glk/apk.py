"""APK 重打包（保留原始压缩数据）与 v2 签名。

不依赖 apktool / apksigner：直接读写 zip 结构，去掉原签名块后用自有密钥重签。
产物只有 v2 签名，最低安装版本为 Android 7.0。
"""

import hashlib
import os
import struct
import subprocess
import tempfile
import zipfile
import zlib

CHUNK = 1024 * 1024
META_PREFIX = "META-INF/"
SIGNATURE_SUFFIXES = (".SF", ".RSA", ".DSA", ".EC")
SIG_ALGORITHM = 0x0103  # RSASSA-PKCS1-v1_5 + SHA-256


class ApkError(RuntimeError):
    pass


class Item:
    __slots__ = ("name", "method", "crc", "csize", "usize", "date_time", "ext_attr", "data", "compressed")

    def __init__(self, name, method, crc, csize, usize, date_time, ext_attr, data, compressed=False):
        self.name = name
        self.method = method
        self.crc = crc
        self.csize = csize
        self.usize = usize
        self.date_time = date_time
        self.ext_attr = ext_attr
        self.data = data
        self.compressed = compressed


def _is_signature(name):
    upper = name.upper()
    if not upper.startswith(META_PREFIX):
        return False
    return upper.endswith(SIGNATURE_SUFFIXES) or upper.endswith("MANIFEST.MF")


def _read_local_data(f, info):
    f.seek(info.header_offset)
    header = f.read(30)
    sig = struct.unpack_from("<I", header, 0)[0]
    if sig != 0x04034B50:
        raise ApkError("本地文件头损坏：%s" % info.filename)
    nlen, elen = struct.unpack_from("<HH", header, 26)
    f.seek(info.header_offset + 30 + nlen + elen)
    return f.read(info.compress_size)


def list_entries(apk_path):
    with zipfile.ZipFile(apk_path) as z:
        return [(i.filename, i.file_size, i.compress_size) for i in z.infolist()]


def compare(base_path, out_path, replaced=()):
    """对比原包与产物：条目集合与大小（替换/新增的条目不计入 size 差异）。"""
    base = {name: size for name, size, _ in list_entries(base_path)}
    out = {name: size for name, size, _ in list_entries(out_path)}
    replaced = set(replaced)
    missing = sorted(name for name in base if name not in out and not _is_signature(name))
    extra = sorted(name for name in out if name not in base and name not in replaced)
    resized = sorted(name for name, size in base.items()
                     if name in out and size != out[name] and name not in replaced)
    return {"missing": missing, "extra": extra, "resized": resized}


class Repacker:
    def __init__(self, base_path):
        self.base_path = base_path
        self._items = []
        self._overrides = {}
        self._additions = {}

    def replace(self, name, source_path):
        self._overrides[name] = source_path

    def add(self, name, source_path):
        self._additions[name] = source_path

    def _collect(self):
        items = []
        with open(self.base_path, "rb") as f, zipfile.ZipFile(self.base_path) as z:
            for info in z.infolist():
                name = info.filename
                if _is_signature(name) or name in self._overrides:
                    continue
                items.append(Item(
                    name, info.compress_type, info.CRC, info.compress_size, info.file_size,
                    info.date_time, info.external_attr, _read_local_data(f, info),
                    compressed=info.compress_type == zipfile.ZIP_DEFLATED,
                ))
        for name, path in self._overrides.items():
            items.append(self._new_item(name, path))
        for name, path in self._additions.items():
            items.append(self._new_item(name, path))
        return items

    @staticmethod
    def _new_item(name, source_path):
        with open(source_path, "rb") as f:
            data = f.read()
        return Item(name, zipfile.ZIP_DEFLATED, 0, 0, len(data), (2026, 1, 1, 0, 0, 0),
                    0o100644 << 16, data)

    def write(self, out_path):
        items = self._collect()
        _assemble(items, out_path)
        return out_path


def _dos(date_time):
    year, month, day, hour, minute, second = date_time
    return ((hour << 11) | (minute << 5) | (second // 2)), (((year - 1980) << 9) | (month << 5) | day)


def _align_extra(offset, name, method):
    if method != 0:
        return b""
    target = 16384 if name.endswith(".so") else 4
    head = offset + 30 + len(name.encode("utf-8")) + 4
    pad = (-head) % target
    return struct.pack("<HH", 0xD935, pad) + b"\x00" * pad


def _assemble(items, out_path):
    central = []
    with open(out_path, "wb") as f:
        offset = 0
        for item in items:
            name_b = item.name.encode("utf-8")
            method = zipfile.ZIP_DEFLATED if item.method == zipfile.ZIP_DEFLATED else 0
            if method == zipfile.ZIP_DEFLATED and not item.compressed:
                compressor = zlib.compressobj(6, zlib.DEFLATED, -15)
                data = compressor.compress(item.data) + compressor.flush()
                crc = zlib.crc32(item.data) & 0xFFFFFFFF
                usize = len(item.data)
            else:
                data = item.data
                crc = item.crc
                usize = item.usize
            extra = _align_extra(offset, item.name, method)
            flags = 0x800 if any(ord(ch) > 127 for ch in item.name) else 0
            mtime, mdate = _dos(item.date_time)
            header = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, flags, method, mtime, mdate,
                                 crc, len(data), usize, len(name_b), len(extra))
            f.write(header + name_b + extra + data)
            central.append((item, method, flags, mtime, mdate, crc, len(data), usize, offset, name_b))
            offset += len(header) + len(name_b) + len(extra) + len(data)
        cd_offset = offset
        for item, method, flags, mtime, mdate, crc, csize, usize, local_off, name_b in central:
            record = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, flags, method, mtime, mdate,
                                 crc, csize, usize, len(name_b), 0, 0, 0, 0, item.ext_attr, local_off)
            f.write(record + name_b)
            offset += len(record) + len(name_b)
        f.write(struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, len(central), len(central),
                            offset - cd_offset, cd_offset, 0))


def _lp(payload):
    return struct.pack("<I", len(payload)) + payload


def _chunked_digest(sections):
    digests = []
    for section in sections:
        for pos in range(0, len(section), CHUNK):
            piece = section[pos:pos + CHUNK]
            digests.append(hashlib.sha256(b"\xa5" + struct.pack("<I", len(piece)) + piece).digest())
    return hashlib.sha256(b"\x5a" + struct.pack("<I", len(digests)) + b"".join(digests)).digest()


def _signed_data(digest, cert):
    digest_entry = struct.pack("<I", SIG_ALGORITHM) + _lp(digest)
    return _lp(_lp(digest_entry)) + _lp(_lp(cert)) + _lp(b"")


def _openssl_dgst(args, data=None, key_path=None):
    with tempfile.TemporaryDirectory() as d:
        cmd = ["openssl", "dgst", "-sha256"] + args
        if key_path is not None:
            if data is None:
                raise ValueError("缺少待签名数据")
            path = os.path.join(d, "data")
            with open(path, "wb") as f:
                f.write(data)
            cmd += [key_path, path]
        result = subprocess.run(cmd, capture_output=True)
        return result


def keygen(out_dir, cn="glk"):
    os.makedirs(out_dir, exist_ok=True)
    key = os.path.join(out_dir, "key.pem")
    cert = os.path.join(out_dir, "cert.pem")
    cert_der = os.path.join(out_dir, "cert.der")
    pub_der = os.path.join(out_dir, "pub.der")
    if not os.path.exists(key):
        subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-sha256",
                        "-keyout", key, "-out", cert, "-days", "7300", "-subj", "/CN=%s" % cn],
                       check=True, capture_output=True)
    subprocess.run(["openssl", "x509", "-in", cert, "-outform", "DER", "-out", cert_der],
                   check=True, capture_output=True)
    subprocess.run(["openssl", "pkey", "-in", key, "-pubout", "-outform", "DER", "-out", pub_der],
                   check=True, capture_output=True)
    return key, cert_der, pub_der


def sign_v2(apk_path, key_pem, cert_der_path, pubkey_der_path):
    with open(apk_path, "rb") as f:
        blob = f.read()
    eocd_offset = blob.rfind(b"PK\x05\x06")
    if eocd_offset < 0:
        raise ApkError("找不到 EOCD")
    cd_offset = struct.unpack_from("<I", blob, eocd_offset + 16)[0]
    with open(cert_der_path, "rb") as f:
        cert = f.read()
    with open(pubkey_der_path, "rb") as f:
        pubkey = f.read()

    def build_value(signed_data, signature):
        signatures = _lp(struct.pack("<I", SIG_ALGORITHM) + _lp(signature))
        signer = _lp(signed_data) + _lp(signatures) + _lp(pubkey)
        return _lp(_lp(signer))

    placeholder = b"\x00" * 256
    placeholder_value = build_value(_signed_data(b"\x00" * 32, cert), placeholder)
    block_len = 8 + 4 + len(placeholder_value) + 32

    # 摘要覆盖的三个区块里，EOCD 的目录偏移必须写成签名块起点（即原目录偏移），
    # 不能在写回 +block_len 之后再算，否则与校验端不一致。
    digest = _chunked_digest([blob[:cd_offset],
                              blob[cd_offset:eocd_offset],
                              blob[eocd_offset:]])

    signed_data = _signed_data(digest, cert)
    result = _openssl_dgst(["-sign"], data=signed_data, key_path=key_pem)
    if result.returncode != 0:
        raise ApkError("签名失败：%s" % result.stderr.decode("utf-8", "replace"))
    signature = result.stdout
    if len(signature) != len(placeholder):
        raise ApkError("签名长度 %d 与预留 %d 不一致，暂只支持 RSA-2048" % (len(signature), len(placeholder)))

    value = build_value(signed_data, signature)
    pairs = struct.pack("<Q", len(value) + 4) + struct.pack("<I", 0x7109871A) + value
    size = len(pairs) + 8 + 16
    block = struct.pack("<Q", size) + pairs + struct.pack("<Q", size) + b"APK Sig Block 42"
    if len(block) != block_len:
        raise ApkError("签名块长度不一致")

    tail = bytearray(blob[eocd_offset:])
    struct.pack_into("<I", tail, 16, cd_offset + block_len)
    with open(apk_path, "wb") as f:
        f.write(blob[:cd_offset] + block + blob[cd_offset:eocd_offset] + bytes(tail))
    return digest


def verify_v2(apk_path, pubkey_der_path):
    with open(apk_path, "rb") as f:
        blob = f.read()
    magic = b"APK Sig Block 42"
    idx = blob.rfind(magic)
    if idx < 0:
        return {"signed": False, "error": "没有 v2 签名块"}
    trailing = struct.unpack_from("<Q", blob, idx - 8)[0]
    block_start = idx + 8 - trailing
    pairs = blob[block_start + 8: idx - 8]
    value = None
    pos = 0
    while pos + 12 <= len(pairs):
        plen = struct.unpack_from("<Q", pairs, pos)[0]
        pid = struct.unpack_from("<I", pairs, pos + 8)[0]
        if pid == 0x7109871A:
            value = pairs[pos + 12:pos + 8 + plen]
        pos += 8 + plen
    if value is None:
        return {"signed": False, "error": "签名块里没有 v2 记录"}

    def read_lp(buf, p):
        n = struct.unpack_from("<I", buf, p)[0]
        return buf[p + 4:p + 4 + n], p + 4 + n

    signers, _ = read_lp(value, 0)
    signer, _ = read_lp(signers, 0)
    signed_data, p = read_lp(signer, 0)
    signatures, p = read_lp(signer, p)
    pubkey, _ = read_lp(signer, p)

    eocd_offset = blob.rfind(b"PK\x05\x06")
    cd_offset = struct.unpack_from("<I", blob, eocd_offset + 16)[0]
    original_eocd = bytearray(blob[eocd_offset:])
    struct.pack_into("<I", original_eocd, 16, block_start)
    digest = _chunked_digest([blob[:block_start], blob[cd_offset:eocd_offset], bytes(original_eocd)])

    digests, _ = read_lp(signed_data, 0)
    digest_record, _ = read_lp(digests, 0)
    algo = struct.unpack_from("<I", digest_record, 0)[0]
    stored, _ = read_lp(digest_record, 4)

    record, _ = read_lp(signatures, 0)
    signature, _ = read_lp(record, 4)

    with tempfile.TemporaryDirectory() as d:
        sd = os.path.join(d, "signed_data")
        sg = os.path.join(d, "signature")
        with open(sd, "wb") as f:
            f.write(signed_data)
        with open(sg, "wb") as f:
            f.write(signature)
        result = subprocess.run(["openssl", "dgst", "-sha256", "-verify", pubkey_der_path,
                                 "-keyform", "DER", "-signature", sg, sd], capture_output=True)
    with open(pubkey_der_path, "rb") as f:
        expected_pubkey = f.read()
    return {
        "signed": True,
        "algorithm": hex(algo),
        "digest_match": stored == digest,
        "signature_valid": result.returncode == 0,
        "pubkey_match": pubkey == expected_pubkey,
        "block_before_cd": cd_offset == idx + 16,
    }
