from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata
from pathlib import Path

datas = [("README.md", "."), ("ROCKFORM-QUICK-START.md", ".")]
binaries = []
hiddenimports = ["scipy.spatial", "scipy.sparse.csgraph", "scipy.spatial.transform", "scipy.special", "networkx",
                 "PySide6.QtOpenGLWidgets", "PySide6.QtOpenGL", "vtkmodules.all",
                 "trimesh.exchange.obj", "trimesh.exchange.stl", "trimesh.path.exchange.svg_io"]
for package in ["pyvista", "pyvistaqt", "rtree", "manifold3d", "fast_simplification"]:
    d, b, h = collect_all(package)
    datas += d
    binaries += b
    hiddenimports += h
datas += collect_data_files("trimesh")
for package in ["numpy", "scipy", "trimesh", "manifold3d", "shapely", "rtree", "PySide6",
                "PySide6_Essentials", "PySide6_Addons", "shiboken6", "pyvista", "pyvistaqt",
                "vtk", "fast-simplification", "matplotlib", "pillow", "QtPy", "networkx"]:
    datas += copy_metadata(package)

a = Analysis(["launch.pyw"], pathex=[SPECPATH], binaries=binaries, datas=datas,
             hiddenimports=hiddenimports, hookspath=[], hooksconfig={"matplotlib": {"backends": ["Agg"]}},
             runtime_hooks=[], excludes=["tkinter", "IPython", "pytest", "PyQt5", "PyQt6"], noarchive=False)
# Qt 6.11 uses Windows' system ICU ABI. Do not accidentally bundle an unrelated
# versioned ICU found on the build host's PATH (e.g. Poppler's ICU 78 DLLs).
a.binaries = [entry for entry in a.binaries if Path(entry[0]).name.lower() not in {"icuuc.dll", "icudt78.dll"}]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Rockform", debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="Rockform")
