# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_submodules


root = Path(SPECPATH)
datas = [(str(root / "src" / "stemlift" / "static"), "stemlift/static")]
binaries = []
hiddenimports = []
for package in ("demucs", "sphn", "lameenc"):
    package_datas, package_binaries, package_hiddenimports = collect_all(package)
    datas.extend(package_datas)
    binaries.extend(package_binaries)
    hiddenimports.extend(package_hiddenimports)
hiddenimports.extend(collect_submodules("demucs"))

cache_root = root / ".desktop-cache"
for source_name, destination_name in (
    ("torch", "torch"),
    ("huggingface", "huggingface"),
):
    source = cache_root / source_name
    if source.is_dir():
        datas.append((str(source), destination_name))

a = Analysis(
    [str(root / "src" / "stemlift" / "frozen.py")],
    pathex=[str(root / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="stemlift",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
