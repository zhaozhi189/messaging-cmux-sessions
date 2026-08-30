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
#       idle watcher 按(会话,目标)记一个——同时盯多个目标就启动多个,互不挤占。
#   watch.sh idle --group <组> [--timeout] [--note]
#       盯整个协作组:组内**任一成员**空闲就唤醒,并在唤醒消息里说明是谁。
#       协调者语义:不用在多个目标之间反复 stop/start 单目标 watcher。
#   watch.sh overdue [--age <分钟,默认30>] [--timeout <秒,默认14400>] [--group <组>]
#       盯台账:我发出的 --ask 请求账龄超标且未提醒过 → 唤醒我去催办。每条只提醒一次。
#       --group 把范围从"我发出的"扩成"组内成员发出的"(组级视角,配 pending.sh --group)。
#   watch.sh list                     列出活着的 watcher
#   watch.sh stop [idle|overdue|all]  停止本会话的 watcher(idle 会停掉全部 idle watcher)
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

# ── idle:等目标空闲(单目标或组内任一成员;TGT_LIST 每行 ref|win|title)────────
run_idle() {
  local start now i n ref win title
  local refs=() wins=() titles=() streaks=()
  while IFS='|' read -r ref win title; do
    [ -n "$ref" ] || continue
    refs+=("$ref"); wins+=("$win"); titles+=("$title"); streaks+=(0)
  done <<EOF
$TGT_LIST
EOF
  n=${#refs[@]}
  start=$(date +%s)
  while :; do
    now=$(date +%s)
    if [ $(( now - start )) -ge "$TIMEOUT" ]; then
      notify "等待 $WATCH_DESC 空闲已超时($(( TIMEOUT / 60 ))min),放弃看护。${NOTE:+备注: $NOTE}" || true
      exit 0
    fi
    i=0
    while [ "$i" -lt "$n" ]; do
      if busy_of "${refs[$i]}" "${wins[$i]}"; then
        streaks[$i]=0
      else
        streaks[$i]=$(( ${streaks[$i]} + 1 ))
      fi
      # 连续 2 次判闲才触发,防单次快照误判;组模式下先闲下来的成员先唤醒
      if [ "${streaks[$i]}" -ge 2 ]; then
        if [ -n "$FLUSH_SIG" ]; then
          "$S_DIR/send.sh" "${refs[$i]}" --flush "$FLUSH_SIG" >/dev/null 2>&1 || true
          notify "${titles[$i]} 已空闲,已尝试补交卡箱消息(签名 $FLUSH_SIG),请核实。${NOTE:+备注: $NOTE}" || true
        else
          notify "${titles[$i]} 已空闲${GROUP:+(组 $GROUP 中最先空闲的成员)}。${NOTE:+备注: $NOTE}" || true
        fi
        exit 0
      fi
      i=$(( i + 1 ))
    done
    sleep 10
  done
}

# ── overdue:盯台账账龄 ───────────────────────────────────────────────────────
# 默认只盯"我发出的";--group 时盯"组内成员发出的"(OVERDUE_SCOPE = 别名+标题列表)
in_overdue_scope() {  # <from>
  if [ -n "${OVERDUE_SCOPE:-}" ]; then
    printf '%s\n' "$OVERDUE_SCOPE" | LC_ALL=C grep -qxF "$1"
  else
    [ -n "$ME" ] && [ "$1" = "$ME" ]
  fi
}
run_overdue() {
  local start now f corr from ts age
  ME=$(self_title)
  [ -n "$ME" ] || [ -n "${OVERDUE_SCOPE:-}" ] || exit 0
  start=$(date +%s)
  while :; do
    now=$(date +%s)
    [ $(( now - start )) -ge "$TIMEOUT" ] && exit 0     # 值完班静默下岗
    for f in "$MSGDIR"/pending/*; do
      [ -e "$f" ] || continue
      corr=$(basename "$f")
      from=$(awk -F= '$1=="from"{print $2; exit}' "$f")
      in_overdue_scope "$from" || continue
      [ -f "$WDIR/nagged-$corr" ] && continue
      ts=$(awk -F= '$1=="ts"{print $2; exit}' "$f"); ts=${ts:-$now}
      age=$(( (now - ts) / 60 ))
      if [ "$age" -ge "$AGE_MIN" ]; then
        if notify "${GROUP:+[组 $GROUP] }$from 发给 $(awk -F= '$1=="to"{print $2; exit}' "$f") 的请求 $corr 已挂 ${age} 分钟未回——考虑催一下(读对方屏幕看卡在哪)或升级给用户。"; then
          touch "$WDIR/nagged-$corr"
        fi
      fi
    done
    sleep 60
  done
}

# ── 参数解析与调度 ────────────────────────────────────────────────────────────
CMD="${1:-}"; shift || true

# 复用 send.sh 的目标解析(别名/标题/子串/重名拒绝),输出一行 ref|win|title
resolve_one() {  # <目标>
  local line ref win title
  line=$(SEND_SELF_OK=1 "$S_DIR/send.sh" "$1" --dry-run x 2>&1 | head -1) || return 1
  ref=$(printf '%s' "$line" | sed -n 's/^目标: \(surface:[0-9]*\) .*/\1/p')
  win=$(printf '%s' "$line" | sed -n 's/.*(\(window:[0-9]*\),.*/\1/p')
  title=$(printf '%s' "$line" | sed -n 's/.*, \(.*\))$/\1/p')
  [ -n "$ref" ] && [ -n "$win" ] || return 1
  printf '%s|%s|%s' "$ref" "$win" "$title"
}

# 从注册表取组成员别名(与 group.sh 同口径)
group_members() {  # <组名>
  local reg="${CMUX_SESSIONS_CONF:-$HOME/.claude/cmux-sessions.conf}"
  [ -f "$reg" ] || return 1
  LC_ALL=C awk -F'|' -v g="$1" '
    function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
    /^[ \t]*#/ || /^[ \t]*$/ { next }
    trim($4) == g { print trim($1) }' "$reg"
}

case "$CMD" in
  idle)
    # 默认值取自环境:后台重入(WATCH_FG=1)时选项经环境变量传递,不能被重置
    FLUSH_SIG="${FLUSH_SIG:-}"; TIMEOUT="${TIMEOUT:-1800}"; NOTE="${NOTE:-}"
    GROUP="${GROUP:-}"; TARGET="${TARGET:-}"; ONCE=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --group)   GROUP="${2:-}"; [ -n "$GROUP" ] || die "--group 缺少组名"; shift 2 ;;
        --flush)   FLUSH_SIG="${2:-}"; [ -n "$FLUSH_SIG" ] || die "--flush 缺少签名"; shift 2 ;;
        --timeout) TIMEOUT="${2:-}"; shift 2 ;;
        --note)    NOTE="${2:-}"; shift 2 ;;
        --once)    ONCE=1; shift ;;
        --*)       die "未知选项: $1" ;;
        *)         TARGET="$1"; shift ;;
      esac
    done
    if [ -n "$GROUP" ]; then
      [ -z "$TARGET" ]    || die "idle: <目标> 与 --group 二选一"
      [ -z "$FLUSH_SIG" ] || die "idle --group 不支持 --flush(补交是针对单目标卡箱消息的)"
    else
      [ -n "$TARGET" ] || die "用法: watch.sh idle <目标>|--group <组> [--flush '<签名>'] [--timeout 秒] [--note '...'] [--once]"
    fi
    # 后台重入时 TGT_LIST 已由环境带入,不重复解析
    if [ -z "${TGT_LIST:-}" ]; then
      if [ -n "$GROUP" ]; then
        members=$(group_members "$GROUP") || die "✗ 找不到注册表——--group 依赖它确定组成员"
        [ -n "$members" ] || die "✗ 组 '$GROUP' 里没有成员"
        TGT_LIST=""
        for m in $members; do
          row=$(resolve_one "$m") || { echo "⚠️ 跳过 $m(离线或解析失败)" >&2; continue; }
          [ "${row%%|*}" = "$SELF_REF" ] && continue   # 等自己空闲没有意义
          TGT_LIST="$TGT_LIST$row
