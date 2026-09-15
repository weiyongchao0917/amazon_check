# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller one-folder build definition for the order conversion GUI."""
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

project_root = Path(SPECPATH)
datas = [(str(project_root / "templates" / "花轮模板.xlsx"), "templates")]
binaries = []
hiddenimports = ["certifi", "openpyxl", "yaml", "tzdata"]
for package in ("curl_cffi",):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden
datas += collect_data_files("certifi") + collect_data_files("tzdata")
hiddenimports += collect_submodules("openpyxl") + collect_submodules("yaml")

a = Analysis(
    [str(project_root / "order_app.py")], pathex=[str(project_root)],
    binaries=binaries, datas=datas, hiddenimports=sorted(set(hiddenimports)),
    hookspath=[], hooksconfig={}, runtime_hooks=[],
    excludes=["pytest", "test", "tests", "unittest", "pandas", "scipy", "matplotlib", "IPython", "jupyter", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="OrderToHualun", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=True, console=False,
          disable_windowed_traceback=False, target_arch=None, codesign_identity=None,
          entitlements_file=None)
COLLECT(exe, a.binaries, a.datas, strip=False, upx=True, upx_exclude=[], name="OrderToHualun")
