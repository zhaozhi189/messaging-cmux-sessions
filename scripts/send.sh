#!/usr/bin/env bash
# send.sh — 可靠地把一条消息投递到另一个 cmux 会话(Claude Code / Codex 等 TUI agent)。
#
# 它替你处理三个必踩的坑:
#   ① 提交必须用 CR(\r),LF(\n)只在输入框里插换行、不提交;
#   ② 目标忙时 Enter 常不生效,消息静默卡在它的输入框里;
#   ③ 光看屏幕无法判断投递成功(Claude Code 的灰色建议词 ghost text 与卡箱文本长得一样)。
#
# 做法:定位 surface(全量扫描,跨 workspace)→ 死壳防线(无 agent 输入框则拒发)
#      → 发送 + CR(斜杠命令先 settle 再回车,防补全 popup 吞 Enter)
#      → 用「消息签名是否已从 ❯/› 输入框消失」验证 → 未提交就自动补 CR(退避重试;
#        对方忙则停止补 CR——新版 TUI 会把 Enter 排队,继续怼只会雪上加霜)
#      → 确认提交后再等 1s(SEND_SETTLE)才返回,让调用方紧接着读屏能看到对方 turn 已启动。
#
# 用法:
#   send.sh <目标> <消息>                    投递一条消息(FYI,不期待回复)
#   send.sh <目标> --ask <消息>              需回复的请求:生成关联号,投递前落台账
#   send.sh <目标> --reply <corr> <消息>     回复某条请求:投递成功后自动消对方台账
#   send.sh <目标> --flush '<消息开头几个字>'  目标空闲后,补交卡在它输入框里的消息
#   send.sh <目标> --peek                    探测目标输入框:空 / 真卡箱 / ghost text
#   send.sh --list                          列出所有可寻址会话
#   (台账查询/手动消账见同目录 pending.sh)
#
# <目标> 可以是:注册表别名 | tab 标题(精确)| 标题子串(唯一时)| surface:N
#
# 选项:
#   --retry       已有持久编号的安全重试，通常由 delivery-wait.py 调用
#   --no-from     不自动添加 "(from <本会话>)" 来源前缀(以 / 开头的斜杠命令自动不加)
#   --dry-run     只解析定位与最终消息,不真的发送
#
# 环境变量:
#   SEND_SETTLE   提交确认后的等待秒数(默认 1,设 0 关闭)
#   CMUX_MSG_DIR  台账/忙闲状态目录(默认 ~/.claude/cmux-messaging)
#
# 退出码:
#   0  操作成功；发送确认不代表任务完成；--peek 时 = 箱空(含 ghost text)
#   1  没找到目标会话
#   2  目标忙,消息留在其输入框(可能已被 TUI 排队)—— 先重查再决定是否 --flush
#      --peek 时 = 箱里确有未提交文本
#   3  多次补 CR 仍残留,需人工查看;--peek 时 = 目标忙,无法探测
#   4  用法错误 / 目标不明确(标题重名)
#   5  目标没有 agent 输入框(死壳/普通 shell)—— 给 shell 执行命令请用裸 cmux send
#   6  会话发现或持久协议错误
#
# 依赖:cmux >= 0.64、bash 3.2+、Python 3.8+、awk/grep/sed。
set -euo pipefail

CMUX=${CMUX_BIN:-$(command -v cmux || echo /Applications/cmux.app/Contents/Resources/bin/cmux)}
SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REGISTRY="${CMUX_SESSIONS_CONF:-$HOME/.claude/cmux-sessions.conf}"
MSGDIR="${CMUX_MSG_DIR:-$HOME/.claude/cmux-messaging}"

die() { echo "$@" >&2; exit 4; }

# ── 会话清单:ref|window|workspace|title,一次拿全(含未选中的 workspace)────────
list_surfaces() {
  python3 "$SCRIPT_DIR/protocol.py" list || {
    echo "✗ 无法连接或发现 cmux 会话；连接或结构错误不等于没有目标。" >&2
    return 6
  }
}

