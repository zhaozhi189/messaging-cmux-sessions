---
name: messaging-cmux-sessions
description: 当一个 AI 编码会话需要联系 cmux(多会话终端)里的另一个会话时使用——给同机的 Claude/Codex 兄弟会话发反馈或指令、把接口/结论转达给前端或后端会话、对另一会话的仓库做旁路审计、驱动并行 agent、或排查"消息发了对方没反应"。触发词(中/英)：给另一个会话发消息、发到那个 cmux tab、审计另一个 agent、多会话协作、会话之间同步；"talk to the other session"、"send this to that cmux tab"、"audit the other agent"、"collaborate between sessions"。
---

# 给 cmux 会话发消息(多会话协作)

## 概述

cmux 把每个 AI 编码会话跑成独立进程,各占一个 *surface*(tab)。会话之间读不到彼此的内存,但 cmux 暴露了一个 Unix-socket CLI:**`cmux send` 能往任意 surface 的终端打字 + 回车**——于是一个会话可以给另一个会话发消息,实现 AI↔AI 协作。

**本质:这是在往一个脆弱的 TUI 里注入键盘事件,不是消息队列。** 没有投递保证、没有回执。所有规程都是围绕这一点建立的。

三条铁律,违反必静默失败:

1. **一律用 `$S/send.sh`,绝不手搓裸发。**
2. **只在对方空闲时发。**
3. **往运行中的 agent 注入是不可逆动作——先跟用户确认。**

## 前置条件(15 秒自检)

```bash
CMUX=$(command -v cmux || echo /Applications/cmux.app/Contents/Resources/bin/cmux)
$CMUX version && $CMUX identify        # 有版本号 + 有 caller 信息 = 环境就绪
```

- **cmux ≥ 0.64**(本 skill 按 0.64.19 校准)。没有就先装 cmux,别继续。
- **必须从 cmux 内部的终端调用**——`identify` 要靠 `$CMUX_SURFACE_ID` 才知道"我是谁"。在 cmux 之外能发消息,但拿不到来源身份。
- **socket 安全模式**(cmux Settings > Automation > Socket Control Mode):`automation`(推荐,0600 socket 仅限当前 macOS 用户)或 `password`;**不要用 `allowAll`**——0666 socket 意味着本机任何用户都能往你的终端注入并执行命令。`off`/`cmuxOnly` 则外部 CLI 完全连不上(本 skill 失效)。
- 目标会话是 **TUI agent**(Claude Code / Codex 等)时才需要本 skill 的全套规程;目标是普通 shell 时,`send` 等于替它敲命令——**那是执行,不是发消息,更要先确认**。

**下文所有 `scripts/xxx.sh` 都相对于本 skill 目录,而你的 shell cwd 通常在项目里——先把它定死:**

```bash
S=~/.claude/skills/messaging-cmux-sessions/scripts   # 用户级安装
# 项目级安装则是 <项目>/.claude/skills/messaging-cmux-sessions/scripts
# 加载本 skill 时会告知它的 base directory,以那个为准
$S/send.sh --list
```

## ⭐ 铁律一:一律用 helper,不手搓裸发

**不要手拼 `cmux send` + `cmux send-key enter` 发正式消息。** 裸发没有验证,必然静默丢消息:一条会换行的消息这样发,会卡在对方输入框里没提交;终端打印了 `OK` 却什么都没发生。

```bash
$S/send.sh <目标> '<消息(单引号包裹)>'          # FYI:单向通知,不期待回复
$S/send.sh <目标> --ask '<问题>'               # 需回复:自动生成关联号 + 落台账
$S/send.sh <目标> --reply <corr> '<答复>'      # 回复:投递成功后自动消对方台账
```

`<目标>` 可以是 **注册表别名** / **tab 标题** / **标题子串(唯一时)** / **`surface:N`**。

**看退出码——它就是你的重发循环:**

