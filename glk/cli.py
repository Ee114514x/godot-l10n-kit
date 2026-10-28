"""命令行入口：glk。"""

import argparse
import json
import sys

from . import __version__
from . import apk, font, gdscript, pck, projectbin, translation


def _pairs(value):
    if "=" not in value:
        raise argparse.ArgumentTypeError("需要 NAME=PATH 形式：%r" % value)
    name, _, path = value.partition("=")
    return name, path


def _build_parser():
    parser = argparse.ArgumentParser(prog="glk", description="Godot 导出包本地化打补丁工具")
    parser.add_argument("--version", action="version", version="glk %s" % __version__)
    sub = parser.add_subparsers(dest="command", required=True)

    apk_parser = sub.add_parser("apk", help="APK 重打包与 v2 签名")
    apk_sub = apk_parser.add_subparsers(dest="action", required=True)

    info = apk_sub.add_parser("info", help="列出 APK 内的文件")
    info.add_argument("apk")
    info.add_argument("--filter", default="", help="只显示名称包含该子串的条目")
    info.set_defaults(func=_cmd_apk_info)

    repack = apk_sub.add_parser("repack", help="替换/新增文件并重新打包")
    repack.add_argument("apk", help="原始 APK")
    repack.add_argument("-o", "--output", required=True)
    repack.add_argument("--replace", type=_pairs, action="append", default=[], metavar="NAME=PATH")
    repack.add_argument("--add", type=_pairs, action="append", default=[], metavar="NAME=PATH")
    repack.add_argument("--keydir", help="密钥目录，内含 key.pem cert.der pub.der")
    repack.add_argument("--key")
    repack.add_argument("--cert")
    repack.add_argument("--pub")
    repack.add_argument("--no-sign", action="store_true", help="只打包不签名")
    repack.set_defaults(func=_cmd_apk_repack)

    keygen = apk_sub.add_parser("keygen", help="生成 RSA-2048 签名密钥")
    keygen.add_argument("-o", "--output", default="keys")
    keygen.add_argument("--cn", default="glk")
    keygen.set_defaults(func=_cmd_apk_keygen)

    sign = apk_sub.add_parser("sign", help="对已打包的 APK 加 v2 签名")
    sign.add_argument("apk")
    sign.add_argument("--key", required=True)
    sign.add_argument("--cert", required=True)
    sign.add_argument("--pub", required=True)
    sign.set_defaults(func=_cmd_apk_sign)

    verify = apk_sub.add_parser("verify", help="校验 v2 签名")
    verify.add_argument("apk")
    verify.add_argument("--pub", required=True)
    verify.set_defaults(func=_cmd_apk_verify)

    pbin = sub.add_parser("pbin", help="project.binary 读写")
    pbin_sub = pbin.add_subparsers(dest="action", required=True)

    pbin_list = pbin_sub.add_parser("list", help="列出全部配置项")
    pbin_list.add_argument("path")
    pbin_list.set_defaults(func=_cmd_pbin_list)

    pbin_set = pbin_sub.add_parser("set", help="设置一个配置项")
    pbin_set.add_argument("path")
    pbin_set.add_argument("key")
    pbin_set.add_argument("value")
    pbin_set.add_argument("--type", choices=("string", "bool", "int", "float"), default="string")
    pbin_set.add_argument("-o", "--output", required=True)
    pbin_set.set_defaults(func=_cmd_pbin_set)

    pbin_autoload = pbin_sub.add_parser("autoload", help="注册一个 autoload 单例")
    pbin_autoload.add_argument("path")
    pbin_autoload.add_argument("name")
    pbin_autoload.add_argument("res_path", help="如 *res://mod/entry.gd")
    pbin_autoload.add_argument("-o", "--output", required=True)
    pbin_autoload.set_defaults(func=_cmd_pbin_autoload)

    pbin_remove = pbin_sub.add_parser("remove", help="删除一个配置项")
    pbin_remove.add_argument("path")
    pbin_remove.add_argument("key")
    pbin_remove.add_argument("-o", "--output", required=True)
    pbin_remove.set_defaults(func=_cmd_pbin_remove)

    pack = sub.add_parser("pck", help="pck / sparsepck 读写")
    pack_sub = pack.add_subparsers(dest="action", required=True)

    pack_list = pack_sub.add_parser("list", help="列出包内文件")
    pack_list.add_argument("path")
    pack_list.add_argument("--filter", default="")
    pack_list.set_defaults(func=_cmd_pck_list)

    pack_add = pack_sub.add_parser("add", help="添加或替换包内文件")
    pack_add.add_argument("path")
    pack_add.add_argument("--file", type=_pairs, action="append", required=True, metavar="RES_PATH=LOCAL_PATH")
    pack_add.add_argument("--sparse", dest="sparse", action="store_true", default=None)
    pack_add.add_argument("--no-sparse", dest="sparse", action="store_false")
    pack_add.add_argument("-o", "--output", required=True)
    pack_add.set_defaults(func=_cmd_pck_add)

    pack_remove = pack_sub.add_parser("remove", help="从包内移除文件")
    pack_remove.add_argument("path")
    pack_remove.add_argument("res_path", action="append")
    pack_remove.add_argument("-o", "--output", required=True)
    pack_remove.set_defaults(func=_cmd_pck_remove)

    font_parser = sub.add_parser("font", help="字体提取与裁剪")
    font_sub = font_parser.add_subparsers(dest="action", required=True)

    carve = font_sub.add_parser("carve", help="从 .fontdata 中取出原始字体")
    carve.add_argument("path")
    carve.add_argument("-o", "--output", required=True)
    carve.set_defaults(func=_cmd_font_carve)

    font_info = font_sub.add_parser("info", help="查看字体名称与字形数")
    font_info.add_argument("path")
    font_info.set_defaults(func=_cmd_font_info)

    subset = font_sub.add_parser("subset", help="按字符集裁剪字体")
    subset.add_argument("path")
    subset.add_argument("--chars")
    subset.add_argument("--chars-file")
    subset.add_argument("-o", "--output", required=True)
    subset.set_defaults(func=_cmd_font_subset)

    tr = sub.add_parser("tr", help="翻译资源读取")
    tr_sub = tr.add_subparsers(dest="action", required=True)
    tr_list = tr_sub.add_parser("list", help="列出 msgid / msgstr")
    tr_list.add_argument("path")
    tr_list.add_argument("--limit", type=int, default=20)
    tr_list.add_argument("--json", action="store_true")
    tr_list.set_defaults(func=_cmd_tr_list)

    lint = sub.add_parser("lint", help="GDScript 结构自检")
    lint.add_argument("path")
    lint.set_defaults(func=_cmd_lint)

    return parser


