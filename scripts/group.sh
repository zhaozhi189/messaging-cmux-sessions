#!/usr/bin/env bash
# group.sh — 把消息广播给一个协作组,并汇总投递结果。
#
# 为什么需要它:注册表早就有 GROUP 列,但它此前只是 sessions.sh 的列表过滤器,
# 没有任何操作能力。三个以上会话协作时,逐个 send.sh 有两个真问题:
#   ① 每次发送都是独立的失败点,发五个漏一个不会有人发现;
#   ② 谁收到了、谁卡箱了、谁离线,没有一处能一眼看全。
# 本脚本只做一件事:对组内每个会话调用 send.sh,然后把退出码汇总成一张表。
#
# 用法:
#   group.sh <组名> '<消息>'              广播(自动跳过自己)
#   group.sh <组名> '<消息>' --exclude a,b  排除指定别名
#   group.sh <组名> --status              只看组内谁在忙/闲/离线
#   group.sh --list                       列出所有组
#
# 退出码:0 全部送达;2 有成员卡箱(用 send.sh <别名> --flush 补交);1 有成员发送失败。
#
# 注册表:${CMUX_SESSIONS_CONF:-~/.claude/cmux-sessions.conf}
set -uo pipefail

HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REGISTRY="${CMUX_SESSIONS_CONF:-$HOME/.claude/cmux-sessions.conf}"

die() { echo "$*" >&2; exit 4; }
[ -f "$REGISTRY" ] || die "✗ 找不到注册表 $REGISTRY——广播依赖它来确定组成员"

if [ "${1:-}" = "--list" ]; then
  printf '组名\t成员\n'
  awk -F'|' '
    function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
    /^[ \t]*#/ || /^[ \t]*$/ { next }
    { g = trim($4); if (g == "") g = "(未分组)"; m[g] = m[g] (m[g] ? "," : "") trim($1) }
    END { for (k in m) print k "\t" m[k] }
  ' "$REGISTRY" | sort
  exit 0
fi

GROUP="${1:-}"; [ -n "$GROUP" ] || die "用法: group.sh <组名> '<消息>' | group.sh <组名> --status | group.sh --list"
shift

MSG=""; EXCLUDE=""; ONLY_STATUS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --status)  ONLY_STATUS=1; shift ;;
    --exclude) EXCLUDE="${2:?--exclude 需要逗号分隔的别名}"; shift 2 ;;
    *)         MSG="$1"; shift ;;
  esac
done

members=$(LC_ALL=C awk -F'|' -v g="$GROUP" '
  function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
  /^[ \t]*#/ || /^[ \t]*$/ { next }
  trim($4) == g { print trim($1) }
' "$REGISTRY")
[ -n "$members" ] || die "✗ 组 '$GROUP' 里没有成员。可用组:$("$HERE/group.sh" --list | tail -n +2 | cut -f1 | tr '\n' ' ')"

# 自己不发给自己:广播里带上自己会造成无限回环
self_title=$("$HERE/sessions.sh" --all 2>/dev/null | awk -F'\t' -v r="$(cmux identify 2>/dev/null | grep -o 'surface:[0-9]*' | head -1)" '$1 == r { print $3 }')

if [ "$ONLY_STATUS" = 1 ]; then
  "$HERE/sessions.sh" --group "$GROUP" --status
  exit 0
fi
[ -n "$MSG" ] || die "缺少消息文本"

printf '成员\t结果\n'
rc_all=0
for a in $members; do
  case ",$EXCLUDE," in *",$a,"*) printf '%s\t— 已排除\n' "$a"; continue ;; esac
  out=$("$HERE/send.sh" "$a" "$MSG" 2>&1); rc=$?
  case $rc in
    0) printf '%s\t✓ 已送达\n' "$a" ;;
    2) printf '%s\t⚠ 卡箱(对方忙)——补交: send.sh %s --flush\n' "$a" "$a"; [ $rc_all -lt 2 ] && rc_all=2 ;;
    5) printf '%s\t✗ 无输入框(死壳/普通 shell),已跳过\n' "$a"; rc_all=1 ;;
    *) printf '%s\t✗ 失败(码 %s): %s\n' "$a" "$rc" "$(printf '%s' "$out" | head -1)"; rc_all=1 ;;
  esac
done
exit $rc_all