# ── 注册表:把别名翻译成 tab 标题(可选,文件不存在就跳过)──────────────────────
# 格式(| 分隔,# 开头为注释):  别名 | tab标题 | 仓库路径 | 分组 | 说明
registry_lookup() {
  [ -f "$REGISTRY" ] || return 1
  LC_ALL=C awk -F'|' -v key="$1" '
    function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
    /^[ \t]*#/ || /^[ \t]*$/ { next }
    trim($1) == key { print trim($2); found = 1; exit }
    END { exit !found }
  ' "$REGISTRY"
}

# ── 定位目标:别名 → surface:N → 标题精确 → 标题子串(唯一才算)────────────────
resolve() {
  local want="$1" alias_tab rows
  alias_tab=$(registry_lookup "$want" 2>/dev/null) && [ -n "$alias_tab" ] && want="$alias_tab"

  rows=$(list_surfaces)
  [ -n "$rows" ] || die "✗ cmux 没有返回任何会话(cmux 在运行吗?)"

  local hit
  if printf '%s' "$want" | grep -qE '^surface:[0-9]+$'; then
    hit=$(printf '%s\n' "$rows" | LC_ALL=C awk -F'|' -v r="$want" '$1 == r')
  else
    hit=$(printf '%s\n' "$rows" | LC_ALL=C awk -F'|' -v t="$want" '$4 == t')
    [ -z "$hit" ] && hit=$(printf '%s\n' "$rows" | LC_ALL=C awk -F'|' -v t="$want" 'index($4, t) > 0')
  fi

  [ -z "$hit" ] && { echo "✗ 未找到会话: $want" >&2; echo "   可用会话:" >&2
                     printf '%s\n' "$rows" | awk -F'|' '{printf "     %-12s\t%s\t%s\n", $1, $3, $4}' >&2
                     exit 1; }

  # 重名/多命中绝不静默取第一个 —— 发错会话比发不出去更糟。
  if [ "$(printf '%s\n' "$hit" | wc -l | tr -d ' ')" -gt 1 ]; then
    echo "✗ 目标不唯一: $want —— 请用 surface:N 或更精确的标题" >&2
    printf '%s\n' "$hit" | awk -F'|' '{printf "     %-12s\t%s\t%s\n", $1, $3, $4}' >&2
    exit 4
  fi
  printf '%s' "$hit"
}

# 本会话身份(用于来源前缀 + 防自发自收)
self_ref() { python3 "$SCRIPT_DIR/protocol.py" self; }
self_title() {
  local me; me=$(self_ref) || return 1
  [ -n "$me" ] || return 1
  list_surfaces | awk -F'|' -v r="$me" '$1 == r { print ($4 != "" ? $4 : r); exit }'
}

# ── 参数解析 ──────────────────────────────────────────────────────────────────
[ $# -ge 1 ] || die "用法: send.sh <目标> <消息> | send.sh <目标> --flush '<签名>' | send.sh --list"

case "$1" in
  --inbox) shift; exec python3 "$SCRIPT_DIR/protocol.py" inbox "$@" ;;
  --ack) shift; exec python3 "$SCRIPT_DIR/protocol.py" ack "$@" ;;
  --complete) shift; exec python3 "$SCRIPT_DIR/protocol.py" complete "$@" ;;
  --status) shift; exec python3 "$SCRIPT_DIR/protocol.py" status "$@" ;;
esac

if [ "$1" = "--list" ]; then
  printf 'SURFACE\tWORKSPACE\tWINDOW\tTITLE\n'
  list_surfaces | awk -F'|' '{printf "%s\t%s\t%s\t%s\n", $1, $3, $2, $4}'
  exit 0
fi