def _cmd_apk_info(args):
    entries = apk.list_entries(args.apk)
    for name, size, csize in entries:
        if args.filter and args.filter not in name:
            continue
        print("%10d %10d  %s" % (size, csize, name))
    print("共 %d 个条目" % len(entries))
    return 0


def _cmd_apk_repack(args):
    repacker = apk.Repacker(args.apk)
    for name, path in args.replace:
        repacker.replace(name, path)
    for name, path in args.add:
        repacker.add(name, path)
    repacker.write(args.output)
    print("已写出 %s" % args.output)
    report = apk.compare(args.apk, args.output,
                         [name for name, _ in args.replace + args.add])
    if report["missing"] or report["extra"] or report["resized"]:
        for tag, items in report.items():
            if items:
                print("自检异常 %s: %s%s" % (tag, items[:5], " …" if len(items) > 5 else ""))
        return 1
    print("自检通过：%d 个原条目完整保留" % (len(apk.list_entries(args.apk)) - 2))
    if args.no_sign:
        print("未签名，无法直接安装；签名请执行 glk apk sign")
        return 0
    key, cert, pub = _resolve_keys(args)
    if not key:
        print("未提供密钥，产物未签名。可用 --keydir 或 glk apk keygen 生成")
        return 1
    apk.sign_v2(args.output, key, cert, pub)
    result = apk.verify_v2(args.output, pub)
    print("签名校验 %s" % json.dumps(result, ensure_ascii=False))
    return 0 if result.get("signature_valid") and result.get("digest_match") else 1


def _resolve_keys(args):
    if args.keydir:
        import os

        return (os.path.join(args.keydir, "key.pem"),
                os.path.join(args.keydir, "cert.der"),
                os.path.join(args.keydir, "pub.der"))
    return args.key, args.cert, args.pub


def _cmd_apk_keygen(args):
    key, cert, pub = apk.keygen(args.output, cn=args.cn)
    print("key  %s" % key)
    print("cert %s" % cert)
    print("pub  %s" % pub)
    return 0


def _cmd_apk_sign(args):
    apk.sign_v2(args.apk, args.key, args.cert, args.pub)
    print(json.dumps(apk.verify_v2(args.apk, args.pub), ensure_ascii=False))
    return 0


def _cmd_apk_verify(args):
    result = apk.verify_v2(args.apk, args.pub)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("signature_valid") and result.get("digest_match") else 1