"
        done
        [ -n "$TGT_LIST" ] || die "✗ 组 '$GROUP' 没有可看护的在线成员"
        WATCH_DESC="组 $GROUP"
      else
        row=$(resolve_one "$TARGET") || die "✗ 目标解析失败: $TARGET"
        TGT_LIST="$row"
        WATCH_DESC="${row##*|}"
      fi
    fi
    if [ "$ONCE" = 1 ]; then
      printf '%s\n' "$TGT_LIST" | while IFS='|' read -r ref win title; do
        [ -n "$ref" ] || continue
        if busy_of "$ref" "$win"; then echo "判定: $title($ref)忙"; else echo "判定: $title($ref)闲"; fi
      done
      exit 0
    fi
    ;;
  overdue)
    AGE_MIN="${AGE_MIN:-30}"; TIMEOUT="${TIMEOUT:-14400}"; GROUP="${GROUP:-}"; ONCE=0
    while [ $# -gt 0 ]; do
      case "$1" in
        --age)     AGE_MIN="${2:-}"; shift 2 ;;
        --timeout) TIMEOUT="${2:-}"; shift 2 ;;
        --group)   GROUP="${2:-}"; [ -n "$GROUP" ] || die "--group 缺少组名"; shift 2 ;;
        --once)    ONCE=1; shift ;;
        *) die "未知选项: $1" ;;
      esac
    done
    # 组模式:盯的范围从"我发出的"扩成"组内成员发出的"(台账 from 记 tab 标题,别名一并算上)
    if [ -n "$GROUP" ] && [ -z "${OVERDUE_SCOPE:-}" ]; then
      reg="${CMUX_SESSIONS_CONF:-$HOME/.claude/cmux-sessions.conf}"
      [ -f "$reg" ] || die "✗ 找不到注册表 $reg——--group 依赖它确定组成员"
      OVERDUE_SCOPE=$(LC_ALL=C awk -F'|' -v g="$GROUP" '
        function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
        /^[ \t]*#/ || /^[ \t]*$/ { next }
        trim($4) == g { print trim($1); print trim($2) }' "$reg" | sort -u)
      [ -n "$OVERDUE_SCOPE" ] || die "✗ 组 '$GROUP' 里没有成员"
    fi
    if [ "$ONCE" = 1 ]; then
      ME=$(self_title); now=$(date +%s); found=0
      for f in "$MSGDIR"/pending/*; do
        [ -e "$f" ] || continue
        from=$(awk -F= '$1=="from"{print $2; exit}' "$f")
        ts=$(awk -F= '$1=="ts"{print $2; exit}' "$f"); ts=${ts:-$now}
        age=$(( (now - ts) / 60 ))
        printf '判定: %s from=%s 账龄=%smin %s\n' "$(basename "$f")" "$from" "$age" \
          "$(in_overdue_scope "$from" && [ "$age" -ge "$AGE_MIN" ] && echo '→ 会触发催办' || echo '→ 不触发')"
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
      # idle watcher 按(会话,目标)有多个,stop idle 按前缀停掉全部
      case "$what" in
        all) : ;;
        *) case "$(basename "$p")" in "${SELF_REF//:/_}-$what"*.pid) : ;; *) continue ;; esac ;;
      esac
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

# ── 锁:idle 按(会话,目标/组)一个,同时盯多个目标互不挤占;overdue 每会话一个 ──
case "$CMD" in
  idle)
    if [ -n "$GROUP" ]; then key="group-${GROUP//[^A-Za-z0-9_-]/_}"
    else first_ref="${TGT_LIST%%|*}"; key="${first_ref//:/_}"; fi
    LOCK="$WDIR/${SELF_REF//:/_}-idle-$key.pid"
    ;;
  overdue) LOCK="$WDIR/${SELF_REF//:/_}-overdue.pid" ;;
esac

# ── 后台化:WATCH_FG=1 是重入标志(后台副本直接跑主循环)────────────────────────
mkdir -p "$WDIR"
if [ "${WATCH_FG:-0}" = 1 ]; then
  trap 'rm -f "$LOCK"' EXIT     # 退出(含超时/触发后)自清 lock,不留"已死"残骸
  "run_$CMD"
  exit 0
fi
if [ -f "$LOCK" ] && kill -0 "$(cat "$LOCK")" 2>/dev/null; then
  die "✗ 已有同目标的 $CMD watcher(pid $(cat "$LOCK"))在跑,先 watch.sh stop $CMD"
fi
LOG="${LOCK%.pid}.log"
case "$CMD" in
  idle)    WATCH_FG=1 FLUSH_SIG="$FLUSH_SIG" TIMEOUT="$TIMEOUT" NOTE="$NOTE" \
           GROUP="$GROUP" TARGET="$TARGET" TGT_LIST="$TGT_LIST" WATCH_DESC="$WATCH_DESC" \
           nohup "$0" idle >>"$LOG" 2>&1 & ;;
  overdue) WATCH_FG=1 AGE_MIN="$AGE_MIN" TIMEOUT="$TIMEOUT" \
           GROUP="$GROUP" OVERDUE_SCOPE="${OVERDUE_SCOPE:-}" \
           nohup "$0" overdue >>"$LOG" 2>&1 & ;;
esac
echo $! > "$LOCK"
echo "✓ $CMD watcher 已启动(pid $!,盯 ${WATCH_DESC:-台账},日志 $LOG)"