TARGET="$1"; shift
[ $# -ge 1 ] || die "缺少消息文本"

ADD_FROM=1; DRY=0; MSG=""; FLUSH_SIG=""; ASK=0; REPLY_CORR=""; PEEK=0; DURABLE=0; MESSAGE_ID=""; RETRY=0
while [ $# -gt 0 ]; do
  case "$1" in
    --flush)   FLUSH_SIG="${2:-}"; [ -n "$FLUSH_SIG" ] || die "--flush 缺少签名参数"; shift 2 ;;
    --no-from) ADD_FROM=0; shift ;;
    --dry-run) DRY=1; shift ;;
    --ask)     ASK=1; shift ;;
    --durable) DURABLE=1; shift ;;
    --retry) RETRY=1; shift ;;
    --id) MESSAGE_ID="${2:-}"; [ -n "$MESSAGE_ID" ] || die "--id 缺少编号"; shift 2 ;;
    --peek)    PEEK=1; shift ;;
    --reply)   REPLY_CORR="${2:-}"; [ -n "$REPLY_CORR" ] || die "--reply 缺少关联号参数"; shift 2 ;;
    *)         MSG="$1"; shift ;;
  esac
done
[ "$DURABLE" = 1 ] && { [ "$ASK" = 0 ] && [ -z "$REPLY_CORR" ] && [ "$PEEK" = 0 ] && [ -z "$FLUSH_SIG" ]; } || { [ "$DURABLE" = 0 ] || die "--durable 不与 ask/reply/peek/flush 混用"; }
[ -z "$MESSAGE_ID" ] || [ "$DURABLE" = 1 ] || die "--id 只用于 --durable"
[ "$RETRY" = 0 ] || { [ "$DURABLE" = 1 ] && [ -n "$MESSAGE_ID" ]; } || die "--retry 仅用于已有持久消息编号"
[ "$ASK" = 1 ] && [ -n "$REPLY_CORR" ] && die "--ask 与 --reply 不能同时使用"

row=$(resolve "$TARGET")
ref=$(printf '%s' "$row" | cut -d'|' -f1)
win=$(printf '%s' "$row" | cut -d'|' -f2); win="${win:-window:1}"
tgt_title=$(printf '%s' "$row" | cut -d'|' -f4)

# 防自发自收(误把自己当目标)。SEND_SELF_OK=1 是 watch.sh 的专用旁路——
# watcher 进程继承了本会话身份,给"自己"注入唤醒消息是故意行为。
me=$(self_ref || true)
if [ -n "$me" ] && [ "$me" = "$ref" ] && [ "${SEND_SELF_OK:-0}" != 1 ]; then
  die "✗ 目标就是本会话($ref),拒绝自发自收"
fi

# --scrollback:read-screen 的返回受当前视口高度截断(小窗口下要 40 行只给十几行),
# 带上 scrollback 才能保证拿满请求的行数。
read_tail() { "$CMUX" read-screen --window "$win" --surface "$ref" --scrollback --lines "${1:-40}" 2>/dev/null; }

# 当前输入框 = 屏幕上最后一个 ❯(Claude)/ ›(Codex)行。
# ⚠️ 读 ≥40 行:长消息会 wrap,只读末尾几行会被状态栏挤掉 ❯ 行 → 误判"已提交"。
box_line() {
  local screen
  screen=$(read_tail 40) || return 1
  [ -n "$screen" ] || return 1
  printf '%s\n' "$screen" | grep -E '^[[:space:]]*[❯›][[:space:]]' | tail -1 || true
}
# 语义忙闲(busy-hook.sh 经 Claude Code hooks 写入):busy|idle|unknown。
# 文件缺失、身份不匹配或超过 CMUX_BUSY_TTL（默认 120 秒）时为 unknown。
sem_state() { python3 "$SCRIPT_DIR/protocol.py" busy "$ref" 2>/dev/null || printf unknown; }

