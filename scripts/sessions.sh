#!/usr/bin/env bash
# sessions.sh — 会话发现与项目关联:把 cmux 里活着的 tab 和「哪个会话负责哪个仓库」对上号。
#
# 没有它,跨会话协作只能靠人肉记「哪个 tab 负责哪个仓库」;有了它,一个 agent 能自己
# 回答:我的兄弟会话是谁、它在不在、它的仓库在哪(审计循环要 git -C <repo> log)。
#
# 用法:
#   sessions.sh                  列出注册表里的会话(带在线状态);无注册表则列全部 cmux 会话
#   sessions.sh --all            列出全部 cmux 会话(含未登记的)
#   sessions.sh --group <名字>    只列某个协作组
#   sessions.sh --status         额外探测忙/闲(逐个读屏,慢一些)
#   sessions.sh --repo <别名>     只打印该会话的仓库路径(给 git -C 用)
#   sessions.sh --ref  <别名>     只打印该会话的 surface ref(给 cmux send 用)
#
# 注册表:${CMUX_SESSIONS_CONF:-~/.claude/cmux-sessions.conf}
#   格式(| 分隔,# 注释):  别名 | cmux tab 标题 | 仓库路径 | 分组 | 说明
#
# 依赖:cmux >= 0.64、bash 3.2+、Python 3.8+、awk/grep/sed。
set -euo pipefail

CMUX=$(command -v cmux || echo /Applications/cmux.app/Contents/Resources/bin/cmux)
REGISTRY="${CMUX_SESSIONS_CONF:-$HOME/.claude/cmux-sessions.conf}"
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

MODE=registry; GROUP=""; WANT_STATUS=0; QUERY=""; QUERY_FIELD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --all)    MODE=all; shift ;;
    --group)  GROUP="${2:?--group 需要组名}"; shift 2 ;;
    --status) WANT_STATUS=1; shift ;;
    --repo)   QUERY="${2:?--repo 需要别名}"; QUERY_FIELD=repo; shift 2 ;;
    --ref)    QUERY="${2:?--ref 需要别名}";  QUERY_FIELD=ref;  shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 4 ;;
  esac
done

# 复用 send.sh 的清单能力,避免两份解析逻辑(DRY)
surfaces() { "$HERE/send.sh" --list | tail -n +2 | awk -F'\t' '{print $1 "|" $3 "|" $2 "|" $4}'; }
#                                                              ref | window | workspace | title

# 忙/闲:末尾出现 "esc to interrupt" 或进行时 spinner(`…（Ns）`)= 忙。
# 注意 "Brewed for"/"Cogitated for" 这类**过去式**是已完成,不是忙。
probe() {
  local ref="$1" win="$2" scr semantic
  semantic=$(python3 "$HERE/protocol.py" busy "$ref" 2>/dev/null) || semantic=unknown
  case "$semantic" in busy|idle) echo "$semantic"; return ;; esac
  scr=$("$CMUX" read-screen --window "$win" --surface "$ref" --lines 12 2>/dev/null) || { echo "?"; return; }
  printf '%s' "$scr" | grep -qiE 'esc to interrupt|esc to cancel|… \(' && { echo busy; return; }
  printf '%s\n' "$scr" | grep -qE '^[[:space:]]*[❯›][[:space:]]' || { echo unknown; return; }
  echo idle
}

rows=$(surfaces)

if [ -n "$QUERY" ]; then
  # 单值查询:给脚本/审计循环用,输出干净的一行
  tab=$(LC_ALL=C awk -F'|' -v k="$QUERY" '
    function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
    /^[ \t]*#/ || /^[ \t]*$/ { next }
    trim($1) == k { print trim($2) "\t" trim($3); exit }' "$REGISTRY" 2>/dev/null || true)
  [ -z "$tab" ] && { echo "✗ 注册表里没有别名: $QUERY ($REGISTRY)" >&2; exit 1; }
  title=$(printf '%s' "$tab" | cut -f1); repo=$(printf '%s' "$tab" | cut -f2)
  if [ "$QUERY_FIELD" = repo ]; then
    printf '%s\n' "${repo/#\~/$HOME}"
  else
    printf '%s\n' "$rows" | LC_ALL=C awk -F'|' -v t="$title" '$4 == t { print $1; exit }'
  fi
  exit 0
fi

if [ "$MODE" = registry ] && [ ! -f "$REGISTRY" ]; then
  echo "ℹ️  未找到注册表 $REGISTRY —— 列出全部 cmux 会话。" >&2
  echo "   建好注册表后,别名/仓库/分组才能用(样例见 skill 里的 cmux-sessions.example.conf)。" >&2
  MODE=all
fi

if [ "$MODE" = all ]; then
  printf 'SURFACE\tWORKSPACE\tTITLE%s\n' "$([ "$WANT_STATUS" = 1 ] && printf '\tSTATUS')"
  printf '%s\n' "$rows" | while IFS='|' read -r ref win ws title; do
    st=""; [ "$WANT_STATUS" = 1 ] && st=$'\t'$(probe "$ref" "$win")
    printf '%s\t%s\t%s%s\n' "$ref" "$ws" "$title" "$st"
  done
  exit 0
fi

# 注册表视图:别名 → 在线状态 → 仓库,按注册表顺序输出
printf 'ALIAS\tSURFACE\tTITLE\tGROUP\tREPO%s\n' "$([ "$WANT_STATUS" = 1 ] && printf '\tSTATUS')"
printf '%s\n' "$rows" | LC_ALL=C awk -F'|' -v reg="$REGISTRY" -v group="$GROUP" '
  function trim(s) { gsub(/^[ \t]+|[ \t]+$/, "", s); return s }
  { ref[$4] = $1; win[$4] = $2 }          # 以 tab 标题为连接键
  END {
    while ((getline line < reg) > 0) {
      if (line ~ /^[ \t]*#/ || line ~ /^[ \t]*$/) continue
      n = split(line, f, "|")
      a = trim(f[1]); t = trim(f[2]); r = (n >= 3 ? trim(f[3]) : ""); g = (n >= 4 ? trim(f[4]) : "")
      if (group != "" && g != group) continue
      # 用 | 而非 tab 分隔:tab 属于 IFS 白字符,shell 的 read 会把连续 tab 折叠成一个,
      # 导致「仓库路径为空」的行整体错位(实测踩过)。
      print a "|" (t in ref ? ref[t] : "(离线)") "|" t "|" g "|" r "|" (t in ref ? win[t] : "")
    }
  }
' | while IFS='|' read -r a ref t g r w; do
  st=""
  if [ "$WANT_STATUS" = 1 ]; then
    if [ "$ref" = "(离线)" ]; then st=$'\t'"offline"; else st=$'\t'$(probe "$ref" "$w"); fi
  fi
  printf '%s\t%s\t%s\t%s\t%s%s\n' "$a" "$ref" "$t" "$g" "$r" "$st"
done
