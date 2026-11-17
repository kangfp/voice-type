#!/usr/bin/env bash
# 构建 voice-type 的 amd64 deb 包。
# 思路：构建期创建完整 venv（含 faster-whisper/modelscope 等二进制依赖），
# 整体装入 /opt/voice-type/venv；系统包只负责 GTK/PyGObject/工具类依赖。
# 用法：bash build-deb.sh [--pip-index URL]
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(sed -n 's/^Version: //p' "$PROJECT_DIR/packaging/control")"
ARCH="amd64"
BUILD_DIR="$PROJECT_DIR/build/deb"
PKG_ROOT="$BUILD_DIR/voice-type_${VERSION}_${ARCH}"
VENV="$PKG_ROOT/opt/voice-type/venv"
PIP_INDEX=()
if [ "${1:-}" = "--pip-index" ]; then
    PIP_INDEX=(-i "$2")
fi

echo "==> [1/6] 清理并创建打包目录"
rm -rf "$PKG_ROOT"
mkdir -p "$PKG_ROOT/DEBIAN" "$PKG_ROOT/opt/voice-type" \
    "$PKG_ROOT/usr/bin" "$PKG_ROOT/usr/share/applications" \
    "$PKG_ROOT/usr/share/icons/hicolor/scalable/apps" \
    "$PKG_ROOT/usr/lib/udev/rules.d" \
    "$PKG_ROOT/usr/share/doc/voice-type"

echo "==> [2/6] 创建 venv 并安装依赖"
if command -v uv >/dev/null 2>&1; then
    # uv 的 relocatable venv 不依赖 ensurepip，脚本自带路径无关 shebang
    uv venv --python python3.12 --system-site-packages --relocatable "$VENV"
    uv pip install --python "$VENV/bin/python" --quiet "${PIP_INDEX[@]}" \
        "faster-whisper>=1.1.0" "httpx>=0.27" "modelscope>=1.20"
    uv pip install --python "$VENV/bin/python" --quiet "${PIP_INDEX[@]}" "$PROJECT_DIR"
else
    python3.12 -m venv --copies --system-site-packages "$VENV"
    "$VENV/bin/pip" install --quiet --no-cache-dir "${PIP_INDEX[@]}" \
        "faster-whisper>=1.1.0" "httpx>=0.27" "modelscope>=1.20"
    "$VENV/bin/pip" install --quiet --no-cache-dir "${PIP_INDEX[@]}" "$PROJECT_DIR"
    "$VENV/bin/pip" uninstall -y --quiet pip setuptools >/dev/null 2>&1 || true
    rm -f "$VENV"/bin/pip "$VENV"/bin/pip3 "$VENV"/bin/pip3.12
fi
# 瘦身：去掉字节码缓存
find "$VENV" -name "__pycache__" -type d -prune -exec rm -rf {} + 2>/dev/null || true

echo "==> [3/6] 重写 venv 脚本 shebang 到目标路径"
for f in "$VENV"/bin/*; do
    if [ -f "$f" ] && head -c2 "$f" 2>/dev/null | grep -q '#!'; then
        sed -i "1s|^#!.*python.*|#!/opt/voice-type/venv/bin/python|" "$f"
    fi
done
# activate 脚本里的 VIRTUAL_ENV 路径也指到目标位置
for f in "$VENV"/bin/activate*; do
    [ -f "$f" ] && sed -i "s|VIRTUAL_ENV=.*|VIRTUAL_ENV=/opt/voice-type/venv|" "$f" || true
done

echo "==> [4/6] 安装包装脚本、桌面入口、图标、udev 规则、文档"
install -m 0755 "$PROJECT_DIR/packaging/voice-type.wrapper" "$PKG_ROOT/usr/bin/voice-type"
install -m 0644 "$PROJECT_DIR/packaging/io.github.voicetype.VoiceType.desktop" \
    "$PKG_ROOT/usr/share/applications/"
install -m 0644 "$PROJECT_DIR/icons/voice-type.svg" \
    "$PKG_ROOT/usr/share/icons/hicolor/scalable/apps/"
install -m 0644 "$PROJECT_DIR/packaging/udev/60-voice-type-uinput.rules" \
    "$PKG_ROOT/usr/lib/udev/rules.d/"
install -m 0644 "$PROJECT_DIR/packaging/copyright" "$PKG_ROOT/usr/share/doc/voice-type/"
install -m 0644 "$PROJECT_DIR/README.md" "$PKG_ROOT/usr/share/doc/voice-type/"

echo "==> [5/6] 安装 DEBIAN 控制文件"
install -m 0644 "$PROJECT_DIR/packaging/control" "$PKG_ROOT/DEBIAN/"
for script in postinst prerm postrm; do
    install -m 0755 "$PROJECT_DIR/packaging/$script" "$PKG_ROOT/DEBIAN/"
done
# Installed-Size（KB）
SIZE_KB=$(du -sk "$PKG_ROOT" | cut -f1)
echo "Installed-Size: $SIZE_KB" >> "$PKG_ROOT/DEBIAN/control"

echo "==> [6/6] 构建 deb"
dpkg-deb --build --root-owner-group "$PKG_ROOT" "$BUILD_DIR/" >/dev/null
DEB="$BUILD_DIR/voice-type_${VERSION}_${ARCH}.deb"
echo
echo "✓ 构建完成：$DEB"
ls -lh "$DEB" | awk '{print "  大小:", $5}'
echo "  安装测试：sudo dpkg -i $DEB && sudo apt-get install -f"