| 退出码 | 含义 | 该做什么 |
|---|---|---|
| 0 | 已确认提交/已排队 | 完成 |
| 1 | 没找到目标 | 跑 `send.sh --list` 看真实标题 |
| 2 | **目标忙,消息留在它输入框(可能已被 TUI 排队)** | 标准后续:`watch.sh idle <ref> --flush '<签名>'`——零 token 等它空闲、自动补交、完事唤醒你;别自己轮询 |
| 3 | 多次补 CR 仍残留 | 人工看一眼那个 surface |
| 4 | 用法错 / 标题重名 / **消息超长被拒** | 前两者改用 `surface:N` 或更精确的标题；**超长改用「写文件 + 发一行指针」**（见下） |
| 5 | **目标没有 agent 输入框(死壳/普通 shell)** | 确认目标对不对;真要给 shell 执行命令,用裸 `cmux send`(那是执行,不是发消息) |

脚本已内置的事,你不用再操心:跨 workspace 全量定位、重名拒绝、防自发自收、自动加 `(from <本会话>)` 来源前缀(斜杠命令自动免前缀)、CR 提交、签名验证、退避补 CR(对方忙则停止补,防重复提交)、死壳拒发、斜杠命令的补全 popup settle、提交确认后 1s settle(`SEND_SETTLE=0` 关闭)。

**唯二仍需绕开 helper 的情况**:消息含**字面单引号 `'`**,或消息**超长**——两者都改用"写文件 + 发一行指针"(见下)。`【】()[]*!/` 这类元字符**不需要**,`send.sh` 用 `grep -F` 且不做 eval,单引号包裹即可安全传入。

**绝不"用眼睛验证"。** 唯一可靠的成功判据是:**消息开头的签名已从 `❯` 输入框消失**。不要靠 grep 屏幕上的活动词判断——滚动区里的**过去式**词(`Cogitated for` / `Brewed for`)会造成假阳性,而且**忙 ≠ 已提交**:消息可能还卡在箱里,agent 正忙于*上一个*任务。

## 认识环境:我是谁、有谁、怎么叫得动

```bash
$CMUX identify              # 我是谁:caller.surface_ref / workspace_ref / window_ref
$S/send.sh --list           # 有谁:全部会话(含未选中 workspace)
$S/sessions.sh --status     # 谁在线、谁在忙、各自的仓库在哪
```

`cmux tree --all` 的**文本**输出在 0.64.10 上已覆盖全部 workspace;`send.sh` 直接解析它,所以**跨 workspace 寻址不需要额外步骤**。若在更老的 cmux 上发现 `tree` 只展开当前 workspace,退回两步:`$CMUX list-workspaces` → `$CMUX tree --workspace workspace:N`。

### ⭐ 给 tab 重命名——这是让协作可寻址的前提

默认标题是当前目录(`…/dev/demo-project`)或 shell 提示符,会随 `cd` 变化、彼此重复,**无法稳定寻址**。开一个协作会话就先命名它:

```bash
$CMUX rename-tab "demo-backend"                        # 给自己(当前 tab)命名
$CMUX rename-tab --surface surface:7 "demo-frontend"    # 给别人命名
$CMUX workspace-action --action rename --title "demo-project"   # 给 workspace(tab 组)命名
```

命名约定(重名会让 `send.sh` 直接拒绝发送,所以务必唯一):

- **用职责,不用路径**:`demo-backend` / `demo-frontend`,而不是 `…/code/demo-project`。
- **同产品加统一前缀**,再按 workspace 分组,一眼看出谁和谁是一条线。若将来多套独立环境共用一个 cmux(如两份 skill 安装、多台机器同步),给前缀再加环境标识(firstmate 的做法是 home 名 + 路径短哈希),避免跨环境误匹配。
- **改名后同步更新注册表**——或者反过来:优先用注册表别名寻址,tab 改名不影响调用方。

### 全局注册表:让会话知道"兄弟是谁、仓库在哪"

`~/.claude/cmux-sessions.conf`(可用 `$CMUX_SESSIONS_CONF` 覆盖),`|` 分隔:

```
# 别名        | cmux tab 标题 | 仓库路径                  | 分组     | 说明
demo-backend    | demo-project       | ~/dev/demo-project     | demo-project  | 后端,接口以它为准
demo-frontend    | demo-frontend    | ~/dev/demo-frontend  | demo-project  | 前端,消费上面的接口
```

有了它:

