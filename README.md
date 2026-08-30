# messaging-cmux-sessions

> 让 cmux 里的多个 AI 编码会话**互相发消息**——可靠地。

一个 Claude Code / Codex 会话可以把接口定稿转达给前端会话、给并行 agent 派活、或者持续旁路审计另一个会话的仓库。cmux 提供了底层能力(`cmux send` 能往任意 tab 的终端打字 + 回车),但**直接用它发消息会静默丢消息**——本 skill 就是把这条路上的坑全部填平,变成一条可靠、有退出码、可编排的通道。

```bash
~/.claude/skills/messaging-cmux-sessions/scripts/send.sh demo-frontend '接口已定稿,详见 /tmp/api-v2.md'
# ✓ 已提交 → surface:7 (demo-frontend)
```

---

## 为什么需要它

`cmux send` 本质是**往一个脆弱的 TUI 里注入键盘事件**,不是消息队列。裸用会遇到:

| 你以为 | 实际发生 |
|---|---|
| 命令打印了 `OK`,消息发出去了 | 只是按键投递了,TUI 没提交,文本卡在对方输入框 |
| 用 `\n` 结尾提交 | LF 在 Claude Code 里只是**输入框内换行**,必须用 `\r` |
| 对方屏幕在转圈 = 收到了 | "忙"和"已提交"是两回事,消息可能还在箱里 |
| 屏幕上有字 = 消息卡住了 | 那可能是 Claude Code 的灰色**建议词**(ghost text) |
| 对方没回复 = 它不理我 | 它的回复大概率**卡在你自己的输入框**里没提交 |
| 按标题找到了 tab | 两个 tab 同名,消息发给了错误的会话 |

这些坑单独看都很小,叠加起来的结果是:**你以为在协作,其实一半消息从未送达,而且没有任何报错。**

## 应用场景

- **前后端并行开发** —— 后端会话定稿 API 后,把签名转达给前端会话,不必等人转述。
- **旁路代码审计** —— 一个会话每 5 分钟看一次另一个会话的新提交,挑出落空需求和未验证假设,确认后推给用户。
- **并行 agent 编排** —— 一个"主控"会话把 brief 写成文件、派给 N 个执行会话,用 `git` HEAD 变化判断完成。
- **跨仓库改动同步** —— 同一产品的多个仓库各占一个会话,改了协议定义的一方通知其余各方。
- **让卡住的会话动起来** —— 兄弟会话在等一个你已经知道答案的问题,直接把答案发过去。

**不适合**:跨机器(cmux socket 是本机的)、需要严格投递保证的生产链路(那该用真正的消息队列)、以及用户没同意的情况下打扰别人的会话。

## 依赖条件

| 依赖 | 要求 | 检查 |
|---|---|---|
| **cmux** | ≥ 0.64(按 0.64.10 校准) | `cmux version` |
| 调用位置 | **必须在 cmux 的终端内** | `cmux identify` 能打出 `caller` |
| shell | bash 3.2+(macOS 自带即可) | — |
| 其他 | `awk` / `grep` / `sed`;`python3` 仅用于长消息闸门的显示列宽计算(缺失时退化为字符数判断),**无需 jq** | — |

没装 cmux 的话:cmux 是一个多会话终端(把每个 AI 编码会话跑成独立 tab),先装它、把你的会话搬进去,本 skill 才有意义。`cmux` 二进制通常在 `/Applications/cmux.app/Contents/Resources/bin/cmux`,脚本会自动回退到这个路径。

目标会话是 **TUI agent**(Claude Code / Codex 等)时才需要全套规程。目标是**普通 shell** 时,发消息等于替它敲命令并回车——**那是执行,不是发消息**,务必先确认。

## 安装

```bash
# 用户级(所有项目可用)
git clone <this-repo> ~/.claude/skills/messaging-cmux-sessions

# 或者项目级
git clone <this-repo> .claude/skills/messaging-cmux-sessions

chmod +x ~/.claude/skills/messaging-cmux-sessions/scripts/*.sh
```

Claude Code 会自动发现 `SKILL.md` 并在相关场景加载。

## 5 分钟上手

