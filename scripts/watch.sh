#!/usr/bin/env bash
# watch.sh — 零 token 看护进程(借鉴 firstmate 的事件驱动监督)。
#
# 一个纯 bash 进程睡在后台盯条件,条件满足才把一条 [watcher] 消息注入**本会话**
# 唤醒 agent——代替"agent 自己轮询 read-screen"的烧 token 方案。
# 可靠性合约(firstmate 的 queue-before-notify):唤醒先写持久队列
# wake-queue/,注入成功(提交或已排队)才删;进程死了队列还在,醒来可对账。
#
# 用法(须在要被唤醒的会话里启动;watcher 继承该会话身份):
#   watch.sh idle <目标> [--flush '<签名>'] [--timeout <秒,默认1800>] [--note '<附言>']
#       等目标空闲(连续 2 次判闲,防单次快照误判)→ 有 --flush 先补交卡箱消息 → 唤醒我。
#       典型场景:send.sh 退出码 2(目标忙)之后接一句:
#         watch.sh idle <目标> --flush '<签名>'
#   watch.sh overdue [--age <分钟,默认30>] [--timeout <秒,默认14400>]
#       盯台账:我发出的 --ask 请求账龄超标且未提醒过 → 唤醒我去催办。每条只提醒一次。
#   watch.sh list                     列出活着的 watcher
#   watch.sh stop [idle|overdue|all]  停止本会话的 watcher
#
# 选项:
#   --once   只做一轮条件检查并打印判断结果,不后台化、不注入(调试用)
#
# 判忙口径与 send.sh 一致:语义状态文件(busy-hook.sh 写入)优先,unknown 才读屏。
set -euo pipefail

S_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CMUX=$(command -v cmux || echo /Applications/cmux.app/Contents/Resources/bin/cmux)
MSGDIR="${CMUX_MSG_DIR:-$HOME/.claude/cmux-messaging}"
WDIR="$MSGDIR/watchers"
QDIR="$MSGDIR/wake-queue"

die() { echo "$@" >&2; exit 4; }

id_json=$("$CMUX" identify 2>/dev/null || true)
SELF_REF=$(printf '%s' "$id_json" | awk -F'"' '/"surface_ref"/ { print $4; exit }')
[ -n "$SELF_REF" ] || die "✗ 拿不到本会话身份(须在 cmux 内运行)"

self_title() { "$S_DIR/send.sh" --list 2>/dev/null | awk -F'\t' -v r="$SELF_REF" '$1 == r { print $4; exit }'; }

# ── 判忙(与 send.sh 同口径):语义文件优先,unknown 读屏 ────────────────────────
busy_of() {  # <ref> <win> ; 退出码 0=忙 1=闲
  local f="$MSGDIR/busy/${1//:/_}" st ts
  if [ -f "$f" ]; then
    st=$(awk -F= '$1=="state"{print $2; exit}' "$f")
    ts=$(awk -F= '$1=="ts"{print $2; exit}' "$f")
    if [ -n "$ts" ] && [ $(( $(date +%s) - ts )) -lt 7200 ]; then
      [ "$st" = busy ] && return 0
      [ "$st" = idle ] && return 1
    fi
  fi
  "$CMUX" read-screen --window "$2" --surface "$1" --scrollback --lines 15 2>/dev/null \
    | grep -qE 'esc to interrupt|… \(' && return 0
  return 1
}

# ── 唤醒注入:先写队列再注入,提交(0)或已排队(2)都算送达,才删队列 ──────────────
wake_once() {  # <消息>
  local msg="[watcher] $1" qf rc=0
  mkdir -p "$QDIR"
  qf="$QDIR/$(date +%s)-$$"
  printf '%s\n' "$msg" > "$qf"
  SEND_SELF_OK=1 SEND_SETTLE=0 "$S_DIR/send.sh" "$SELF_REF" --no-from "$msg" >/dev/null 2>&1 || rc=$?
  if [ "$rc" -le 2 ] && [ "$rc" -ne 1 ]; then rm -f "$qf"; return 0; fi
  return 1
}
notify() {  # <消息> —— 注入失败(如 cmux 重启导致 ref 失效)时退避重试,队列文件兜底
  local n=0
  until wake_once "$1"; do
    n=$((n + 1)); [ "$n" -ge 10 ] && return 1
    sleep 30
  done
}

