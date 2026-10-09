# 打包为 Windows exe 说明

本项目使用 [PyInstaller](https://pyinstaller.org/) 打包为单文件 Windows exe，已内置全部 Python 依赖、Playwright 驱动以及 chromium 浏览器内核，可免安装直接分发给其他 Windows 用户。

## 环境要求

- Windows 10/11 64 位
- Python 3.9+（建议 3.10–3.12）
- 已安装项目依赖（`pip install -r requirements.txt`）

## 打包步骤

```bash
# 1. 安装 PyInstaller
pip install pyinstaller

# 2. 用项目自带的 spec 打包
pyinstaller futures_daily_report.spec
```

打包完成后：

- 单文件 exe：`dist\期货公司日研报下载器.exe`
- 临时构建目录：`build\`

## spec 做了什么

`futures_daily_report.spec` 会自动：

1. 收集 Flask、requests、bs4、lxml、Pillow、pypdf、python-docx 等依赖；
2. 收集 Playwright 驱动（`playwright/driver`）；
3. 把 chromium 浏览器内核（`chromium_headless_shell-*`、`chromium-*`、`ffmpeg-*`）作为数据打包进去；
4. 程序运行时自动把 `PLAYWRIGHT_BROWSERS_PATH` 指向内置的浏览器目录，使「中原期货」「华联期货」无需额外安装即可工作。

## 首次运行较慢

单文件 exe 首次启动会解压内置资源（约 200–300MB），属正常现象，请耐心等待 10–30 秒。

## 验证

打包后建议先双击 exe，确认：
- 服务启动、浏览器打开 http://127.0.0.1:8899 面板正常；
- 至少下载 1–2 家纯 HTTP 公司（金元 / 东海 / 安粮）验证可用；
- 再测试中原 / 华联，确认 chromium 内置生效。

## 常见问题

- **杀毒软件误报**：PyInstaller 打包的 exe 可能被部分杀软误报，添加信任即可（本项目开源，可自行比对源码）。
- **启动慢**：单文件需解压，换成 `--onedir` 模式可加快启动（但会变成文件夹形态）。