# 判忙:语义状态优先(hooks 是权威),unknown 才用屏幕启发式。
# 屏幕启发式只看末尾 15 行——加了 --scrollback 后,更早的历史里会有过期的忙碌行。
is_busy() {  # <已抓取的屏幕内容>
  case "$(sem_state)" in
    busy) return 0 ;;
    idle) return 1 ;;
  esac
  printf '%s' "$1" | tail -15 | grep -qiE 'esc to interrupt|esc to cancel|… \('
}
# 提交确认后的 settle:确认只证明文本被接收,对方 harness 起 turn 还要约 1 秒,
# 立刻读屏会看到陈旧的空闲画面而误判。SEND_SETTLE=0 关闭。
# --reply 的消账也在这里做:只有投递确认成功才消(送达 ≠ 已回,但回复本身送达即闭环)。
ok() {
  if [ -n "${MESSAGE_ID:-}" ]; then
    python3 "$SCRIPT_DIR/protocol.py" notified "$MESSAGE_ID" || {
      echo "⚠️ 通知已提交但状态记录失败，请核查原编号，不要重发正文"; exit 3;
    }
  fi
  if [ -n "${REPLY_CORR:-}" ] && [ -f "$MSGDIR/pending/$REPLY_CORR" ]; then
    mkdir -p "$MSGDIR/done"
    printf 'replied_at=%s\nreplied_by=%s\n' "$(date +%s)" "${from:-?}" >> "$MSGDIR/pending/$REPLY_CORR"
    mv "$MSGDIR/pending/$REPLY_CORR" "$MSGDIR/done/$REPLY_CORR"
  fi
  [ "${SEND_SETTLE:-1}" = 0 ] || sleep "${SEND_SETTLE:-1}"
  echo "$1"; exit 0
}
# ⚠️ 判据只能是「箱里有没有本条消息的签名」,不能是「箱里有没有字」——
#    Claude Code 会在输入框显示灰色建议词(ghost text),读屏时与卡箱文本无法区分。
msg_in_box() {
  local line
  line=$(box_line) || return 0
  [ -n "$line" ] || return 0
  [ -n "$1" ] && printf '%s\n' "$line" | grep -qF "$1"
}

# mkdir 锁在同机不同进程间生效；残留锁需要人工核查，不自动抢占。
if [ "$DRY" != 1 ]; then
  LOCK_ROOT="${CMUX_SEND_LOCK_DIR:-$MSGDIR/send-locks}"
  mkdir -p "$LOCK_ROOT"
  SEND_LOCK="$LOCK_ROOT/${ref//:/_}"
  mkdir "$SEND_LOCK" 2>/dev/null || die "✗ 目标正在被另一发送进程操作，或存在残留锁: $SEND_LOCK"
  printf '%s\n' "$$" > "$SEND_LOCK/pid"
  trap 'rm -f "$SEND_LOCK/pid"; rmdir "$SEND_LOCK" 2>/dev/null || true' EXIT
fi

# 投递监听仅在目标锁内复核原编号、endpoint、权限与完整输入，不清空用户草稿。
if [ "$RETRY" = 1 ]; then
  [ "$DRY" = 0 ] || die "--retry 不与 --dry-run 混用"
  action=$(python3 "$SCRIPT_DIR/delivery-wait.py" --probe --id "$MESSAGE_ID" --target "$ref") || exit 6
  case "$action" in
    notified|received|completed) echo "已有通知或接收记录，停止重试"; exit 0 ;;
    empty|owned)
      python3 "$SCRIPT_DIR/protocol.py" enqueue "$ref" "${CMUX_FROM:-codex-external}" "$MSG" "$MESSAGE_ID" >/dev/null || exit 6
      ;;
    session_changed) echo "目标身份已改变，停止旧路由" >&2; exit 6 ;;
    *) echo "投递继续等待：$action"; exit 2 ;;
  esac
  if [ "$action" = owned ]; then
    FLUSH_SIG="请查 cmux 收件箱：$MESSAGE_ID"
  fi
fi

