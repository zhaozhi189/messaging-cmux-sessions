#!/usr/bin/env bash
# 生命周期状态采用原子写入，并绑定当前会话的结构身份。
set -euo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
case "${1:-}" in
  busy|idle) python3 "$HERE/protocol.py" busy-write "$1" ;;
  end) python3 "$HERE/protocol.py" busy-write unknown ;;
  *) echo "用法: busy-hook.sh busy|idle|end" >&2; exit 4 ;;
esac
