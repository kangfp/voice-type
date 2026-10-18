#!/usr/bin/env bash
# 移除 voice-type 的用户级安装（保留源代码目录和已下载模型）。
set -uo pipefail

BIN_DIR="$HOME/.local/bin"
DESKTOP_ID="io.github.voicetype.VoiceType"

"$BIN_DIR/voice-type" quit >/dev/null 2>&1 || true
"$BIN_DIR/voice-type" uninstall-hotkey >/dev/null 2>&1 || true

rm -f "$BIN_DIR/voice-type"
rm -f "$HOME/.local/share/applications/$DESKTOP_ID.desktop"
rm -f "$HOME/.config/autostart/voice-type.desktop"
rm -f "$HOME/.local/share/icons/hicolor/scalable/apps/voice-type.svg"
rm -f "${XDG_RUNTIME_DIR:-/tmp}/voice-type.sock"

echo "✓ 已移除程序、桌面入口、自启动项和全局快捷键。"
echo "  项目目录、虚拟环境和模型缓存没有删除。"