def _cmd_pbin_list(args):
    with open(args.path, "rb") as f:
        entries = projectbin.parse(f.read())
    for key, value in entries:
        kind, decoded = projectbin.decode(value)
        print("%-56s %s(%r)" % (key, kind, decoded))
    print("共 %d 项" % len(entries))
    return 0


def _typed_value(kind, text):
    if kind == "bool":
        return projectbin.boolean(text.lower() in ("1", "true", "yes", "on"))
    if kind == "int":
        return projectbin.int32(int(text))
    if kind == "float":
        return projectbin.float32(float(text))
    return projectbin.string(text)


def _cmd_pbin_set(args):
    with open(args.path, "rb") as f:
        entries = projectbin.parse(f.read())
    projectbin.set(entries, args.key, _typed_value(getattr(args, "type", "string"), args.value))
    with open(args.output, "wb") as f:
        f.write(projectbin.build(entries))
    print("已写入 %s" % args.output)
    return 0


def _cmd_pbin_autoload(args):
    args.key = "autoload/%s" % args.name
    args.value = args.res_path
    return _cmd_pbin_set(args)


def _cmd_pbin_remove(args):
    with open(args.path, "rb") as f:
        entries = projectbin.parse(f.read())
    entries = projectbin.remove(entries, args.key)
    with open(args.output, "wb") as f:
        f.write(projectbin.build(entries))
    print("已写入 %s（%d 项）" % (args.output, len(entries)))
    return 0


def _cmd_pck_list(args):
    pack = pck.Pack.load(args.path)
    print("引擎版本 %d.%d.%d，%s，%d 个条目" % (
        *pack.engine, "sparse" if pack.sparse else "packed", len(pack.entries)))
    for entry in pack.entries:
        if args.filter and args.filter not in entry.path:
            continue
        print("%10d  offset=%-10d flags=%d  %s" % (entry.size, entry.offset, entry.flags, entry.path))
    return 0


def _cmd_pck_add(args):
    pack = pck.Pack.load(args.path, sparse=args.sparse)
    for res_path, local_path in args.file:
        with open(local_path, "rb") as f:
            pack.add_file(res_path, f.read())
    pack.write(args.output)
    print("已写出 %s（sparse=%s，%d 个条目）" % (args.output, pack.sparse, len(pack.entries)))
    if pack.sparse:
        print("sparse 包不存文件数据：新文件需同时放进 APK 的 assets/ 下同名路径")
    return 0


def _cmd_pck_remove(args):
    pack = pck.Pack.load(args.path)
    for res_path in args.res_path:
        pack.remove_file(res_path)
    pack.write(args.output)
    print("已写出 %s" % args.output)
    return 0


def _cmd_font_carve(args):
    with open(args.path, "rb") as f:
        raw = font.carve(f.read())
    with open(args.output, "wb") as f:
        f.write(raw)
    print("已取出 %d 字节 -> %s" % (len(raw), args.output))
    return 0


def _cmd_font_info(args):
    print(json.dumps(font.info(args.path), ensure_ascii=False))
    return 0


def _cmd_font_subset(args):
    text = args.chars or ""
    if args.chars_file:
        with open(args.chars_file, encoding="utf-8") as f:
            text += f.read()
    if not text:
        print("需要 --chars 或 --chars-file", file=sys.stderr)
        return 2
    font.subset(args.path, args.output, text=text)
    print("已裁剪 -> %s" % args.output)
    return 0


def _cmd_tr_list(args):
    sets = translation.load(args.path)
    if args.json:
        payload = [{
            "format": item.format,
            "locale": item.locale,
            "count": len(item.messages),
            "messages": [{"msgid": a, "msgstr": b} for a, b in item.messages],
            "notes": item.notes,
        } for item in sets]
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    for item in sets:
        header = "%s  locale=%s  共 %d 条" % (item.format, item.locale or "-", len(item.messages))
        print(header)
        for note in item.notes:
            print("  # %s" % note)
        for msgid, msgstr in item.messages[:args.limit]:
            source = "(哈希不可还原)" if msgid is None else msgid.replace("\n", "\\n")
            print("%-40s -> %s" % (source, msgstr.replace("\n", "\\n")))
    return 0


def _cmd_lint(args):
    with open(args.path, encoding="utf-8") as f:
        problems, structure = gdscript.check(f.read())
    for line in problems + structure:
        print(line)
    print("问题 %d 项" % (len(problems) + len(structure)))
    return 0 if not problems and not structure else 1


def main(argv=None):
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, KeyError) as exc:
        print("错误：%s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