# ── idle:等目标空闲 ──────────────────────────────────────────────────────────
run_idle() {
  local streak=0 start now
  start=$(date +%s)
  while :; do
    now=$(date +%s)
    if [ $(( now - start )) -ge "$TIMEOUT" ]; then
      notify "等待 $TGT_TITLE 空闲已超时($(( TIMEOUT / 60 ))min),放弃看护。${NOTE:+备注: $NOTE}" || true
      exit 0
    fi
    if busy_of "$TGT_REF" "$TGT_WIN"; then streak=0; else streak=$(( streak + 1 )); fi
    if [ "$streak" -ge 2 ]; then
      if [ -n "$FLUSH_SIG" ]; then
        "$S_DIR/send.sh" "$TGT_REF" --flush "$FLUSH_SIG" >/dev/null 2>&1 || true
        notify "$TGT_TITLE 已空闲,已尝试补交卡箱消息(签名 $FLUSH_SIG),请核实。${NOTE:+备注: $NOTE}" || true
      else
        notify "$TGT_TITLE 已空闲。${NOTE:+备注: $NOTE}" || true
      fi
      exit 0
    fi
    sleep 10
  done
}

# ── overdue:盯台账账龄 ───────────────────────────────────────────────────────
run_overdue() {
  local me start now f corr from to ts age
  me=$(self_title); [ -n "$me" ] || exit 0
  start=$(date +%s)
  while :; do
    now=$(date +%s)
    [ $(( now - start )) -ge "$TIMEOUT" ] && exit 0     # 值完班静默下岗
    for f in "$MSGDIR"/pending/*; do
      [ -e "$f" ] || continue
      corr=$(basename "$f")
      from=$(awk -F= '$1=="from"{print $2; exit}' "$f")
      [ "$from" = "$me" ] || continue
      [ -f "$WDIR/nagged-$corr" ] && continue
      ts=$(awk -F= '$1=="ts"{print $2; exit}' "$f"); ts=${ts:-$now}
      age=$(( (now - ts) / 60 ))
      if [ "$age" -ge "$AGE_MIN" ]; then
        if notify "发给 $(awk -F= '$1=="to"{print $2; exit}' "$f") 的请求 $corr 已挂 ${age} 分钟未回——考虑催一下(读对方屏幕看卡在哪)或升级给用户。"; then
          touch "$WDIR/nagged-$corr"
        fi
      fi
    done
    sleep 60
  done
}

# ── 参数解析与调度 ────────────────────────────────────────────────────────────
CMD="${1:-}"; shift || true
LOCK="$WDIR/${SELF_REF//:/_}-$CMD.pid"

case "$CMD" in
  idle)
    TARGET="${1:-}"; [ -n "$TARGET" ] && [ "${TARGET#--}" = "$TARGET" ] || die "用法: watch.sh idle <目标> [--flush '<签名>'] [--timeout 秒] [--note '...'] [--once]"
    shift
    # 默认值取自环境:后台重入(WATCH_FG=1)时选项经环境变量传递,不能被重置
    FLUSH_SIG="${FLUSH_SIG:-}"; TIMEOUT="${TIMEOUT:-1800}"; NOTE="${NOTE:-}"; ONCE=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --flush)   FLUSH_SIG="${2:-}"; [ -n "$FLUSH_SIG" ] || die "--flush 缺少签名"; shift 2 ;;
        --timeout) TIMEOUT="${2:-}"; shift 2 ;;
        --note)    NOTE="${2:-}"; shift 2 ;;
        --once)    ONCE=1; shift ;;
        *) die "未知选项: $1" ;;
      esac
    done
    # 复用 send.sh 的目标解析(别名/标题/子串/重名拒绝),从 dry-run 输出取 ref/win/title
    line=$(SEND_SELF_OK=1 "$S_DIR/send.sh" "$TARGET" --dry-run x 2>&1 | head -1) || die "✗ 目标解析失败: $line"
    TGT_REF=$(printf '%s' "$line" | sed -n 's/^目标: \(surface:[0-9]*\) .*/\1/p')
    TGT_WIN=$(printf '%s' "$line" | sed -n 's/.*(\(window:[0-9]*\),.*/\1/p')
    TGT_TITLE=$(printf '%s' "$line" | sed -n 's/.*, \(.*\))$/\1/p')
    [ -n "$TGT_REF" ] && [ -n "$TGT_WIN" ] || die "✗ 目标解析失败: $line"
    if [ "$ONCE" = 1 ]; then
      if busy_of "$TGT_REF" "$TGT_WIN"; then echo "判定: $TGT_TITLE($TGT_REF)忙"; else echo "判定: $TGT_TITLE($TGT_REF)闲"; fi
      exit 0
    fi
    ;;
  overdue)
    AGE_MIN="${AGE_MIN:-30}"; TIMEOUT="${TIMEOUT:-14400}"; ONCE=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --age)     AGE_MIN="${2:-}"; shift 2 ;;
        --timeout) TIMEOUT="${2:-}"; shift 2 ;;
        --once)    ONCE=1; shift ;;
        *) die "未知选项: $1" ;;
      esac
    done
    if [ "$ONCE" = 1 ]; then
      me=$(self_title); now=$(date +%s); found=0
      for f in "$MSGDIR"/pending/*; do
        [ -e "$f" ] || continue
        from=$(awk -F= '$1=="from"{print $2; exit}' "$f")
        ts=$(awk -F= '$1=="ts"{print $2; exit}' "$f"); ts=${ts:-$now}
        age=$(( (now - ts) / 60 ))
        printf '判定: %s from=%s 账龄=%smin %s\n' "$(basename "$f")" "$from" "$age" \
          "$([ "$from" = "$me" ] && [ "$age" -ge "$AGE_MIN" ] && echo '→ 会触发催办' || echo '→ 不触发')"
        found=1
      done
      [ "$found" = 0 ] && echo "判定: 台账为空"
      exit 0
    fi
    ;;
  list)
    shopt -s nullglob; found=0
    for p in "$WDIR"/*.pid; do
      pid=$(cat "$p" 2>/dev/null) || continue
      if kill -0 "$pid" 2>/dev/null; then st="运行中"; else st="已死(残留 lock)"; fi
      printf '%-40s pid=%-8s %s\n' "$(basename "$p" .pid)" "$pid" "$st"; found=1
    done
    [ "$found" = 0 ] && echo "没有 watcher。"
    shopt -s nullglob
    for q in "$QDIR"/*; do [ -e "$q" ] && echo "⚠️ 未送达的唤醒: $q → $(cat "$q")"; done
    exit 0
    ;;
  stop)
    what="${1:-all}"; stopped=0
    shopt -s nullglob
    for p in "$WDIR/${SELF_REF//:/_}"-*.pid; do
      case "$what" in all) : ;; *) [ "$p" = "$WDIR/${SELF_REF//:/_}-$what.pid" ] || continue ;; esac
      pid=$(cat "$p" 2>/dev/null) || true
      [ -n "${pid:-}" ] && kill "$pid" 2>/dev/null || true
      rm -f "$p"; stopped=$((stopped + 1))
    done
    echo "✓ 已停止 $stopped 个 watcher"
    exit 0
    ;;
  *)
    die "用法: watch.sh idle|overdue|list|stop …(详见文件头)"
    ;;
esac

# ── 后台化:WATCH_FG=1 是重入标志(后台副本直接跑主循环)────────────────────────
mkdir -p "$WDIR"
if [ "${WATCH_FG:-0}" = 1 ]; then
  trap 'rm -f "$LOCK"' EXIT     # 退出(含超时/触发后)自清 lock,不留"已死"残骸
  "run_$CMD"
  exit 0
fi
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK")" 2>/dev/null; then
  die "✗ 本会话已有 $CMD watcher(pid $(cat "$LOCK"))在跑,先 watch.sh stop $CMD"
fi
LOG="$WDIR/${SELF_REF//:/_}-$CMD.log"
case "$CMD" in
  idle)    WATCH_FG=1 FLUSH_SIG="$FLUSH_SIG" TIMEOUT="$TIMEOUT" NOTE="$NOTE" \
           TGT_REF="$TGT_REF" TGT_WIN="$TGT_WIN" TGT_TITLE="$TGT_TITLE" \
           nohup "$0" idle "$TARGET" >>"$LOG" 2>&1 & ;;
  overdue) WATCH_FG=1 AGE_MIN="$AGE_MIN" TIMEOUT="$TIMEOUT" \
           nohup "$0" overdue >>"$LOG" 2>&1 & ;;
esac
echo $! > "$LOCK"
echo "✓ $CMD watcher 已启动(pid $!,日志 $LOG)"
