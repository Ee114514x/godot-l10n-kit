# godot-l10n-kit

[![test](https://github.com/Ee114514x/godot-l10n-kit/actions/workflows/test.yml/badge.svg)](https://github.com/Ee114514x/godot-l10n-kit/actions/workflows/test.yml)

给 Godot 4 导出包做本地化的命令行工具：改 `project.binary` 注册注入脚本、往 `pck` / `sparsepck` 里塞资源、裁字体、读导出后的翻译资源、重打包并重签 APK。

只处理容器格式，不反编译、不解密、不破解，也不附带任何游戏资源。请只对你有权修改的包使用（自己的作品，或开发者明确授权的本地化项目）。

## 安装

```bash
pip install -e ".[font]"     # 需要字体功能时装 fonttools
glk --version
```

不安装也可以直接跑：

```bash
PYTHONPATH=/path/to/godot-l10n-kit python3 -m glk --help
```

依赖：Python 3.9+、`openssl`（签名用）、`fonttools`（字体相关命令用）。

## 命令

| 命令 | 作用 |
|---|---|
| `glk pbin list <project.binary>` | 列出全部配置项与类型 |
| `glk pbin set <bin> <key> <value> [--type string\|bool\|int\|float] -o out` | 写配置项 |
| `glk pbin autoload <bin> <Name> <res_path> -o out` | 注册 autoload 单例，等价于 `autoload/<Name>` |
| `glk pbin remove <bin> <key> -o out` | 删除配置项 |
| `glk pck list <pck>` | 列出包内文件与偏移 |
| `glk pck add <pck> --file RES=LOCAL -o out` | 添加或替换包内文件 |
| `glk pck remove <pck> <res> -o out` | 从包内移除文件 |
| `glk apk info <apk> [--filter assets/]` | 列出 APK 条目 |
| `glk apk repack <apk> -o out --replace N=P --add N=P --keydir keys` | 重打包、自检、签名 |
| `glk apk keygen -o keys` | 生成 RSA-2048 密钥 |
| `glk apk sign / verify` | 单独签名、校验签名 |
| `glk font carve <.fontdata> -o out.ttf` | 从 Godot 导入缓存里取出原始字体 |
| `glk font info <ttf>` | 字体名、字形数、CJK 字形数 |
| `glk font subset <ttf> --chars "汉字…" -o out.ttf` | 按字符集裁剪 |
| `glk tr list <x.zh.translation> [--json] [--limit N]` | 读 GTRN 或 RSRC 封装的翻译资源 |
| `glk lint <script.gd>` | 括号配对与缩进自检 |

## 典型流程

```bash
# 1. 取原始字体，裁出目标语言用得到的字形
glk font carve assets/.godot/imported/myfont.ttf-xxxx.fontdata -o build/base.ttf
glk font subset build/base.ttf --chars-file translation.txt -o build/font.ttf

# 2. 把注入脚本和字体塞进 pck
glk pck add assets/assets.sparsepck \
    --file mod/entry.gd=build/entry.gd \
    --file mod/font.ttf=build/font.ttf \
    -o build/assets.sparsepck

# 3. 在 project.binary 里注册 autoload
glk pbin autoload assets/project.binary L10n '*res://mod/entry.gd' -o build/project.binary

# 4. 重打包并签名（会自动对比原包，报告条目缺失或大小异常）
glk apk keygen -o keys
glk apk repack game.apk -o build/game-signed.apk \
    --replace assets/project.binary=build/project.binary \
    --replace assets/assets.sparsepck=build/assets.sparsepck \
    --add assets/mod/entry.gd=build/entry.gd \
    --add assets/mod/font.ttf=build/font.ttf \
    --keydir keys
```

## 演示

`examples/build_demo.py` 会生成一套合成的假包（APK / pck / project.binary / 字体 / 翻译），然后把上面所有命令跑一遍：

```bash
python3 examples/build_demo.py
```

产物在 `examples/out/`，可以直接拿来对照每一步的输入输出。一次完整运行的实际输出见 [examples/demo-output.txt](examples/demo-output.txt)。

## sparse pck 与普通 pck 的区别

- 普通 `.pck`：文件数据在包内，`offset` 相对 `file_base` 指向数据位置。`glk pck add` 会把新数据追加到目录表之后、回填 offset，并保留原有数据区。
- `.sparsepck`：只存目录，数据放在 APK 的 `assets/` 下同名路径，条目 offset 无意义（写 0）。所以往 sparse 包里加文件后，**必须同时把文件加进 APK**，否则运行时读不到。

## 翻译资源

Godot 4 导出的 `.translation` 是 `RSRC` 封装的 `OptimizedTranslation`：

- `hash_table` / `bucket_table` / `strings` 三张表，译文用 smaz 压缩，msgid 只保留哈希。
- 所以 `glk tr list` 对这类文件**只能列出译文**，无法还原 msgid；需要成对内容时请用项目里的 `.po` 或未优化的 `Translation` 资源。
- 未优化的 `Translation` 资源带 `messages` 字典，可以正常输出 msgid → msgstr。
- 裸 `GTRN` 文件（老格式）也支持。
- `RSCC` 压缩资源暂不支持。

## 签名说明

`glk apk repack` 会丢弃原有的 `META-INF/*.SF`、`*.RSA`、`*.DSA`、`*.EC` 和 `MANIFEST.MF`，然后用你自己的密钥重新加 **APK Signature Scheme v2** 块。

- 只有 v2：Android 7.0 以下装不上。
- 只支持 RSA-2048（v2 块长度依赖签名长度，预留了 256 字节）。
- 签名后原开发者的签名失效，这是重打包的必然结果——应用商店不会接受，只能侧载分发。
- 未压缩的 `.so` 按 16 KB 对齐，其余按 4 字节对齐，避免安装时 `INSTALL_FAILED_INVALID_APK`。

## 测试

```bash
python3 -m unittest discover -s tests -v
```

测试全部使用合成数据（自建 APK / pck / 字体 / RSRC），不依赖任何真实游戏包。

## 许可

MIT，见 `LICENSE`。`glk/smaz.py` 里的 smaz 码表来自 Godot 的 `thirdparty/misc/smaz.c`，为 Salvatore Sanfilippo 的 BSD 3-Clause 授权，原始声明保留在该文件头部。