```bash
$S/send.sh demo-backend "接口已定稿"                        # 用别名寻址,tab 改名不受影响
$S/sessions.sh --group demo-project --status                   # 这条线上谁在线、谁在忙
git -C "$($S/sessions.sh --repo demo-backend)" log --oneline -1   # 审计:它有没有新提交
```

### 三个以上会话:用 `group.sh` 广播,别逐个发

注册表的 `分组` 列此前只是 `sessions.sh` 的列表过滤器,没有操作能力。会话一多,
逐个 `send.sh` 有两个真问题:**每次发送都是独立的失败点**(发五个漏一个不会有人发现),
**谁收到了、谁卡箱了、谁离线没有一处能一眼看全**。

```bash
$S/group.sh --list                        # 有哪些组、各自成员
$S/group.sh demo-project --status              # 组内谁在忙/闲/离线
$S/group.sh demo-project '接口契约已更新'        # 广播(自动跳过自己),汇总每人的投递结果
$S/group.sh demo-project '<消息>' --exclude demo-frontend
```

退出码:`0` 全部送达;`2` 有成员卡箱(按提示逐个 `--flush` 补交);`1` 有成员发送失败。

**广播只适合「通知」,不适合「派活」。** 派活要一人一份、内容不同,还要各自跟踪回复,
那是写文件 + 发指针 + `--ask` 台账的活。广播用来同步全组都该知道的事:
契约变更、口径改了、环境挂了、里程碑过了。

**成员超过三个时,别用 N² 的两两消息维护共识**——没人有全局视图。
改用一个共享看板文件(谁在做什么、卡在哪、等谁),各会话读写同一份,
消息只用来提示「看板更新了」。见 `references/collaboration-patterns.md`。

**没有注册表也能用**——退化成按 tab 标题寻址。但跨项目协作(前端↔后端、审计者↔被审计者)强烈建议建,否则每轮都要人肉重新说明"谁是谁、代码在哪"。

## 底层命令(仅当 helper 不适用)

```bash
$CMUX send --window window:1 --surface surface:2 "your message\r"
```

- **`\r`(CR)= 回车 / 提交。** Claude Code 的 TUI 把 `\n`(LF)当作*输入框内换行*、**不提交**。
- 结尾放**一个** `\r`;中间不要有 `\n` / `\r`。
- **寻址必须带 `--window`**,即使用 UUID 也带,否则报 `Surface is not a terminal`。
- 消息若已卡在箱里,单独发一个 `\r` 可提交——**但先确认箱里确实是那条消息**,往空箱发 `\r` 会让 TUI 重放上一条输入。

裸发之后的验证方式必须和 `send.sh` 一致:读 ≥40 行、确认签名已从 `❯` 框消失。

## ⭐ 长消息 / 含字面单引号 → 写文件,发一行指针

> **2026-08-29 起闸门按「显示列宽」判,阈值取目标终端的真实宽度**(退出码 4)。
> 早先版本按**字符数**判、阈值固定 260,这两点都是错的:终端按**列**换行,而中文占 **2 列**,
> 按字符数会系统性低估;固定 260 又约等于常见终端宽度的 1.7 倍,等于把「保证换行」的
> 整段区间放行了。实测一条 **153 字符 / 159 列**的消息在 149 列宽的终端里必然 wrap,
> 却轻松通过了 260 的闸门 —— 退出码 0,消息卡箱,两个会话白等一轮(2026-08-29 踩)。
> 阈值仍可用 `SEND_MAXCHARS` 覆盖,`SEND_MAXCHARS=0` 关闭(自负卡箱风险)。

**为什么要强制,而不是"建议"**:超长消息会打破投递验证的两条前提,导致**退出码 0 但消息其实卡在对方输入框里**——比明着失败更糟,因为你会据此以为对方已收到,然后基于错误前提往下走(实测连翻三次,其中一次误报了两轮进度)。

两条被打破的前提:

1. **签名假设「必落在 `❯` 那一行内」** —— 只在箱里干净时成立。若上一条已卡在箱里,本条签名就落在中间行;
2. **`box_line` 只取最后一个 `❯` 行、`read_tail` 只读 40 行** —— 长消息 wrap 后,签名滚出读取窗口。

