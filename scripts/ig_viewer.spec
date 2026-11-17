"""Shared desktop packaging with release-only Qt WebEngine resources."""

from pathlib import Path
import sys

project_dir = Path(SPECPATH).parent
icon_path = project_dir / "igviewer" / "assets" / "ig-viewer.ico"

analysis = Analysis(
    [str(project_dir / "main.py")],
    pathex=[str(project_dir)],
    datas=[(str(icon_path), "igviewer/assets")],
    binaries=[],
    hiddenimports=[],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

# Retain all release resources. Remove a debug variant only when its matching
# release file is present in the same collection.
resource_names = {entry[0] for entry in analysis.datas}
analysis.datas = [
    entry for entry in analysis.datas
    if not (
        entry[0].endswith((".debug.pak", ".debug.bin"))
        and entry[0].replace(".debug.pak", ".pak").replace(".debug.bin", ".bin") in resource_names
    )
]

archive = PYZ(analysis.pure)
executable = EXE(
    archive,
    analysis.scripts,
    [],
    exclude_binaries=True,
    name="IG Viewer",
    console=False,
    debug=False,
    strip=False,
    upx=False,
    icon=str(icon_path),
)
collection = COLLECT(
    executable, analysis.binaries, analysis.datas,
    strip=False, upx=False, name="IG Viewer",
)
if sys.platform == "darwin":
    app = BUNDLE(collection, name="IG Viewer.app", bundle_identifier="com.xuan.igviewer")