```bash
S=~/.claude/skills/messaging-cmux-sessions/scripts

# 1. 我是谁?
cmux identify

# 2. 有谁?(含未选中的 workspace)
$S/send.sh --list

# 3. 给协作会话起个稳定的名字 —— 这是可寻址的前提
cmux rename-tab "demo-backend"
cmux rename-tab --surface surface:7 "demo-frontend"

# 4. 先干跑,确认定位对了
$S/send.sh demo-frontend '接口已定稿' --dry-run

# 5. 真发(看退出码,不要看屏幕)
$S/send.sh demo-frontend '接口已定稿,详见 /tmp/api-v2.md'
```

退出码就是你的重发循环:`0` 已提交 / `1` 没找到 / `2` 目标忙(空闲后 `--flush` 补交)/ `3` 需人工看 / `4` 用法错、标题重名或消息超宽 / `5` 目标没有 agent 输入框(死壳)。

## 全局项目配置(跨会话关联)

**没有配置也能用**(退化成按 tab 标题寻址),但跨项目协作强烈建议建——否则每一轮协作都要人肉重新说明"谁是谁、代码在哪"。

```bash
cp cmux-sessions.example.conf ~/.claude/cmux-sessions.conf
```

格式(`|` 分隔,`#` 注释):

```
# 别名        | cmux tab 标题 | 仓库路径                  | 分组     | 说明
demo-backend   | demo-backend   | ~/dev/demo-backend    | demo-project | 后端,接口以它为准
demo-frontend  | demo-frontend  | ~/dev/demo-frontend   | demo-project | 前端,消费上面的接口
```

| 字段 | 作用 |
|---|---|
| **别名** | 脚本和 agent 用它寻址,**稳定不变**。tab 改名时只改这一行,所有调用方不受影响。 |
| **tab 标题** | 必须与 cmux 侧栏显示的完全一致(`send.sh --list` 可查)。 |
| **仓库路径** | 审计/协作时跑 `git -C <repo> log` 用。支持 `~`。没有仓库就留空。 |
| **分组** | 同一产品/同一条协作线放一组,便于批量查看。 |
| **说明** | 给 agent 看的一句话:这个会话是干什么的。 |

用起来:

```bash
$S/send.sh demo-backend '接口已定稿'                                  # 别名寻址
$S/sessions.sh --group demo-project --status                             # 这条线上谁在线、谁在忙
git -C "$($S/sessions.sh --repo demo-backend)" log --oneline -1       # 它有没有新提交
```

路径可用 `CMUX_SESSIONS_CONF` 覆盖(便于团队共享一份、或按机器区分)。

## 最佳实践

**1. 先给 tab 命名,再谈协作。** 默认标题是当前目录或 shell 提示符,会随 `cd` 变化、彼此重复,无法稳定寻址。

```bash
cmux rename-tab "demo-backend"                              # 当前 tab
cmux rename-tab --surface surface:7 "demo-frontend"          # 别的 tab
cmux workspace-action --action rename --title "demo-project"   # 整个 tab 组
```

命名约定:**用职责不用路径**(`demo-backend` 而不是 `…/code/demo-project`);**同产品统一前缀**;**保证唯一**——重名时 `send.sh` 会直接拒绝并列出候选,这是特性不是 bug。

**2. 消息永远带来源和意图。** `send.sh` 会自动加 `(from <本会话>)`。手动补上意图标记:`[需回复]` / `[FYI]`。不带来源的注入消息,对方无法与真人指令区分。

**3. 长消息写文件,只发一行指针。** 长消息会 wrap 成多行,结尾的 `\r` 只提交最后一个可视行,其余留箱。发 brief、评审意见、任何含代码或 markdown 的载荷,一律:

```bash
$S/send.sh demo-frontend '请读并执行:/tmp/brief.md'
```

**4. 用退出码,不用眼睛。** 唯一可靠的成功判据是"消息签名已从对方 `❯` 输入框消失"——脚本替你做了。不要 grep 屏幕上的活动词:`Cogitated for` 这类**过去式**是已完成,会造成假阳性。

**5. 判断"对方做完了"要看带外信号。** `read-screen` 是瞬时快照,会在两次工具调用之间拍到假空闲。用 `git log` HEAD 变化、文件产出、或 ack 文件。

**6. 正确性重要时,升级到文件信箱 + ack。** 把文件系统当通道,`cmux send` 只当门铃。见 `references/collaboration-patterns.md`。