# ── --flush:补交卡在目标输入框里的消息 ────────────────────────────────────────
if [ -n "$FLUSH_SIG" ]; then
  if msg_in_box "$FLUSH_SIG"; then
    is_busy "$(read_tail 40)" && { echo "⚠️ 目标忙，暂不补交"; exit 2; }
    if [ "$RETRY" = 1 ]; then
      action=$(python3 "$SCRIPT_DIR/delivery-wait.py" --probe --id "$MESSAGE_ID" --target "$ref") || exit 6
      case "$action" in
        notified|received|completed) exit 0 ;;
        owned) : ;;
        session_changed) exit 6 ;;
        *) echo "补交前状态变化，继续等待：$action"; exit 2 ;;
      esac
    fi
    "$CMUX" send --window "$win" --surface "$ref" '\r' >/dev/null; sleep 2
    msg_in_box "$FLUSH_SIG" && { echo "⚠️ flush 后仍残留 $ref"; exit 3; }
    ok "✓ 已补交 → $ref ($tgt_title)"
  fi
  # 空箱补 \r 会让 TUI 重放上一条输入(实测重跑过整条 brief),所以签名不匹配就什么都不做。
  echo "✓ 箱内无该消息(已提交或从未卡箱),无需 flush。当前输入框: $(box_line)"; exit 0
fi

# ── --peek:目标输入框里到底有没有「真的」未提交文本?────────────────────────
#    Claude Code 会在空输入框显示灰色历史建议词(ghost text),read-screen 拿到的
#    纯文本与卡箱文本无法直接区分——曾诱导对空箱反复 --flush 而不自知(2026-08-29 踩)。
#    判据:注入一个空格再退格,净效应为零。真文本注入后仍在(只是多个尾空格);
#    ghost 是"影子",一有真实输入就消失/改变。仅在目标空闲时探测——忙时注入会打扰,
#    且屏幕在滚动,读数不稳。
if [ "$PEEK" = 1 ]; then
  if is_busy "$(read_tail 40)"; then
    echo "? 目标忙,不探测(忙时注入会打扰,读数也不稳)。等空闲再试: watch.sh idle $TARGET"
    exit 3
  fi
  line=$(box_line)
  [ -n "$line" ] || { echo "✗ 目标 $ref($tgt_title)没有 agent 输入框(❯/›)。" >&2; exit 5; }
  txt=$(printf '%s' "$line" | sed -E 's/.*[❯›][[:space:]]*//; s/[[:space:]]+$//')
  if [ -z "$txt" ]; then
    echo "✓ 箱空:$ref($tgt_title)输入框没有未提交文本。"
    exit 0
  fi
  "$CMUX" send --window "$win" --surface "$ref" ' ' >/dev/null
  sleep 1
  after=$(box_line)
  "$CMUX" send --window "$win" --surface "$ref" "$(printf '\177')" >/dev/null
  if printf '%s' "$after" | grep -qF "$txt"; then
    echo "⚠ 真卡箱:$ref($tgt_title)输入框有未提交文本(注入探针后仍在):"
    echo "   $txt"
    echo "   确认是你发的消息后补交: $0 $TARGET --flush '<该文本开头几个字>'"
    exit 2
  fi
  echo "✓ ghost text:注入探针后文本消失——箱里其实是空的,不要 flush。"
  echo "   (刚才看到的是 Claude Code 的灰色历史建议词: $txt)"
  exit 0
fi

[ -n "$MSG" ] || die "缺少消息文本"
[ "$DURABLE" = 1 ] || case "$MSG" in
  *$'\n'*|*$'\r'*|*$'\033'*) die "消息不能含换行、回车或 ESC，请写文件后发送指针" ;;
esac
[ -z "$REPLY_CORR" ] || [[ "$REPLY_CORR" =~ ^a[0-9]+-[0-9]+$ ]] || die "关联号格式不正确"

