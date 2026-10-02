#!/usr/bin/env bash
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$root"
work="$(mktemp -d)"
trap 'rm -rf -- "$work"' EXIT
"${PYTHON:-python3}" -m build --wheel --outdir "$work/wheels"
wheel=("$work"/wheels/kalinka_plugin_roon-*-py3-none-any.whl)
test "${#wheel[@]}" -eq 1
name="$(basename -- "${wheel[0]}")"
version="${name#kalinka_plugin_roon-}"
version="${version%-py3-none-any.whl}"
case "$version" in
    *.dev*)
        echo "Warning: building untagged dev version $version." >&2
        echo "For a release build, check out the release tag first:" >&2
        echo "    git fetch --tags --force && git checkout kalinka-plugin-roon-v<X.Y.Z>" >&2
        ;;
esac
pkg="$work/pkg"
extension="$pkg/usr/libexec/kalinka-plugin-roon/extension"
mkdir -p "$pkg/DEBIAN" "$pkg/opt/kalinka/wheels" "$extension" \
    "$pkg/usr/lib/systemd/system/kalinka.service.d" \
    "$pkg/usr/share/doc/kalinka-plugin-roon"
cp "${wheel[0]}" "$pkg/opt/kalinka/wheels/"
cp src/kalinka_plugin_roon/extension/*.js src/kalinka_plugin_roon/extension/*.json "$extension/"
(cd "$extension" && npm ci --omit=dev --ignore-scripts --no-audit --no-fund)
sed "s/@VERSION@/$version/g" debian/control.in > "$pkg/DEBIAN/control"
cp debian/triggers "$pkg/DEBIAN/"
install -m 755 debian/prerm debian/postinst debian/postrm "$pkg/DEBIAN/"
install -m 644 debian/roon-audio.conf "$pkg/usr/lib/systemd/system/kalinka.service.d/roon-audio.conf"
install -m 644 README.md LICENSE THIRD_PARTY.md "$pkg/usr/share/doc/kalinka-plugin-roon/"
mkdir -p dist
dpkg-deb --root-owner-group -Zxz --build "$pkg" "dist/kalinka-plugin-roon_${version}_all.deb"