**7. 醒来先查自己的收件箱。** 对方忘了 `\r`,回复就卡在**你的**输入框里。每轮开始先 `read-screen` 自己的 surface。

**8. 注入是不可逆的。** 往运行中的 agent 发消息会打断/改变它的执行路径。除非用户已明确授权,否则先确认再发。

## 目录结构

```
messaging-cmux-sessions/
├── SKILL.md                        # 给 agent 读的主文档(铁律 + 规程 + 坑表)
├── README.md                       # 给人读的:场景、依赖、配置、最佳实践
├── PITCH.md                        # 对外介绍文案(三种长度)+ 3 分钟演示脚本
├── cmux-sessions.example.conf      # 全局注册表样例
├── references/
│   └── collaboration-patterns.md   # 进阶:文件信箱+ack、旁路审计、组看板、协作约定
└── scripts/
    ├── send.sh                     # 定位 + 查空闲 + 发送 + 验证 + 补 CR;--peek 探测卡箱
    ├── sessions.sh                 # 会话发现 + 项目关联(在线/忙闲/仓库路径)
    ├── group.sh                    # 按注册表分组广播,汇总每人投递结果
    ├── board.sh                    # 组共享看板:谁在做什么 / 待决 / 已定(决策落盘)
    ├── pending.sh                  # 待回复台账(send.sh --ask 的账);--group 组级视图
    ├── watch.sh                    # 零 token 看护:等空闲 / 盯账龄,条件满足注入唤醒
    └── busy-hook.sh                # Claude Code hooks 回调:写语义化忙/闲状态
```

## 多会话协作(3 个以上)

两两消息是 O(N²),且没人有全局视图。三件套:

```bash
$S/group.sh <组> '<消息>'             # 广播通知(契约变更、环境挂了),汇总投递结果
$S/board.sh <组>                     # 共享看板:谁在做什么 / 待决 / 已定
$S/pending.sh list --group <组>      # 组级台账:谁欠谁的回复,带账龄
$S/watch.sh idle --group <组>        # 组内任一成员空闲就唤醒协调者
```

**广播只用于「通知」,派活用「写文件 + 发指针 + `--ask` 台账」**;对话里定下的决策
当场 `board.sh --decide` 落盘——「已定」栏是唯一真相源。详见 `references/collaboration-patterns.md`。

## 故障排查

| 症状 | 原因 | 处理 |
|---|---|---|
| `Error: Surface is not a terminal` | 寻址没带 `--window` | 用 `send.sh`,或底层命令补上 `--window` |
| 退出码 2 | 目标忙,消息留在它输入框 | 等空闲,按提示跑 `send.sh <ref> --flush '<签名>'` |
| `✗ 目标不唯一` | 多个 tab 标题相似 | 用 `surface:N`,或给 tab 起唯一名字 |
| `✗ 未找到会话` | 标题对不上 | `send.sh --list` 看真实标题;注册表里的 tab 标题要与之一致 |
| 发了但对方毫无反应 | 消息卡在它输入框没提交 | `cmux read-screen --lines 40` 确认;`send.sh` 会自动补 CR |
| 对方回复一直没到你这 | 它发消息时没带 `\r` | `read-screen` **你自己的** surface,补一个 `\r` 吃进来 |
| 一个 `\r` 让对方重跑了上一条 | 往空输入框补了 `\r`,TUI 重放上一条输入 | 补之前先读 ≥30 行确认文本确实跟在 `❯` 后 |
| 老版本 cmux 上跨 workspace 找不到 tab | `tree` 只展开当前 workspace | 升级 cmux;或 `list-workspaces` → `tree --workspace workspace:N` |

## 已知限制

- **仅限本机**:cmux 通过 Unix socket 通信,不跨机器。
- **无投递保证**:底层是键盘注入。需要保证就用文件信箱 + ack 模式。
- **spinner 是开放集**:cmux/Claude Code 的"忙碌"提示词会新增,脚本匹配的是 `…` + 耗时的形态而非固定词表。
- **针对 Claude Code TUI 校准**:Codex 等其他 agent 的输入框行为可能略有差异,`\r` 提交与签名验证的原理相同,但提示符字符可能不是 `❯`。
