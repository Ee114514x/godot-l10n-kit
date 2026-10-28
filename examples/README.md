# 示例

`build_demo.py` 现场生成一套完全合成的测试素材，然后把 glk 的全部命令跑一遍：

```bash
python3 examples/build_demo.py
```

生成的目录结构：

```
examples/out/
├── src/                       # 输入：假 APK、sparse pck、project.binary、字体、翻译
├── build/                     # 输出：裁好的字体、改过的 pck 与 project.binary、签名后的 APK
└── keys/                      # 现场生成的 RSA-2048 签名密钥
```

脚本演示的顺序：读配置与包结构 → 取字体并裁剪 → 往 pck 加文件 → 注册 autoload → 重打包 → 自检 → 签名 → 校验签名。每一步的终端输出就是该命令的说明。
