"""Godot 4 .pck / .sparsepck 目录表读写。

头部：magic "GDPC" | pack_version(u32) | 引擎版本 major/minor/patch(u32*3)
      | flags(u32) | file_base(u64) | dir_offset(u64)
目录项：名称长度(含 NUL 补齐) | 名称 | offset(u64, 相对 file_base) | size(u64)
      | md5(16) | flags(u32)

两种布局都能处理：
- Godot 自己写出的包把文件数据放在目录表之前，目录表位于包尾；
- 本工具新增的数据写在目录表之后，偏移按表长回填。
"""

import hashlib
import struct
from dataclasses import dataclass, field

MAGIC = b"GDPC"
HEADER_SIZE = 40

FILE_ENCRYPTED = 1
FILE_REMOVAL = 2


class FormatError(ValueError):
    pass


@dataclass
class Entry:
    path: str
    offset: int
    size: int
    md5: bytes
    flags: int = 0


@dataclass
class Pack:
    raw: bytearray
    pack_version: int
    engine: tuple
    flags: int
    file_base: int
    dir_offset: int
    entries: list = field(default_factory=list)
    _pending: list = field(default_factory=list)
    _dir_end: int = 0
    _sparse: bool = False

    @classmethod
    def load(cls, path, sparse=None):
        with open(path, "rb") as f:
            data = bytearray(f.read())
        if data[:4] != MAGIC:
            raise FormatError("不是 Godot pck：magic 为 %r" % bytes(data[:4]))
        pack_version, major, minor, patch = struct.unpack_from("<IIII", data, 4)
        flags, file_base, dir_offset = struct.unpack_from("<IQQ", data, 20)
        pack = cls(data, pack_version, (major, minor, patch), flags, file_base, dir_offset)
        pack.sparse = path.endswith(".sparsepck") if sparse is None else sparse
        pack._read_dir()
        return pack

    @property
    def sparse(self):
        return self._sparse

    @sparse.setter
    def sparse(self, value):
        self._sparse = bool(value)

    def _read_dir(self):
        data = self.raw
        pos = self.dir_offset
        if pos + 4 > len(data):
            raise FormatError("目录偏移 %d 越界" % pos)
        count = struct.unpack_from("<I", data, pos)[0]
        pos += 4
        for _ in range(count):
            name_len = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            end = pos + name_len
            if end > len(data):
                raise FormatError("目录项名称越界：offset %d" % pos)
            path = bytes(data[pos:end]).rstrip(b"\x00").decode("utf-8")
            pos = end
            if pos + 36 > len(data):
                raise FormatError("目录项数据越界：offset %d" % pos)
            offset, size = struct.unpack_from("<QQ", data, pos)
            pos += 16
            md5 = bytes(data[pos:pos + 16])
            pos += 16
            flags = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            self.entries.append(Entry(path, offset, size, md5, flags))
        self._dir_end = pos

    def find(self, path):
        path = path.lstrip("/")
        for entry in self.entries:
            if entry.path == path:
                return entry
        return None

    def read_file(self, entry):
        if entry.flags & FILE_REMOVAL:
            raise FormatError("%s 已从包中移除" % entry.path)
        if self.sparse:
            raise FormatError("sparse 包不含文件数据，%s 需要从 APK 的 assets/ 读取" % entry.path)
        start = self.file_base + entry.offset
        return bytes(self.raw[start:start + entry.size])

    def add_file(self, path, data):
        """添加或替换包内路径。sparse 模式下数据不写入 pck，由调用方放进 APK 的 assets/。"""
        path = path.lstrip("/")
        digest = hashlib.md5(data).digest()
        entry = self.find(path)
        if entry is None:
            entry = Entry(path, 0, len(data), digest, 0)
            self.entries.append(entry)
        entry.md5 = digest
        entry.size = len(data)
        entry.flags &= ~FILE_REMOVAL
        if not self.sparse:
            self._pending.append((path, data))
        return entry

    def remove_file(self, path):
        entry = self.find(path)
        if entry is None:
            raise KeyError(path)
        entry.flags |= FILE_REMOVAL
        entry.size = 0
        entry.md5 = b"\x00" * 16
        self._pending = [(p, d) for p, d in self._pending if p != path]

    def _table(self):
        table = bytearray(struct.pack("<I", len(self.entries)))
        for entry in self.entries:
            table += _name_field(entry.path)
            table += struct.pack("<QQ", entry.offset, entry.size) + entry.md5 + struct.pack("<I", entry.flags)
        return table

    def write(self, out_path):
        pending_paths = {path for path, _ in self._pending}
        payload = bytes(self.raw[self._dir_end:])
        table = self._table()
        delta = self.dir_offset + len(table) - self._dir_end
        for entry in self.entries:
            if entry.path in pending_paths or not entry.size:
                continue
            if self.file_base + entry.offset >= self._dir_end:
                entry.offset += delta
        cursor = self.dir_offset + len(table) + len(payload)
        for path, data in self._pending:
            self.find(path).offset = cursor - self.file_base
            cursor += len(data)
        table = self._table()
        out = bytearray(self.raw[:self.dir_offset]) + table + payload
        for _, data in self._pending:
            out += data
        with open(out_path, "wb") as f:
            f.write(out)
        self.raw = out
        self._dir_end = self.dir_offset + len(table) + len(payload)
        self._pending = []
        return out_path


def _name_field(path):
    raw = path.encode("utf-8") + b"\x00"
    pad = (-len(raw)) % 4
    return struct.pack("<I", len(raw) + pad) + raw + b"\x00" * pad


def create(sparse=False, engine=(4, 0, 0), pack_version=2, flags=0, file_base=0):
    """生成一个空包，用于测试或从零组装。"""
    header = bytearray(MAGIC)
    header += struct.pack("<IIII", pack_version, engine[0], engine[1], engine[2])
    header += struct.pack("<IQQ", flags, file_base, HEADER_SIZE)
    header += struct.pack("<I", 0)
    pack = Pack(header, pack_version, engine, flags, file_base, HEADER_SIZE)
    pack.sparse = sparse
    pack._dir_end = len(header)
    return pack
