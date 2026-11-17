#!/usr/bin/env bash
set -euo pipefail

# Build an unsigned local macOS application. No Instagram exports or caches are
# included: the application keeps its state under Application Support at runtime.
project_dir="$(cd "$(dirname "$0")/.." && pwd)"
cd "$project_dir"
build_dir="$(mktemp -d "${TMPDIR:-/tmp}/ig-viewer-build.XXXXXX")"
trap 'rm -rf "$build_dir"' EXIT

python3 -m pip install -r requirements-dev.txt
python3 -m PyInstaller \
  --noconfirm \
  --workpath "$build_dir/work" \
  --distpath "$build_dir/dist" \
  scripts/ig_viewer.spec

app_path="$build_dir/dist/IG Viewer.app"
# Build and sign outside synced folders. iCloud/File Provider may attach Finder
# metadata to an .app in-place, which invalidates an otherwise correct signature.
# Qt WebEngine can include optional Helper symlinks that are absent on a given
# architecture. `xattr -r` reports those as missing after handling the real
# files, so do not treat that cleanup warning as a failed build.
xattr -cr "$app_path" 2>/dev/null || true
if codesign --force --deep --sign - "$app_path" && codesign --verify --deep --strict "$app_path"; then
  signing_status="ad-hoc signature verified"
else
  # Qt WebEngine's nested framework layout can reject a second deep-sign pass
  # on this local PyInstaller/Python combination. Keep the usable local build,
  # but never present this fallback as notarized or signature-verified.
  signing_status="signature verification skipped (open locally with Finder right-click if macOS asks)"
fi
mkdir -p "$project_dir/dist"
archive_path="$project_dir/dist/IG Viewer.zip"
rm -f "$archive_path"
ditto -c -k --sequesterRsrc --keepParent "$app_path" "$archive_path"
echo "Built archive: $archive_path ($signing_status)"
