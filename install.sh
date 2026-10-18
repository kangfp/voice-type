#!/usr/bin/env bash
# 安装语音输入到当前用户环境。请勿用 sudo 运行本脚本。
set -euo pipefail

if [ "$(id -u)" -eq 0 ]; then
    echo "✗ 请不要用 sudo 运行本脚本；需要时脚本会自己调用 sudo 安装系统包。" >&2
    exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
APPLICATIONS_DIR="$HOME/.local/share/applications"
ICONS_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/voice-type"
DESKTOP_ID="io.github.voicetype.VoiceType"

mkdir -p "$BIN_DIR" "$APPLICATIONS_DIR" "$ICONS_DIR" "$STATE_DIR"

echo "==> [1/6] 安装系统依赖（需要 sudo 密码）"
sudo apt-get update -qq || echo "! apt update 失败，继续尝试安装"
sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    ydotool \
    wl-clipboard \
    libnotify-bin \
    pipewire-bin \
    libgtk-4-1 \
    gir1.2-gtk-4.0 \
    gir1.2-adw-1 \
    python3-gi

NEED_RELOGIN=0
if ! id -nG "$USER" | tr ' ' '\n' | grep -qx input; then
    echo "    将当前用户加入 input 组（ydotool 写 /dev/uinput 所需）"
    sudo usermod -aG input "$USER"
    NEED_RELOGIN=1
fi

echo "==> [2/6] 创建 Python 虚拟环境（复用系统 PyGObject）"
if ! command -v uv >/dev/null 2>&1; then
    echo "✗ 未找到 uv。请先安装：https://docs.astral.sh/uv/getting-started/installation/" >&2
    exit 1
fi
if [ -f "$PROJECT_DIR/.venv/pyvenv.cfg" ] && \
   ! grep -q "include-system-site-packages = true" "$PROJECT_DIR/.venv/pyvenv.cfg"; then
    echo "    现有 .venv 未启用系统包（PyGObject），重建"
    rm -rf "$PROJECT_DIR/.venv"
fi
uv venv --system-site-packages --allow-existing --python python3.12 "$PROJECT_DIR/.venv"
uv pip install --python "$PROJECT_DIR/.venv/bin/python" -e "$PROJECT_DIR"

echo "==> [3/6] 安装启动脚本"
cat > "$BIN_DIR/voice-type" <<EOF
#!/usr/bin/env bash
export PYTHONPATH="/usr/lib/python3/dist-packages\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$PROJECT_DIR/.venv/bin/python" -m voice_type "\$@"
EOF
chmod +x "$BIN_DIR/voice-type"

echo "==> [4/6] 安装图标与桌面入口"
install -m 0644 "$PROJECT_DIR/icons/voice-type.svg" "$ICONS_DIR/voice-type.svg"
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true

cat > "$APPLICATIONS_DIR/$DESKTOP_ID.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=语音输入
Comment=按快捷键说话，文字自动输入当前应用
Exec=$BIN_DIR/voice-type gui
Icon=voice-type
Terminal=false
Categories=Utility;Accessibility;
StartupWMClass=$DESKTOP_ID
EOF

# 不安装自启动项：需要时从应用菜单打开，关闭窗口即退出。

echo "==> [5/6] 检查安装结果"
if "$BIN_DIR/voice-type" status >/dev/null 2>&1; then
    echo "    后台服务当前已在运行"
else
    echo "    后台服务未启动（需要时从应用菜单打开「语音输入」）"
fi

echo "==> [6/6] 注册备用快捷键 Super+Alt+V"
"$BIN_DIR/voice-type" install-hotkey || {
    echo "! 快捷键安装失败，可稍后在窗口里点「安装/更新」重试。" >&2
}

echo
echo "✓ 安装完成"
echo "  • 使用：按住键盘右侧 Alt 说话，松开自动识别并输入"
echo "  • 备用：按 Super+Alt+V 开始/停止（适合长段听写）"
echo "  • 注意：右 Alt 也是 AltGr，输入特殊字符时可能误触；可在窗口里关闭该功能"
echo "  • 打开：在应用菜单搜索「语音输入」，或运行 voice-type show"
echo "  • 退出：直接关闭窗口（不会开机自启动，也不会留在后台）"
echo "  • 自检：voice-type doctor"
echo "  • 首次本地识别会下载 large-v3-turbo 模型（约 1.6GB），请耐心等待"
echo "  • 若快捷键不生效，请重新登录一次 GNOME 会话"
if [ "$NEED_RELOGIN" -eq 1 ]; then
    echo
    echo "! 你刚被加入 input 组，必须注销并重新登录后，文字注入和右 Alt 监听才会生效。"
fi
