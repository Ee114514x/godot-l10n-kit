"""Godot 4 二进制资源（RSRC）解析。

布局（core/io/resource_format_binary.cpp，FORMAT_VERSION=6）：
  "RSRC" | big_endian(u32) | use_real64(u32) | 版本 u32*3
  | type(ustring) | importmd_ofs(u64) | flags(u32) | uid(u64)
  | [script_class(ustring)] | 11 个保留 u32
  | 字符串表(u32 数量 + ustring*)
  | 外部资源(u32 数量 + [type, path, uid?]*)
  | 内部资源(u32 数量 + [path(ustring), offset(u64)]*)
内部资源在 offset 处：type(ustring) | 属性数(u32) | [属性名索引(u32), Variant]*
ustring = 长度(u32，含结尾 NUL) + UTF-8 字节
"""

import struct

MAGIC = b"RSRC"
COMPRESSED_MAGIC = b"RSCC"

FLAG_NAMED_SCENE_IDS = 1
FLAG_UIDS = 2
FLAG_REAL_T_IS_DOUBLE = 4
FLAG_HAS_SCRIPT_CLASS = 8
RESERVED_FIELDS = 11

VARIANT_NAMES = {
    1: "nil", 2: "bool", 3: "int", 4: "float", 5: "string",
    10: "vector2", 11: "rect2", 12: "vector3", 13: "plane", 14: "quaternion",
    15: "aabb", 16: "basis", 17: "transform3d", 18: "transform2d", 20: "color",
    22: "node_path", 23: "rid", 24: "object", 26: "dictionary", 30: "array",
    31: "packed_byte_array", 32: "packed_int32_array", 33: "packed_float32_array",
    34: "packed_string_array", 35: "packed_vector3_array", 36: "packed_color_array",
    37: "packed_vector2_array", 40: "int64", 41: "double", 42: "callable",
    43: "signal", 44: "string_name", 45: "vector2i", 46: "rect2i", 47: "vector3i",
    48: "packed_int64_array", 49: "packed_float64_array", 50: "vector4", 51: "vector4i",
    52: "projection", 53: "packed_vector4_array",
}


class FormatError(ValueError):
    pass


class Reader:
    def __init__(self, data, big_endian=False, real_is_double=False):
        self.data = data
        self.pos = 0
        self.end = ">" if big_endian else "<"
        self.real_is_double = real_is_double

    def _unpack(self, fmt, size):
        if self.pos + size > len(self.data):
            raise FormatError("读取越界：offset %d" % self.pos)
        value = struct.unpack_from(self.end + fmt, self.data, self.pos)[0]
        self.pos += size
        return value

    def u32(self):
        return self._unpack("I", 4)

    def u64(self):
        return self._unpack("Q", 8)

    def i32(self):
        return self._unpack("i", 4)

    def f32(self):
        return self._unpack("f", 4)

    def f64(self):
        return self._unpack("d", 8)

    def raw(self, size):
        if self.pos + size > len(self.data):
            raise FormatError("读取越界：offset %d" % self.pos)
        chunk = self.data[self.pos:self.pos + size]
        self.pos += size
        return chunk

    def ustring(self):
        length = self.u32()
        if length == 0:
            return ""
        blob = self.raw(length)
        return blob.rstrip(b"\x00").decode("utf-8", "replace")

    def variant(self, string_table):
        kind = self.u32()
        if kind == 1:
            return None
        if kind == 2:
            return bool(self.u32())
        if kind == 3:
            return self.i32()
        if kind == 4:
            return self.f64() if self.real_is_double else self.f32()
        if kind == 5:
            return self.ustring()
        if kind == 44:
            index = self.u32()
            if index >= len(string_table):
                raise FormatError("StringName 索引越界：%d" % index)
            return string_table[index]
        if kind == 26:
            result = {}
            for _ in range(self.u32()):
                key = self.variant(string_table)
                result[str(key)] = self.variant(string_table)
            return result
        if kind == 30:
            return [self.variant(string_table) for _ in range(self.u32())]
        if kind == 31:
            return self.raw(self.u32())
        if kind == 32:
            count = self.u32()
            value = list(struct.unpack_from(self.end + "%di" % count, self.data, self.pos))
            self.pos += 4 * count
            return value
        if kind == 33:
            count = self.u32()
            value = list(struct.unpack_from(self.end + "%df" % count, self.data, self.pos))
            self.pos += 4 * count
            return value
        if kind == 48:
            count = self.u32()
            value = list(struct.unpack_from(self.end + "%dq" % count, self.data, self.pos))
            self.pos += 8 * count
            return value
        if kind == 49:
            count = self.u32()
            value = list(struct.unpack_from(self.end + "%dd" % count, self.data, self.pos))
            self.pos += 8 * count
            return value
        if kind == 34:
            return [self.ustring() for _ in range(self.u32())]
        if kind == 40:
            return self.u64()
        if kind == 41:
            return self.f64()
        if kind == 24:
            mode = self.u32()
            if mode == 0:
                return None
            if mode == 1:
                return {"external": self.ustring(), "path": self.ustring()}
            if mode == 2:
                return {"internal": self.u32()}
            if mode == 3:
                return {"external_index": self.u32()}
            raise FormatError("未知 Object 编码：%d" % mode)
        if kind == 23:
            return {"rid": self.u32()}
        raise FormatError("暂不支持 Variant 类型 %d（%s）" % (kind, VARIANT_NAMES.get(kind, "unknown")))


class Resource:
    def __init__(self, type_name, properties):
        self.type = type_name
        self.properties = properties


class ResourceFile:
    def __init__(self, **fields):
        self.__dict__.update(fields)


def parse(data):
    if data[:4] == COMPRESSED_MAGIC:
        raise FormatError("RSCC 压缩资源暂不支持")
    if data[:4] != MAGIC:
        raise FormatError("不是 Godot 二进制资源：缺少 RSRC 头")

    head = Reader(data)
    head.pos = 4
    big_endian = head.u32()
    head.real_is_double = bool(head.u32())
    reader = Reader(data, big_endian=bool(big_endian), real_is_double=head.real_is_double)
    reader.pos = 12
    major = reader.u32()
    minor = reader.u32()
    fmt = reader.u32()
    type_name = reader.ustring()
    reader.u64()
    flags = reader.u32()
    uid = reader.u64()
    script_class = reader.ustring() if flags & FLAG_HAS_SCRIPT_CLASS else None
    for _ in range(RESERVED_FIELDS):
        reader.u32()

    string_table = [reader.ustring() for _ in range(reader.u32())]

    external = []
    for _ in range(reader.u32()):
        entry = {"type": reader.ustring(), "path": reader.ustring()}
        if flags & FLAG_UIDS:
            entry["uid"] = reader.u64()
        external.append(entry)

    internal = []
    for _ in range(reader.u32()):
        internal.append({"path": reader.ustring(), "offset": reader.u64()})

    resources = []
    for entry in internal:
        reader.pos = entry["offset"]
        kind = reader.ustring()
        properties = {}
        for _ in range(reader.u32()):
            index = reader.u32()
            if index >= len(string_table):
                raise FormatError("属性名索引越界：%d" % index)
            properties[string_table[index]] = reader.variant(string_table)
        resources.append(Resource(kind, properties))

    return ResourceFile(
        big_endian=bool(big_endian),
        real_is_double=bool(head.real_is_double),
        version=(major, minor, fmt),
        type=type_name,
        flags=flags,
        uid=uid,
        script_class=script_class,
        strings=string_table,
        external=external,
        internal=internal,
        resources=resources,
    )
