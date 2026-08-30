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
#   pending.sh [list]        列出所有未决请求(含账龄)
#   pending.sh resolve <corr>  手动消一条(对方没用 --reply 回、但事实上已回时)
set -euo pipefail

MSGDIR="${CMUX_MSG_DIR:-$HOME/.claude/cmux-messaging}"
PEND="$MSGDIR/pending"
DONE="$MSGDIR/done"

field() { awk -F= -v k="$2" '$1==k { sub(/^[^=]*=/, ""); print; exit }' "$1"; }

cmd="${1:-list}"
case "$cmd" in
  list)
    shopt -s nullglob
    files=("$PEND"/*)
    if [ ${#files[@]} -eq 0 ]; then
      echo "✓ 台账为空,没有未决请求。"
      exit 0
    fi
    now=$(date +%s)
    printf '%-22s %-14s %-14s %-8s %s\n' 'CORR' 'FROM' 'TO' '账龄' '消息摘要'
    for f in "${files[@]}"; do
      corr=$(basename "$f")
      ts=$(field "$f" ts); ts=${ts:-$now}
      age=$(( (now - ts) / 60 ))
      if [ "$age" -ge 60 ]; then age_s="$((age / 60))h$((age % 60))m"; else age_s="${age}m"; fi
      printf '%-22s %-14s %-14s %-8s %.60s\n' \
        "$corr" "$(field "$f" from)" "$(field "$f" to)" "$age_s" "$(field "$f" msg)"
    done
    echo
    echo "提示: 回复方式 send.sh <FROM列的会话> --reply <CORR> '<答复>';手动消账 pending.sh resolve <CORR>"
    ;;
  resolve)
    corr="${2:-}"
    [ -n "$corr" ] || { echo "用法: pending.sh resolve <corr>" >&2; exit 2; }
    f="$PEND/$corr"
    [ -f "$f" ] || { echo "✗ 未找到未决记录: $corr(可能已消或从未存在)" >&2; exit 1; }
    mkdir -p "$DONE"
    printf 'resolved_at=%s\nresolved_by=manual\n' "$(date +%s)" >> "$f"
    mv "$f" "$DONE/$corr"
    echo "✓ 已消账: $corr"
    ;;
  *)
    echo "用法: pending.sh [list] | pending.sh resolve <corr>" >&2
    exit 2
    ;;
esac
