# Spec de PyInstaller: un mismo archivo para Linux, Windows y macOS.
# Uso (desde host/): pyinstaller --noconfirm packaging/planta.spec
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files

HERE = Path(SPECPATH)
SRC = HERE.parent / "src"
VERSION = re.search(r'__version__ = "(.+)"', (SRC / "planta" / "__init__.py").read_text()).group(1)
ICON = HERE / {"win32": "icon.ico", "darwin": "icon.icns"}.get(sys.platform, "icon.png")

# La GUI no usa matplotlib (solo el ensayo estático de la CLI) ni estos módulos de Qt.
EXCLUDES = [
    "matplotlib", "tkinter", "IPython", "pytest",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtQuick", "PySide6.QtQml",
    "PySide6.QtQuickWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtMultimedia",
    "PySide6.QtPdf", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth",
    "PySide6.QtNetwork", "PySide6.QtSql", "PySide6.QtDesigner", "PySide6.QtHelp",
]

a = Analysis(
    [str(HERE / "planta_gui.py")],
    pathex=[str(SRC)],
    datas=collect_data_files("planta"),  # firmware/*.hex e ícono
    hiddenimports=["serial.tools.list_ports"],
    excludes=EXCLUDES,
)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="Planta",
          console=False, icon=str(ICON))
coll = COLLECT(exe, a.binaries, a.datas, name="Planta")

if sys.platform == "darwin":
    app = BUNDLE(
        coll, name="Planta.app", icon=str(ICON), bundle_identifier="ar.edu.ort.planta",
        info_plist={"CFBundleShortVersionString": VERSION, "NSHighResolutionCapable": True},
    )