两者叠加 ⇒ 签名"查不到"被判成"已提交"。**根治办法不是把读取窗口调大(治标),而是不让消息 wrap。**

一条短指针一劳永逸:

1. 把完整内容写到一个文件(scratchpad 即可——同机跨会话可读)。
2. 发一条**短**的单行消息:
   ```bash
   $S/send.sh demo-frontend '请读并执行:/abs/path/to/brief.md'
   ```

这是发 brief、多问题评审、任何含代码/路径/markdown 载荷的验证过的模式。

## 验证投递 & 判断空闲/忙碌

`cmux send` 返回 `OK` 只表示 cmux 投递了按键,**不代表 TUI 提交了**。

```bash
$CMUX read-screen --window <w> --surface <ref> --lines 40
```

- **已提交/空闲** → 末尾提示符行是孤零零的 `❯ `,你发的文本不在其后。
- **卡住(未提交)** → 文本还跟在 `❯` 后 → 补一个 `\r`。**读 ≥40 行**,只读末尾几行会被状态栏挤掉 `❯` 行,误判为"已提交"。
- **⭐ 语义忙闲优先(hooks 权威源)**:Claude Code 会话的忙闲状态由 hooks 写入 `~/.claude/cmux-messaging/busy/surface_N`(`UserPromptSubmit→busy`,`Stop→idle`,`SessionEnd→删除`;见 `scripts/busy-hook.sh`)。判忙先读这个文件:`state=busy` 就是忙、`state=idle` 就是闲,**不用再刮屏猜**。文件缺失/超 2h = unknown——unknown 绝不当 idle 用,退回下面的读屏启发式。Codex 等无 hooks 的目标永远走读屏。`send.sh` 已内置这套优先级。
- **忙(读屏启发式,仅语义状态 unknown 时用)** → 末尾出现 `esc to interrupt`,或一条**进行时** spinner 行(`Noodling…` / `Simmering…` / `Baking…`——这是**开放集**,会冒新词,匹配尾部 `…` + 耗时,别用固定词表)。`Brewed for` / `Cogitated for` 是**过去式** = 已完成 = 空闲。⚠️ 带 `--scrollback` 读屏时只看**末尾 ~15 行**,更早的历史里全是过期的忙碌行。
- **⚠️ Ghost text**:Claude Code 输入框会显示灰色**建议词**,读屏时与卡箱文本无法区分——所以判成功一律用**消息开头签名**匹配,不要用"箱里有没有字"。想明确知道"对方箱里到底有没有东西",别读屏猜,用 **`send.sh <目标> --peek`**:它注入一个空格再退格(净效应为零),真文本注入后仍在、ghost 一有真实输入就消失。退出码 `0` 箱空(含 ghost)/ `2` 真卡箱 / `3` 目标忙无法探测。
- **⚠️ read-screen 是单次瞬时快照。** 轮询时某次快照恰好落在两次工具调用之间,会拍到瞬间的空 `❯`、误判为空闲。要么连续 2 次读都空闲才算,要么盯**带外信号**:目标仓库的 `git log` HEAD 变化、某文件被写、或 `~/.cmuxterm/claude-hook-sessions.json` 里对应 `surfaceId` 的 `lastSubtitle: Waiting`。
- **⚠️ 空闲 ≠ 卡住。** 空的 `❯` 且无 spinner,常意味着 agent **做完了、在等你回答**(它提了问题)。补 `\r` / 重发前先读 ≥30 行看它最后一句是不是提问,否则会把它带跑。

## 双向对话——回复会回到你这

cmux 消息是**双向、多轮的,不是发完即弃**。对方往你的 surface 跑 `cmux send` 并以 `\r` 结尾,它的消息就作为一条内联用户消息到你这。把协作当聊天:

- **每条消息都带来源前缀**(`send.sh` 自动加)。不加的话,一条注入的行和真正的用户指令无法区分——你不知道谁在说、往哪回。
- **标注意图用 `--ask` / `--reply`,不要靠散文约定**:`--ask` 自动生成关联号、在消息里带上回复方式、并在**投递前**把请求落进台账 `~/.claude/cmux-messaging/pending/`;对方用 `--reply <corr>` 回复,投递成功自动消账(同机共享文件系统,双方读写同一份台账)。**送达 ≠ 已回**——台账只认带同一关联号的回复。
- **收到 `[需回复 <corr>]` 的消息,回复时必须用消息里给出的 `--reply <corr>` 命令**,否则发起方的台账永远挂着。
- **⭐ 每次醒来先跑 `$S/pending.sh`**:看"谁欠我回复"(账龄大了就催或升级给用户)和"我欠谁回复"(赶紧回)。没有 watcher,台账靠这条规程闭环;对方事实上已回但没用 `--reply` 时,用 `pending.sh resolve <corr>` 手动消账。
- **一条回复常要扇出**:后端定稿了 API → 你转发给前端会话。用一个文件维护线程:谁在等谁的什么。

> ⚠️ **`\r` 规则是对称的——也是回复被静默丢失的头号原因。** 对方忘了结尾的 `\r`,它的回复就卡在**你的**输入框里没提交、永不到达:你在它眼里"没反应",它在你眼里"没声",双方都不知为何。

**接收端也要守:**

- **每次你醒来时**(定时 tick、任一协作轮次),**先 `read-screen` 你自己的 surface**。若 `❯` 后有你没打的文本——对方没带 `\r` 的回复——补一个 `\r` 把它吃进来。
- **在 brief 里提前约定**:要求每个会话给你发消息时用 `send.sh`(它总会补 CR)。一句话能挡掉大多数丢回复。
- **别把"没回复"当成"对方无视我"**——先排除"卡在你箱里"和"卡在它箱里"。沉默通常是漏了 CR,不是拒绝。

### 对方不回复时

投递(`OK`)≠ 得到答复。对方可能在忙、卡在权限弹窗、或就是不回。**用 `--ask` 发的请求不怕忘:台账在 `pending.sh` 里挂着,每次醒来都会看到账龄。**

> 设计说明:消息标记用纯文本 `[需回复 corr]` 而不是 firstmate 式的 U+2063 不可见字符——零宽字符能否进 read-screen 的文本缓冲不确定,一旦丢失会连带破坏签名验证;纯文本标记人和脚本都能可靠读到。

- **有界等待,绝不无限**:轮询几次就设上限,别为等回复空转到底。
- **绝不往忙碌的目标重发**——消息会排队或卡住,猛发只会雪上加霜。发一次,等空闲。
- **识别"无法回复"状态**:卡在权限/确认弹窗(需真人)、错误循环、或空闲但你的问题被忽略。
- **超过上限就升级给用户**:报告你发了什么、对方现在什么状态。**不要**默默一直重试。

## 零 token 看护:等人别用轮询,用 watch.sh

**不要为"等对方空闲/等催办时机"反复 read-screen 轮询——那是烧 token 空转。** `watch.sh` 起一个纯 bash 后台进程睡在条件上,条件满足才把一条 `[watcher]` 消息注入本会话唤醒你(你在忙则自动排队,turn 结束后进来):

```bash
$S/watch.sh idle <目标> --flush '<签名>'    # send.sh 退出码 2 的标准后续:
                                            # 等目标空闲 → 自动补交卡箱消息 → 唤醒我核实
$S/watch.sh idle <目标> --note '干完活了叫我看结果'   # 等对方跑完长任务
$S/watch.sh idle --group <组>               # 协调者:组内任一成员空闲就唤醒,并说明是谁
$S/watch.sh overdue --age 30                # 值班 4h:我发出的 --ask 挂超 30min 就叫醒我去催
$S/watch.sh list                            # 活着的 watcher + 未送达的唤醒
$S/watch.sh stop [idle|overdue|all]         # 停止(stop idle 停掉全部 idle watcher)
$S/watch.sh idle <目标> --once              # 调试:只判一次忙闲,不后台化不注入
```

可靠性合约(borrowed from firstmate):**唤醒先写持久队列 `wake-queue/` 再注入,注入确认(提交或已排队)才删**。所以:

- 收到 `[watcher]` 开头的消息 = 看护条件满足了,按消息内容行动;
- **醒来时 `watch.sh list` 里出现"⚠️ 未送达的唤醒"** = watcher 尝试注入失败(多半 cmux 重启导致 ref 失效),队列文件里是它想告诉你的话——读完处理掉;
- 判忙口径与 send.sh 一致(语义状态文件优先,unknown 读屏),连续 2 次判闲才触发,防单次快照误判;
- **idle watcher 按(会话,目标)记一个**——同时盯多个目标就启动多个,互不挤占;同一目标重复启动才会被拒。协调者盯全组用 `idle --group <组>`,别为每个成员起一个再手动轮换。overdue 每会话一个。超时自动下岗(idle 默认 30min 且会唤醒告知,overdue 默认 4h 静默退出)。

## 进阶:可靠闭环与多会话编排

正确性重要时(任务交接、评审结论、接口交接、跨会话决策),**别把终端当通道——把文件系统当通道,`cmux send` 只当门铃**。文件信箱 + ack 协议、旁路审计者配方、并行 agent 编排,见 `references/collaboration-patterns.md`。

## 关键坑(全部实战踩过)

| 现象 | 原因 | 解法 |
|---|---|---|
| `Error: Surface is not a terminal` | ref 按错误的 window 解析 | 永远带 `--window`(即使用 UUID) |
| 文本进了输入框却**从不提交** | 消息以 `\n` 结尾,或目标当时**忙** | 用 `\r` 结尾;确保目标空闲;卡箱就补一个 `\r` |
| 返回 `OK` 但下游没反应 | `send` 只投递按键,TUI 没提交 | `read-screen` 验证签名是否已从 `❯` 消失;或直接用 `send.sh` |
| 断定"已发送"但消息**其实还卡在箱里** | 用活动词/spinner 验证——命中滚动区里的**过去式**词是假阳性;且忙 ≠ 已提交 | 只用"签名是否已从 `❯` 消失"验证(读 ≥40 行) |
| 一个 `\r` **重新提交了上一条消息** | 把已完成的 `❯` 行误当"卡住",往**空箱**发了 `\r` → TUI 重放上一条输入 | 补 `\r` 前先读 ≥30 行确认未提交文本**确实跟在 `❯` 后**;空 `❯` = 别补 |
| 长消息只提交了最后一行 | wrap 成多行后 `\r` 只提交一个可视行 | 别追着补——清空箱,改用**写文件 + 发指针** |
| **消息没超字数闸门却仍卡箱** | 闸门旧版按**字符数**判、阈值固定 260;终端按**显示列**换行且中文占 2 列 —— 153 字符可以是 159 列 | 已修:按显示列宽判、阈值取目标终端真实宽度。旧版请升级 `send.sh` |
| **对方的回复始终没到你这** | 对方发回复时**没带 `\r`** → 卡在**你的**输入框 | `read-screen` 你自己的 surface,补一个 `\r` 吃进来 |
| **`--flush` 反复无效,且目标一直 idle** | 箱里根本没有东西——你看到的是 **ghost text**(灰色历史建议词),读屏与卡箱文本长得一样 | 别再 flush。用 `send.sh <目标> --peek` 确证:退出码 0 = 箱空(ghost),2 = 真卡箱 |
| 消息发给了错误的会话 | 多个 tab 标题重名/相似,旧逻辑静默取第一个 | 用 `send.sh`(重名直接拒绝并列候选);给 tab 起唯一名字 |
| Ctrl+U / 退格清不干净 | TUI **吞掉批量按键**,只有部分生效 | 趁目标空闲重复 2-3 轮退格洪流(见下) |
| 全新 surface 读屏报 `internal_error: Failed to read terminal text` | **首次 `send` 写入前**,read-screen 必失败(任何 --lines、等多久都一样);首次写入后永久可读 | 就绪探测用 `list-panes`(结构化检查,无此坑),别用读屏 |
| 隔天按缓存的 id 发错 / 发不出 | workspace/surface **UUID 不跨 cmux 重启存活**(重启后全部重新生成) | 绝不缓存 id 过夜;每次按标题/别名重新解析(`send.sh` 每次都全量重解析) |
| `close-workspace` 返回 OK 但 workspace 还在 | 关的是窗口里**最后一个** workspace——cmux 保持每窗口 ≥1 个,静默 no-op 仍报成功 | 先在该窗口建一个一次性 sibling workspace,再关目标 |
| read-screen 要 40 行只拿到十几行 | 返回受**当前视口高度**截断(实际行为,非 bug) | 加 `--scrollback` 再 `--lines N`(`send.sh` 已内置) |
| 发 `/compact` 等斜杠命令没生效 | ① 补全 popup 吞掉紧跟的 Enter;② 加了来源前缀就不再是斜杠命令 | `send.sh` 已内置:斜杠命令免前缀 + 先发文本 settle 1.2s 再单发 CR |
| 刚确认提交就读屏,误判对方"空闲" | 提交确认 ≠ turn 已启动,harness 起 turn 要约 1 秒 | `send.sh` 已内置提交后 1s settle(`SEND_SETTLE=0` 关闭);手工读屏也等一拍 |
| 消息被死壳当命令执行 | 目标 agent 已退出只剩 shell,注入文本 + CR = 执行任意命令 | `send.sh` 已内置死壳防线:屏幕无 `❯`/`›` 输入框直接拒发(退出码 5) |