# ── 来源前缀:不带来源的注入消息,对方无法与真人指令区分 ──────────────────────
# 斜杠命令是指令不是聊天,加了前缀就不再是斜杠命令(变成 "(from x) /cmd"),必须跳过。
case "$MSG" in /*) ADD_FROM=0 ;; esac
from=${CMUX_FROM:-$(self_title || true)}

if [ "$DURABLE" = 1 ]; then
  if [ "$DRY" = 1 ]; then
    echo "目标: $ref ($win, $tgt_title)"; echo "持久消息正文: $MSG"; exit 0
  fi
  from=${from:-codex-external}
  if [ -n "$MESSAGE_ID" ]; then
    MESSAGE_ID=$(python3 "$SCRIPT_DIR/protocol.py" enqueue "$ref" "$from" "$MSG" "$MESSAGE_ID")
  else
    MESSAGE_ID=$(python3 "$SCRIPT_DIR/protocol.py" enqueue "$ref" "$from" "$MSG")
  fi
  echo "消息已落盘: ${MESSAGE_ID}；重试必须沿用 --id 和原正文，不能重复派活。"
  status=$(python3 "$SCRIPT_DIR/protocol.py" status "$MESSAGE_ID")
  if printf '%s' "$status" | grep -qE '"state": "(notified|received|completed)"'; then
    echo "已有通知或接收记录，不再发送通知；可用 --status 查询"; exit 0
  fi
  MSG="请查 cmux 收件箱：$MESSAGE_ID"
fi

# --ask/--reply 的结构化标记依赖来源身份(对方要知道回给谁/消谁的账)。
CORR=""
if [ "$ASK" = 1 ]; then
  [ "$ADD_FROM" = 1 ] && [ -n "$from" ] || die "--ask 需要来源身份(须在 cmux 内运行,且不能与 --no-from 同用)"
  CORR="a$(date +%s)-$RANDOM"
fi
if [ -n "$REPLY_CORR" ]; then
  [ -n "$from" ] || die "--reply 需要来源身份(须在 cmux 内运行)"
fi

PRELEN=0
if [ "$ADD_FROM" = 1 ] && [ -n "$from" ] && ! printf '%s' "$MSG" | grep -q '^(from '; then
  if [ -n "$CORR" ]; then
    prefix="(from $from)[需回复 $CORR] "
  elif [ -n "$REPLY_CORR" ]; then
    prefix="(from $from)[回复 $REPLY_CORR] "
  else
    prefix="(from ${CMUX_FROM_LABEL:-$from}) "
  fi
  MSG="$prefix$MSG"
  PRELEN=${#prefix}
fi
# 需回复的请求在尾部附上回复方式——对方(可能是没装本 skill 的 Codex)才知道怎么消账。
if [ -n "$CORR" ]; then
  printf -v reply_script '%q' "$SCRIPT_DIR/send.sh"
  printf -v reply_target '%q' "${CMUX_REPLY_TO:-$me}"
  MSG="$MSG [回复: $CORR]"
fi

# 签名 = 消息开头若干字符,必须落在 ❯ 那一行内(wrap 只影响后续行)。
# 前缀本身不能当签名——同一发送方的每条消息前缀都一样,会互相误判;
# 所以签名长度 = 前缀长度 + 正文前 12 个字符，按 Unicode 字符截取。
SIG=$(python3 -c 'import sys; print(sys.argv[1][:int(sys.argv[2])])' "$MSG" "$((PRELEN + 12))")

# ── ⭐ 长度闸门:超长消息必然 wrap,而 wrap 会同时打破两条验证前提 ────────────────
#    ① 签名假设「必落在 ❯ 那一行内」——箱里已堆着上一条时,本条签名落在中间行;
#    ② box_line 只取最后一个 ❯ 行、read_tail 只读 40 行——wrap 后签名滚出读取窗口。
#    两者叠加 ⇒ 签名"查不到"被当成"已提交",退出码 0 却实际卡箱(2026-08-08 连翻三次)。
#    根治办法不是把窗口调大(治标),而是不让消息 wrap:写文件 + 发一行指针。
# 单位必须是「显示列宽」而不是字符数:终端按列换行,而中文/全角字符占 2 列。
# 按字符数判会系统性低估——实测一条 153 字符的消息实际占 159 列,在 149 列宽的终端
# 里必然 wrap,却轻松通过了 260 的字符数闸门,于是退出码 0 而消息卡箱(2026-08-29 踩)。
disp_width() { LC_ALL=en_US.UTF-8 python3 -c '
import sys, unicodedata
m = sys.argv[1]
print(sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in m))
' "$1" 2>/dev/null || printf '%s' "$1" | LC_ALL=en_US.UTF-8 wc -m | tr -d " "; }

# 阈值取目标终端的真实宽度,不用固定值:固定 260 差不多是常见终端宽度的 1.7 倍,
# 等于把「保证换行」的区间(宽度~260)整段放行了。
#
# 宽度取屏幕末尾若干行的最大显示宽度:底部的分隔线与状态栏总是铺满整宽。
# 不要用「第一条 ──── 的长度」——输出里嵌套的表格/框也画分隔线,长度五花八门
# (实测同屏出现过 84 / 132 / 444),取到哪条全看运气,会把阈值压到 40 几列而误拒。
term_cols=$(read_tail 15 | LC_ALL=en_US.UTF-8 python3 -c '
import sys, unicodedata
w = 0
for line in sys.stdin.buffer.read().decode("utf-8", "replace").splitlines():
    n = sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in line.rstrip())
    w = max(w, n)
print(w)
' 2>/dev/null) || term_cols=100
[ "${term_cols:-0}" -lt 60 ] && term_cols=100      # 读不到/异常窄就取保守值,宁可误拒不可漏放
SAFE_COLS=$((term_cols - 8))                        # 留出提示符与光标的余量
MSGCHARS=$(disp_width "$MSG")
if [ "${SEND_MAXCHARS:-$SAFE_COLS}" -gt 0 ] && [ "$MSGCHARS" -gt "${SEND_MAXCHARS:-$SAFE_COLS}" ]; then
  cat >&2 <<EOF
✗ 消息过宽($MSGCHARS 显示列 > ${SEND_MAXCHARS:-$SAFE_COLS} 可用列,目标终端宽 ${term_cols}),拒绝发送——
  换行后"已提交"的验证不可靠,
  会出现退出码 0 但消息其实卡在对方输入框里(本 skill 已多次踩此坑)。

  改用「写文件 + 发一行指针」:
    1) 把完整内容写到文件(scratchpad 或项目内均可,同机跨会话可读)
    2) $0 $TARGET '请读并执行:/abs/path/to/brief.md'

  这样对方拿到的是完整内容,而指针短到不会 wrap,投递验证才可信。
  确需强发(自负卡箱风险):SEND_MAXCHARS=0 $0 ...
EOF
  exit 4
fi

if [ "$DRY" = 1 ]; then
  echo "目标: $ref ($win, $tgt_title)"; echo "消息: $MSG"; echo "签名: $SIG"
  [ -n "$CORR" ] && echo "台账: 将创建 $MSGDIR/pending/$CORR"
  [ -n "$REPLY_CORR" ] && echo "台账: 投递成功后将消 $MSGDIR/pending/$REPLY_CORR"
  exit 0
fi

# ── 死壳防线:屏幕上没有 agent 输入框(❯/›)= agent 已退出只剩 shell,或全新 surface。
#    往死壳注入,消息会被 shell 当命令执行——绝不能发。
#    (全新 surface 在首次写入前 read-screen 必报 internal_error,也会走到这里,同样该拒。)
if [ -z "$(box_line)" ]; then
  echo "✗ 目标 $ref($tgt_title)没有 agent 输入框(❯/›)——可能是普通 shell、agent 已退出或全新 surface。" >&2
  echo "   给 shell 执行命令请用裸 cmux send(那是执行,不是发消息)。" >&2
  exit 5
fi

# 不覆盖已有输入；Codex 占位提示文本只在明确匹配默认提示时放行。
current=$(box_line) || { echo "✗ 读屏失败，拒绝发送"; exit 5; }
input=$(printf '%s' "$current" | sed -E 's/^[[:space:]]*[❯›][[:space:]]*//; s/[[:space:]]+$//')
case "$input" in
  ''|'Ask Codex to do anything'|'Implement {feature}'|'Find and fix a bug in @filename'|'Write tests for @filename'|'Summarize recent commits'|'Improve documentation in @filename'|'Explain this codebase') ;;
  *) echo "✗ 目标输入框已有文本或未识别的建议词，拒绝追加；请先确认输入状态"; exit 3 ;;
