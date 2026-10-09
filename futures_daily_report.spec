# -*- mode: python ; coding: utf-8 -*-
"""
期货公司日研报下载器 —— PyInstaller 打包配置
============================================================
打包为单文件 Windows exe，内置：
  - Flask 及全部 Python 依赖
  - Playwright 驱动（node driver）
  - chromium 浏览器内核（中原、华联两家需要）

构建：pyinstaller futures_daily_report.spec
产物：dist\期货公司日研报下载器.exe
"""
import os

from PyInstaller.utils.hooks import collect_all, collect_submodules, copy_metadata

project_root = os.path.abspath(os.path.dirname(__file__))

# ---- 1. 收集 playwright 及其 node 驱动 ----
pw_datas, pw_binaries, pw_hidden = collect_all('playwright')

# ---- 2. 收集 python-docx ----
docx_datas, docx_binaries, docx_hidden = collect_all('docx')

# ---- 3. 隐藏导入（惰性 import 的模块需显式声明）----
hiddenimports = (
    collect_submodules('playwright')
    + collect_submodules('flask')
    + [
        'playwright.sync_api',
        'docx', 'docx.shared', 'docx.enum.text', 'docx.oxml.ns',
        'PIL', 'pypdf', 'bs4', 'lxml', 'requests', 'urllib3',
    ]
    + pw_hidden
    + docx_hidden
)

# ---- 4. 内置 chromium 浏览器内核 ----
_browsers_src = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ms-playwright')
_browser_dirs = ['chromium_headless_shell-1234', 'chromium-1234', 'ffmpeg-1011', 'winldd-1007']
browser_datas = []
for _name in _browser_dirs:
    _src = os.path.join(_browsers_src, _name)
    if os.path.isdir(_src):
        browser_datas.append((_src, os.path.join('ms-playwright', _name)))

# ---- 5. 前端模板与静态资源 ----
web_datas = [
    (os.path.join(project_root, 'templates'), 'templates'),
    (os.path.join(project_root, 'static'), 'static'),
]

datas = web_datas + pw_datas + docx_datas + browser_datas
binaries = pw_binaries + docx_binaries

a = Analysis(
    [os.path.join(project_root, 'app.py')],
    pathex=[project_root],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='期货公司日研报下载器',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # 保留控制台窗口，方便看到启动地址与日志
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
