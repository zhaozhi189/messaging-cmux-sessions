#!/usr/bin/env bash
# board.sh — 组共享看板:谁在做什么、哪些事待决、哪些已定。
#
# 为什么需要它:三个以上会话协作时,两两消息是 O(N²),没有任何一方有全局视图,
# 协调者被迫当「人肉总线 + 记忆」;更伤的是对话里达成的决策只活在 cmux 消息和
# 提交信息里,不落盘就丢(实测一次协作后要手工补 10 条实现期决策进设计文档)。
# 看板的关键价值不是省消息,是「已定」一栏——它保存带版本与适用范围的协作决策。
#
# 用法:
#   board.sh --list                                    列出已有看板
#   board.sh <组>                                      读看板
#   board.sh <组> --init                               创建看板骨架
#   board.sh <组> --set-status <会话> <任务> <状态> [<阻塞于>]
#                                                      更新"在做什么"里自己那一行
#   board.sh <组> --ask '<问题>' [--owner '<谁来定>']    登记一条待决(自动编号 Dn)
#   board.sh <组> --decide <编号> '<决策>' '<理由>'      待决 → 已定(编号不存在则新增)
#
# 配合:写完看板后用 group.sh <组> '看板已更新:<一句话>' 提示全组——
# 消息只当门铃,内容以看板为准。各会话读写同一份文件(同机共享文件系统)。
#
# 依赖:bash 3.2+,awk。并发写用 mkdir 自旋锁保护。
set -euo pipefail

MSGDIR="${CMUX_MSG_DIR:-$HOME/.claude/cmux-messaging}"
BOARDS="$MSGDIR/boards"

die() { echo "$*" >&2; exit 4; }