esac

# ── 需回复的请求:投递前先落台账(先记账再送达,进程死了账也不丢)──────────────
if [ -n "$CORR" ]; then
  mkdir -p "$MSGDIR/pending"
  printf 'from=%s\nto=%s\ntarget=%s\nts=%s\nmsg=%s\n' \
    "$from" "$tgt_title" "$ref" "$(date +%s)" \
    "$(printf '%s' "$MSG" | LC_ALL=en_US.UTF-8 cut -c1-120)" > "$MSGDIR/pending/$CORR"
  printf 'reply_script=%s\nreply_target=%s\n' "$SCRIPT_DIR/send.sh" "${CMUX_REPLY_TO:-$me}" >> "$MSGDIR/pending/$CORR"
  if [ -n "${CMUX_REPLY_TO:-$me}" ]; then
    echo "回复命令: bash $reply_script $reply_target --reply $CORR '<答复>'"
  else
    echo "外部调用方无 cmux surface：请在 brief 中约定回复文件，不向空目标发消息。"
  fi
fi

if [ "$RETRY" = 1 ]; then
  action=$(python3 "$SCRIPT_DIR/delivery-wait.py" --probe --id "$MESSAGE_ID" --target "$ref") || exit 6
  case "$action" in
    notified|received|completed) exit 0 ;;
    empty) : ;;
    session_changed) exit 6 ;;
    *) echo "发送前状态变化，继续等待：$action"; exit 2 ;;
  esac
