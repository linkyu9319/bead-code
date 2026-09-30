# BeadCode｜拼豆码工坊

[![CI](https://github.com/yunko1993/bead-code/actions/workflows/ci.yml/badge.svg)](https://github.com/yunko1993/bead-code/actions/workflows/ci.yml)

将微信、支付宝等**静态二维码图片**转换成拼豆图纸。项目同时提供手机适配的 Web 页面和命令行工具。程序不会按普通图片缩放二维码，而是先解码收款内容，再重建无头像、H 级纠错的干净二维码，最后按整数倍映射成拼豆。

## 启动 Web 页面

Windows PowerShell：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run_web.py
```

电脑打开 `http://127.0.0.1:8000`。同一局域网内的手机可以访问 `http://电脑局域网IP:8000`，但需要允许 Windows 防火墙中的对应入站访问。

服务器部署请参阅 [DEPLOYMENT.md](DEPLOYMENT.md)。

## 第一版输出

- `scan_preview.png`：无网格扫码预览，程序会自动复扫验证。
- `blueprint.png`：每颗拼豆标注实体色号，并带坐标和每 10 格粗线的施工图。
- `blueprint.pdf`：便于打印的单页 PDF。
- `materials.csv`：色号标准、实体色号、参考颜色、精确数量和建议采购数量。
- `metadata.json`：二维码版本、图纸尺寸、成品尺寸和验证结果。

隐私说明：二维码内容只在内存中用于复验，输出文件不保存支付宝链接明文，只保存 SHA-256 摘要。

## 安装

Windows PowerShell：

```powershell
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 使用

```powershell
.\.venv\Scripts\python.exe main.py ".\支付宝收款码.png"
```

默认采用：

- 一个 QR 模块对应 `2 × 2` 颗拼豆；
- 5mm 拼豆；
- 深色 `#111827`，浅色 `#FFFFFF`；
- QR 标准要求的四模块静区。

Web 页面可切换 `MARD 221`、`COCO 291` 和 `Artkal C 197` 三套色号标准。
程序会按 CIE Lab 视觉色差匹配最接近的实体色号，并使用匹配后的实际颜色执行扫码复验。
命令行可通过 `--palette mard-221|coco-291|artkal-c-197` 指定标准。
内置色卡的数据来源和许可见 [THIRD_PARTY_DATA.md](THIRD_PARTY_DATA.md)。

使用一颗拼豆对应一个模块：

```powershell
.\.venv\Scripts\python.exe main.py ".\收款码.png" --beads-per-module 1
```

使用支付宝蓝色拼豆：

```powershell
.\.venv\Scripts\python.exe main.py ".\收款码.png" --dark-color "#1677FF" --allow-low-contrast
```

支付宝蓝与纯白的计算对比度约为 `4.10:1`，略低于本工具保守设置的 `4.5:1`，因此需要明确允许。程序仍会自动复扫数字预览，但制作实物前必须额外验证。

指定输出目录和 2.6mm 迷你拼豆：

```powershell
.\.venv\Scripts\python.exe main.py ".\收款码.png" -o output --bead-size-mm 2.6
```

部分微信平台码包含 ECI 或中央头像结构，如果干净重建无法通过自动复扫，可使用兼容模式保留原始模块：

```powershell
.\.venv\Scripts\python.exe main.py ".\微信收款码.png" --preserve-source-modules
```

兼容模式会保留平台原码已经使用的纠错余量，实物制作应优先使用每模块 `2 × 2` 颗拼豆。

## 实物制作注意事项

自动复验只能证明生成的数字预览仍可扫描，不能保证熨烫后的实物一定可用。正式收款前必须使用多台手机，在不同距离、角度和光照下扫描实物，并核对支付宝展示的收款人姓名。

- 定位图案和四周白色静区不能缺豆。
- 深浅拼豆应保持高对比，不建议使用相近颜色。
- 默认 `2 × 2` 颗拼豆对应一个模块，容错高于一模块一颗。
- 熨烫时避免格子明显位移、粘连或大面积反光。
- 不要在成品二维码上额外添加 Logo、文字或装饰。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```