清空卡住的输入:

```bash
BS=$(printf '\177%.0s' $(seq 1 300)); $CMUX send --window window:1 --surface surface:2 "$BS"
```

## 常见错误

- 忘了 `--window` → "not a terminal"。
- 用 `\n` 而非 `\r` 结尾 → 文本进箱却不提交。
- 在目标生成中途发 → 消息卡住,看着发了其实没。
- 未经用户确认就往另一个 agent 推消息 → 不可逆、打扰。
- 把审计笔记写进目标的仓库 → 污染它的工作区。
- 把超长消息硬塞进 `send` → wrap 重提交地狱。**写文件,发一行指针。**
- 绕开 `send.sh` 手搓,然后用活动词"验证" → 最典型的翻车路径。
- 把空的 `❯` 当"卡住"去补 `\r` → 可能重放上一条;agent 往往只是**在等你回答**。
- 信单次 `read-screen` 判"完成" → 用连续 2 次读,或盯 `git` HEAD / 文件产出。

## 速查

```bash
CMUX=$(command -v cmux || echo /Applications/cmux.app/Contents/Resources/bin/cmux)
$CMUX identify                                          # 我是谁
$S/send.sh --list                                  # 有谁(全部 workspace)
$S/sessions.sh --status                            # 谁在线/在忙 + 各自仓库
$S/group.sh <组名> --status                        # 只看某个协作组
$S/group.sh <组名> '<消息>'                         # 广播给全组,汇总投递结果
$CMUX rename-tab "demo-backend"                           # 给 tab 命名(可寻址的前提)
$S/send.sh <目标> '<消息>'                          # FYI(定位+死壳防线+CR+验证+补 CR)
$S/send.sh <目标> --ask '<问题>'                    # 需回复:关联号+台账
$S/send.sh <目标> --reply <corr> '<答复>'           # 回复:成功后自动消账
$S/pending.sh                                      # ⭐ 每次醒来先看:谁欠谁的回复
$S/pending.sh resolve <corr>                       # 手动消账
$S/watch.sh idle <目标> --flush '<签名>'            # 零 token 等空闲+补交+唤醒(退出码 2 的标准后续)
$S/watch.sh overdue                                # 值班盯台账账龄,超龄唤醒催办
$S/watch.sh list                                   # watcher 状态 + 未送达唤醒
$S/send.sh <目标> --flush '<签名>'                  # 退出码 2 之后补交
$S/send.sh <目标> --peek                           # 对方箱里有没有东西?(区分 ghost text)
$S/send.sh <目标> '<消息>' --dry-run                # 只看定位结果,不发
$CMUX read-screen --window <w> --surface <ref> --scrollback --lines 40   # 验证 / 判忙闲
cat ~/.claude/cmux-messaging/busy/surface_N             # 语义忙闲(hooks 写入)
$CMUX send --help ; $CMUX docs api                      # 更多
```