fi

# ── 发送:一条消息 + 一个 CR。中间绝不能有 \n / \r,否则变成换行而非提交。────
# 斜杠命令例外:输入 "/xxx" 会弹出补全 popup,紧跟的 Enter 会被 popup 吞掉
# (选中补全项而不是提交),所以先发文本、等 popup 稳定、再单发 CR。
case "$MSG" in
  /*)
    "$CMUX" send --window "$win" --surface "$ref" "$MSG" >/dev/null
    sleep 1.2
    "$CMUX" send --window "$win" --surface "$ref" '\r' >/dev/null
    ;;
  *)
    "$CMUX" send --window "$win" --surface "$ref" "$MSG"'\r' >/dev/null
    ;;
esac

# ── 验证 + 按需补 CR ──────────────────────────────────────────────────────────
# 成功判据:a) 明确的队列提示;b) 签名已从输入框消失。
# ⚠️ 目标"忙"不等于"已提交"—— 忙时 CR 常不生效、文本留箱。
# ⚠️ 但一旦确认目标在忙,就停止补 CR:新版 TUI 会把 mid-turn 的 Enter 排队,
#    turn 结束后逐个生效,继续怼 CR 可能触发重复提交/重放。发一次,等空闲。
BUSY=0
for wait in 1 1 2 3 5 8; do
  sleep "$wait"
  scr=$(read_tail 40) || { echo "⚠️ 发送后读屏失败，投递状态未知，请勿直接重发"; exit 3; }
  if ! msg_in_box "$SIG"; then
    ok "✓ 已提交 → $ref ($tgt_title)"
  fi
  if is_busy "$scr"; then
    BUSY=1; break
  fi
  "$CMUX" send --window "$win" --surface "$ref" '\r' >/dev/null
done

# 目标忙 / ~20 秒仍卡箱:不无限等,交回调用方稍后处理。
if [ "$BUSY" = 1 ] || is_busy "$(read_tail 40)"; then
  echo "⚠️ 目标忙,消息暂留其输入框(已发过 CR,TUI 可能已将其排队,turn 结束会自动提交)。"
  echo "   空闲后先重查再决定是否补交:"
  echo "   $0 $ref --flush '$SIG'"
  exit 2
fi
echo "⚠️ 多次补 CR 后输入框仍残留,请人工查看 $ref($tgt_title)。末尾:"; read_tail 6
exit 3
