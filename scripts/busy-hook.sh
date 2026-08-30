#!/usr/bin/env bash
# busy-hook.sh — Claude Code hooks 回调:把本会话的忙/闲状态写到共享状态文件。
#
# 语义化忙闲(借鉴 firstmate 的 busy-state 合约):状态来自 harness 自己的生命周期
# 事件,不靠刮屏猜 spinner 词。由 ~/.claude/settings.json 的三个 hooks 调用:
#   UserPromptSubmit → busy-hook.sh busy    (turn 开始)
#   Stop             → busy-hook.sh idle    (turn 结束)
#   SessionEnd       → busy-hook.sh end     (会话结束,删状态文件)
#
# 键 = cmux 数字 surface ref(经 cmux identify),与 send.sh resolve 的目标同域。
# 消费方(send.sh)的三态语义:文件缺失/过期(>2h)= unknown,绝不当 idle 用;
# unknown 时退回读屏启发式。所以本脚本任何失败都静默退出——宁缺毋错。
#
# 不在 cmux 里(无 CMUX_SURFACE_ID)、identify 失败(如 cmux app 已重启,旧环境
# 变量失效)→ 静默退出,不留脏文件。
set -u

STATE_DIR="${CMUX_MSG_DIR:-$HOME/.claude/cmux-messaging}/busy"
[ -n "${CMUX_SURFACE_ID:-}" ] || exit 0

CMUX_BIN=$(command -v cmux || echo /Applications/cmux.app/Contents/Resources/bin/cmux)
ref=$("$CMUX_BIN" identify 2>/dev/null | awk -F'"' '/"surface_ref"/ { print $4; exit }')
[ -n "$ref" ] || exit 0

f="$STATE_DIR/${ref//:/_}"      # surface:28 → surface_28
case "${1:-}" in
  busy|idle)
    mkdir -p "$STATE_DIR"
    printf 'state=%s\nts=%s\n' "$1" "$(date +%s)" > "$f"
    ;;
  end)
    rm -f "$f"
    ;;
  *)
    echo "用法: busy-hook.sh busy|idle|end" >&2
    exit 2
    ;;
esac
exit 0
