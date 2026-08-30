#!/usr/bin/env bash
# pending.sh — 待回复台账:列出/消掉 send.sh --ask 创建的未决请求。
#
# 机制(借鉴 firstmate 的 pending-reply 合约):
#   · send.sh --ask 在**投递前**落一条持久记录到 pending/<corr>;
#   · 送达成功 ≠ 已回复——只有对方用 send.sh <发起方> --reply <corr> 回复成功,
#     记录才移入 done/(同机共享文件系统,回复方直接消台账);
#   · 没有 watcher,所以规程是:**每次会话醒来先跑 pending.sh**,
#     看"谁欠我回复"(催/升级)和"我欠谁回复"(赶紧回)。
#
# 用法:
#   pending.sh [list]              列出所有未决请求(含账龄)
#   pending.sh list --group <组>   组级台账:只看组内成员(from 或 to)的未决请求
#                                  ——协调者的全局视图,不用在脑子里维护这张表
#   pending.sh resolve <corr>      手动消一条(对方没用 --reply 回、但事实上已回时)
set -euo pipefail

MSGDIR="${CMUX_MSG_DIR:-$HOME/.claude/cmux-messaging}"
PEND="$MSGDIR/pending"
DONE="$MSGDIR/done"
REGISTRY="${CMUX_SESSIONS_CONF:-$HOME/.claude/cmux-sessions.conf}"

field() { awk -F= -v k="$2" '$1==k { sub(/^[^=]*=/, ""); print; exit }' "$1"; }

cmd=list; corr=""; GROUP=""
while [ $# -gt 0 ]; do
  case "$1" in
    list)    cmd=list; shift ;;
    resolve) cmd=resolve; corr="${2:-}"; shift; [ $# -gt 0 ] && shift ;;
    --group) GROUP="${2:-}"; [ -n "$GROUP" ] || { echo "--group 需要组名" >&2; exit 2; }; shift 2 ;;
    *)       echo "用法: pending.sh [list] [--group <组>] | pending.sh resolve <corr>" >&2; exit 2 ;;
  esac
done

# 组成员集合 = 别名 + tab 标题(台账 from/to 记的是标题,别名一并算上更鲁棒)
MEMBERS=""
if [ -n "$GROUP" ]; then
  [ -f "$REGISTRY" ] || { echo "✗ 找不到注册表 $REGISTRY——--group 依赖它确定组成员" >&2; exit 2; }
  MEMBERS=$(awk -F'|' -v g="$GROUP" '
    function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
    /^[ \t]*#/ || /^[ \t]*$/ { next }
    trim($4) == g { print trim($1); print trim($2) }' "$REGISTRY" | sort -u)
  [ -n "$MEMBERS" ] || { echo "✗ 组 '$GROUP' 里没有成员" >&2; exit 2; }
fi
in_scope() { [ -z "$MEMBERS" ] || printf '%s\n' "$MEMBERS" | grep -qxF "$1"; }

case "$cmd" in
  list)
    # 不用数组:bash 3.2 在 set -u 下展开空数组会报 unbound variable
    shopt -s nullglob
    shown=0; now=$(date +%s)
    for f in "$PEND"/*; do
      from=$(field "$f" from); to=$(field "$f" to)
      in_scope "$from" || in_scope "$to" || continue
      if [ "$shown" = 0 ]; then
        printf '%-22s %-14s %-14s %-8s %s\n' 'CORR' 'FROM' 'TO' '账龄' '消息摘要'
      fi
      shown=1
      corr=$(basename "$f")
      ts=$(field "$f" ts); ts=${ts:-$now}
      age=$(( (now - ts) / 60 ))
      if [ "$age" -ge 60 ]; then age_s="$((age / 60))h$((age % 60))m"; else age_s="${age}m"; fi
      printf '%-22s %-14s %-14s %-8s %.60s\n' "$corr" "$from" "$to" "$age_s" "$(field "$f" msg)"
    done
    if [ "$shown" = 0 ]; then
      echo "✓ 台账为空,没有${GROUP:+组 $GROUP 的}未决请求。"
      exit 0
    fi
    echo
    echo "提示: 回复方式 send.sh <FROM列的会话> --reply <CORR> '<答复>';手动消账 pending.sh resolve <CORR>"
    ;;
  resolve)
    [ -n "$corr" ] || { echo "用法: pending.sh resolve <corr>" >&2; exit 2; }
    f="$PEND/$corr"
    [ -f "$f" ] || { echo "✗ 未找到未决记录: $corr(可能已消或从未存在)" >&2; exit 1; }
    mkdir -p "$DONE"
    printf 'resolved_at=%s\nresolved_by=manual\n' "$(date +%s)" >> "$f"
    mv "$f" "$DONE/$corr"
    echo "✓ 已消账: $corr"
    ;;
esac