if [ "${1:-}" = "--list" ]; then
  found=0
  for f in "$BOARDS"/*.md; do
    [ -e "$f" ] || continue
    printf '%s\t%s\n' "$(basename "$f" .md)" "$f"; found=1
  done
  [ "$found" = 0 ] && echo "没有看板。建一个: board.sh <组> --init"
  exit 0
fi

GROUP="${1:-}"; [ -n "$GROUP" ] && [ "${GROUP#--}" = "$GROUP" ] || die "用法见文件头: board.sh <组> [--init|--set-status|--ask|--decide]"
shift
FILE="$BOARDS/${GROUP//[^A-Za-z0-9_.-]/_}.md"
LOCKD="$FILE.lock"

lock() {
  local n=0
  until mkdir "$LOCKD" 2>/dev/null; do
    n=$((n + 1)); [ "$n" -ge 50 ] && die "✗ 看板被锁超过 5s($LOCKD)——上一个写入者可能异常退出,确认后 rmdir 它"
    sleep 0.1
  done
  trap 'rmdir "$LOCKD" 2>/dev/null || true' EXIT
}

# 单元格里的 | 会破坏 markdown 表格,替换成全角
cell() { printf '%s' "$1" | tr '|' '¦'; }

# 在 <section> 的表末尾插入 <newrow>;<delkey> 非空时先删除该 section 内首列为它的行
# ⚠️ LC_ALL=C 必须加:macOS awk 的字符串比较走 locale collation,UTF-8 locale 下
#    「已定」==「在做什么」会被判为相等(实测),字节级比较才正确。
table_edit() {  # <section> <delkey> <newrow>
  local tmp="$FILE.tmp.$$"
  LC_ALL=C awk -v section="$1" -v delkey="$2" -v newrow="$3" '
    { lines[NR] = $0
      if ($0 ~ /^## /) cur = substr($0, 4)
      sec[NR] = cur }
    END {
      # 插入锚点 = section 内最后一个「不会被删除」的表行(表头/分隔行也算,空表插表首)
      anchor = 0
      for (i = 1; i <= NR; i++)
        if (sec[i] == section && lines[i] ~ /^\|/ \
            && (delkey == "" || index(lines[i], "| " delkey " |") != 1))
          anchor = i
      for (i = 1; i <= NR; i++) {
        if (delkey != "" && sec[i] == section && index(lines[i], "| " delkey " |") == 1) continue
        print lines[i]
        if (newrow != "" && i == anchor) print newrow
      }
    }' "$FILE" > "$tmp" && mv "$tmp" "$FILE"
}

next_id() { awk 'match($0, /^\| D[0-9]+ /) { n = substr($0, 4, RLENGTH - 4) + 0; if (n > max) max = n } END { print max + 1 }' "$FILE"; }

ACTION="${1:---read}"; [ $# -gt 0 ] && shift

case "$ACTION" in
  --read)
    [ -f "$FILE" ] || die "✗ 组 '$GROUP' 还没有看板。建一个: board.sh $GROUP --init"
    cat "$FILE"
    ;;
  --init)
    [ -f "$FILE" ] && die "✗ 看板已存在: $FILE"
    mkdir -p "$BOARDS"
    cat > "$FILE" <<EOF
# $GROUP 协作看板

> 各会话读写同一份;消息只用来提示「看板更新了」。**「已定」记录协作决策，不覆盖用户最新指令或实际产物**,
> 实现期在对话里定下的口径必须进这里,否则协作一结束就丢。

## 在做什么

| 会话 | 当前任务 | 状态 | 阻塞于 |
| --- | --- | --- | --- |

## 待决

| 编号 | 问题 | 谁来定 | 提出时间 |
| --- | --- | --- | --- |

## 已定

| 编号 | 决策 | 理由 | 时间 |
| --- | --- | --- | --- |
EOF
    echo "✓ 已创建 $FILE"
    echo "  提示全组: group.sh $GROUP '组看板已建,读写方式见 board.sh --help;文件 $FILE'"
    ;;
  --set-status)
    who="${1:-}"; task="${2:-}"; st="${3:-}"; blk="${4:-—}"
    [ -n "$who" ] && [ -n "$task" ] && [ -n "$st" ] || die "用法: board.sh <组> --set-status <会话> <任务> <状态> [<阻塞于>]"
    [ -f "$FILE" ] || die "✗ 组 '$GROUP' 还没有看板。先 board.sh $GROUP --init"
    lock
    table_edit "在做什么" "$(cell "$who")" "| $(cell "$who") | $(cell "$task") | $(cell "$st") | $(cell "$blk") |"
    echo "✓ 已更新 $who 的状态。别忘了按门铃: group.sh $GROUP '看板已更新: $who $st'"
    ;;
  --ask)
    q="${1:-}"; owner="—"
    [ -n "$q" ] || die "用法: board.sh <组> --ask '<问题>' [--owner '<谁来定>']"
    shift || true
    while [ $# -gt 0 ]; do
      case "$1" in
        --owner) owner="${2:-—}"; shift 2 ;;
        *) die "未知选项: $1" ;;
      esac
    done
    [ -f "$FILE" ] || die "✗ 组 '$GROUP' 还没有看板。先 board.sh $GROUP --init"
    lock
    id="D$(next_id)"
    table_edit "待决" "" "| $id | $(cell "$q") | $(cell "$owner") | $(date '+%m-%d %H:%M') |"
    echo "✓ 已登记待决 ${id}。按门铃让 $owner 来定: group.sh $GROUP '看板新增待决 ${id},请 ${owner} 拍板'"
    ;;
  --decide)
    id="${1:-}"; decision="${2:-}"; why="${3:-—}"
    [ -n "$id" ] && [ -n "$decision" ] || die "用法: board.sh <组> --decide <编号|new> '<决策>' '<理由>'"
    [ -f "$FILE" ] || die "✗ 组 '$GROUP' 还没有看板。先 board.sh $GROUP --init"
    lock
    if [ "$id" = new ]; then id="D$(next_id)"; else
      # 从待决区摘掉(不在也没关系:允许直接记一条已定)
      table_edit "待决" "$id" ""
    fi
    table_edit "已定" "$id" "| $id | $(cell "$decision") | $(cell "$why") | $(date '+%m-%d %H:%M') |"
    echo "✓ 已定 ${id}。按门铃: group.sh $GROUP '看板已定 ${id}: $decision'"
    ;;
  *)
    die "未知操作: $ACTION(可用 --read/--init/--set-status/--ask/--decide;详见文件头)"
    ;;
esac
