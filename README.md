# messaging-cmux-sessions

> 将 cmux 里的多个 AI 编码会话组织成团队，独立思考、主动协作，共同完成任务目标。

团队共享目标、职责和验收标准。每个 Claude Code / Codex 会话结合自己的项目上下文独立判断方案、实现与结论，主动提出问题和改进建议，与相关成员讨论并验证；协调者整合意见、处理依赖并跟进整体交付。执行、评审和协调各方都应接受基于证据的质询，按[团队工作流](references/team-workflow.md#共同目标与独立判断)共同改进结果。

消息投递、持久信箱、任务监听和共享看板支撑这一协作过程。cmux 提供底层终端输入能力，本 skill 通过来源核实、安全投递、状态记录和重试降低漏消息风险；最终以实际产物和独立验收确认目标达成。

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
- **团队分工协作** —— 围绕共享目标明确各会话职责，成员自主推进并相互讨论、交叉评审，以实际产物和必要测试独立验收。
- **跨仓库改动同步** —— 同一产品的多个仓库各占一个会话,改了协议定义的一方通知其余各方。
- **让卡住的会话动起来** —— 兄弟会话在等一个你已经知道答案的问题,直接把答案发过去。

**不适合**:跨机器(cmux socket 是本机的)、需要严格投递保证的生产链路(那该用真正的消息队列)、以及用户没同意的情况下打扰别人的会话。

## 依赖条件

| 依赖 | 要求 | 检查 |
|---|---|---|
| **cmux** | ≥ 0.64(按 0.64.10 校准) | `cmux version` |
| 调用位置 | 同用户外部进程也可；接收确认必须有真实 caller | `cmux identify` 能打出 `caller` |
| shell | bash 3.2+(macOS 自带即可) | — |
| **Python 3** | ≥ 3.8；用于结构化会话发现、持久信箱、忙闲状态和显示列宽计算 | `python3 --version` |
| 其他 | `awk` / `grep` / `sed`，**无需 jq** | — |

没装 cmux 的话:cmux 是一个多会话终端(把每个 AI 编码会话跑成独立 tab),先装它、把你的会话搬进去,本 skill 才有意义。`cmux` 二进制通常在 `/Applications/cmux.app/Contents/Resources/bin/cmux`,脚本会自动回退到这个路径。

目标会话是 **TUI agent**(Claude Code / Codex 等)时才需要全套规程。目标是**普通 shell** 时,发消息等于替它敲命令并回车——**那是执行,不是发消息**,务必先确认。

## 安装

```bash
# 用户级(所有项目可用)
git clone https://github.com/zhaozhi189/messaging-cmux-sessions.git ~/.claude/skills/messaging-cmux-sessions

# 或者项目级
git clone https://github.com/zhaozhi189/messaging-cmux-sessions.git .claude/skills/messaging-cmux-sessions

chmod +x ~/.claude/skills/messaging-cmux-sessions/scripts/*.sh
```

Claude Code 会自动发现 `SKILL.md` 并在相关场景加载。Codex 用户级安装可将目标目录换成 `~/.codex/skills/messaging-cmux-sessions`；两个工具共用源码的安装方式见下文。

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

退出码：`0` 操作成功（发送通知不代表任务完成） / `1` 没找到 / `2` 目标忙，先核查再决定是否补交 / `3` 状态未知或输入框已有文本 / `4` 参数、重名、超宽或锁冲突 / `5` 无法确认 agent 输入框 / `6` 会话发现或持久协议错误。

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

来源名称取实际会话标题，不能按角色或仓库名自行补 `-be` / `-fe`。有真实 caller 时默认使用自动来源，不设置 `CMUX_FROM` 覆盖。`caller=null` 不等于来源名称未知：用户或会话上下文已明确来源，或当前仓库路径、注册表映射与现场唯一会话一致时，可通过 `CMUX_FROM` 使用实际会话名。例如已明确来源是 `aimeter`，就显示 `(from aimeter)`；外部进程信息写入 brief。只有缺少或无法消除来源歧义时才使用“来源未识别（外部进程）”。显示标签不赋予 caller 或 ACK 身份。

新消息的 `CMUX_FROM` 与真实 caller 冲突，或通过 `CMUX_FROM_LABEL` 给普通消息改名时，退出 4，并在消息落盘和发送前停止。监听器展示标签继续显示“监听器 / 被监控对象”；旧消息和既有监听事件保留原 sender。

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
├── SKILL.md                        # 给 agent 读的操作入口与协作约束
├── README.md                       # 给人读的:场景、依赖、配置、最佳实践
├── PITCH.md                        # 对外介绍文案(三种长度)+ 3 分钟演示脚本
├── cmux-sessions.example.conf      # 全局注册表样例
├── references/
│   ├── collaboration-patterns.md   # 进阶:文件信箱+ack、旁路审计、组看板、协作约定
│   ├── claude-legacy-guide.md      # 旧版 Claude 行为与排障经验
│   └── team-workflow.md            # 团队派单、持续监听、讨论与验收
├── scripts/
│   ├── send.sh                     # 定位 + 查空闲 + 发送 + 验证 + 补 CR;--peek 探测卡箱
│   ├── sessions.sh                 # 会话发现 + 项目关联(在线/忙闲/仓库路径)
│   ├── group.sh                    # 按注册表分组广播,汇总每人投递结果
│   ├── board.sh                    # 组共享看板:谁在做什么 / 待决 / 已定(决策落盘)
│   ├── pending.sh                  # 待回复台账(send.sh --ask 的账);--group 组级视图
│   ├── watch.sh                    # 零 token 看护:等空闲 / 盯账龄,条件满足注入唤醒
│   ├── busy-hook.sh                # 生命周期回调:写语义化忙/闲状态
│   ├── protocol.py                 # 结构化发现、持久信箱与会话身份校验
│   ├── delivery-wait.py            # 原编号投递等待：忙/草稿只观察，精确门铃补交
│   └── task-watch.py               # 当次任务监听与持久事件通知
└── tests/
    ├── test_send.py                # 模拟 cmux 的发送与协议回归测试
    ├── test_protocol.py            # 外部进程只读消息与接收身份边界测试
    ├── test_routing.py             # 完整会话定位、特殊标题与来源冲突测试
    ├── test_delivery_wait.py       # 原编号投递等待与安全重试测试
    └── test_task_watch.py          # 模拟 cmux 的任务监听与通知测试
```

## 多会话协作(3 个以上)

两两消息是 O(N²),且没人有全局视图。三件套:

```bash
$S/group.sh <组> '<消息>'             # 广播通知(契约变更、环境挂了),汇总投递结果
$S/board.sh <组>                     # 共享看板:谁在做什么 / 待决 / 已定
$S/pending.sh list --group <组>      # 组级台账:谁欠谁的回复,带账龄
$S/watch.sh idle --group <组>        # 组内任一成员空闲就唤醒协调者
```

**广播只用于「通知」,派活优先用 `--durable` 持久信箱，长内容写文件后发指针**；旧的 `--ask` 台账保留兼容。对话里定下的决策
当场 `board.sh --decide` 落盘，注明证据、版本和适用范围；旧决策不能覆盖用户最新指令。详见 `references/collaboration-patterns.md`。

## 故障排查

| 症状 | 原因 | 处理 |
|---|---|---|
| `Error: Surface is not a terminal` | 寻址没带 `--window` | 用 `send.sh`,或底层命令补上 `--window` |
| 退出码 2 | 目标忙，消息可能仍在输入框 | 先读屏核对，只有确认本次消息卡箱后才用 `--flush`；持久模式沿用原编号和正文重试 |
| `✗ 目标不唯一` | 多个 tab 标题相似 | 用 `surface:N`,或给 tab 起唯一名字 |
| `✗ 未找到会话` | 标题对不上 | `send.sh --list` 看真实标题;注册表里的 tab 标题要与之一致 |
| 发了但对方毫无反应 | 消息卡在它输入框没提交 | `cmux read-screen --lines 40` 确认;`send.sh` 会自动补 CR |
| 对方回复一直没到你这 | 它发消息时没带 `\r` | `read-screen` **你自己的** surface,补一个 `\r` 吃进来 |
| 一个 `\r` 让对方重跑了上一条 | 往空输入框补了 `\r`,TUI 重放上一条输入 | 补之前先读 ≥30 行确认文本确实跟在 `❯` 后 |
| `外部进程没有接收会话身份` | 当前工具进程没有真实 caller，不能确定自己的收件箱或确认接收 | 已知编号时用 `send.sh --status <编号>` 读完整正文；按 brief 写进度与结果文件，不执行 `--inbox/--ack/--complete`，不取 focused 冒充 caller |
| 退出码 6 | cmux 访问失败、JSON 结构不兼容或持久协议错误 | 查看原始错误，核对访问权限、cmux 版本和消息身份；不将失败当作空列表 |

## 已知限制

- **仅限本机**:cmux 通过 Unix socket 通信,不跨机器。
- **无投递保证**:底层是键盘注入。需要保证就用文件信箱 + ack 模式。
- **spinner 是开放集**:cmux/Claude Code 的"忙碌"提示词会新增,脚本匹配的是 `…` + 耗时的形态而非固定词表。
- **支持 Claude / Codex 提示符**：识别 `❯` / `›`；自定义主题与建议词可能保守拒发，读屏仍属于启发式判断。


## Claude / Codex 共用源码安装

本仓库是统一维护源。两个工具的技能目录均使用软链接，修改与测试在仓库进行：

```bash
mkdir -p ~/dev
git clone https://github.com/zhaozhi189/messaging-cmux-sessions.git ~/dev/messaging-cmux-sessions
mkdir -p ~/.codex/skills ~/.claude/skills
ln -s ~/dev/messaging-cmux-sessions ~/.codex/skills/messaging-cmux-sessions
ln -s ~/dev/messaging-cmux-sessions ~/.claude/skills/messaging-cmux-sessions
```

仅在目标不存在时执行；已有安装先备份再迁移，不能直接覆盖。Codex 使用 `S=~/.codex/skills/messaging-cmux-sessions/scripts`。注册表和台账默认继续共用 Claude 路径，避免拆出两个协作数据空间。

发送方按目标加锁，锁冲突退出 4；读屏失败不报告送达；已有未提交输入不追加；多行消息写文件后发指针。`--ask` 返回回复命令，台账记录实际脚本路径和来源 surface，消息只携带短关联号，避免长命令使消息换行。Codex 收到请求可查询共享台账取得回复对象。

watcher 只有在确认投递成功时删除唤醒记录，读屏失败不会判为空闲。Codex 生命周期 hooks 需按实际版本独立配置和核查，安装技能不会自动配置 hooks。

验证：

```bash
python3 tests/test_send.py
python3 tests/test_protocol.py
python3 tests/test_routing.py
python3 tests/test_delivery_wait.py
python3 tests/test_task_watch.py
```

这些测试使用模拟 cmux，不会向真实会话发送消息。覆盖发送防护、回复台账、目标锁、无 caller 时的只读正文与确认拒绝、当次任务身份、进度时间戳、通知优先级与终止后的重试宽限。


## 外部进程访问

cmux 的 `automation.socketControlMode` 应设为 `automation`，允许同一 macOS 用户的外部进程访问本机 socket。该授权与 Codex 脚本审批规则分别生效。访问失败时 `send.sh --list` 返回非零并保留原始错误，不把失败报告成没有会话。

外部进程没有 cmux caller，不要伪造 `CMUX_SURFACE_ID`。来源展示按用户明确说明、启动上下文或任务交接中的绑定核实；尚无明确绑定时，可用当前仓库真实路径、注册表映射与现场唯一会话交叉核对。核实后设置 `CMUX_FROM` 为实际会话名，同名时追加已确认的 surface，外部工具进程信息和来源依据写入 brief；已绑定的 endpoint 发生漂移时先重新核实。不能仅凭 `caller=null` 就把已知来源降级为未知，也不能仅凭目录名、任务角色或 `focused` 猜来源。证据仍缺失或冲突时才设置 `CMUX_FROM='来源未识别（外部进程）'`。旧消息重试仍沿用原 sender，不改写历史。

需要 `--ask` 时在 brief 中指定回复文件。若有真实的回复接收会话，可设置 `CMUX_REPLY_TO=surface:N`；该编号必须现查。没有真实接收会话时由外部调用方前台读取回复文件；任务监听可用 `--notify` 显式绑定已验证的协调接收会话，不伪造 caller。

修改 socket 模式后执行 `cmux reload-config` 并用外部进程实际测试；若运行实例仍拒绝连接，需从 Automation 设置应用模式，或安排重启 cmux，不能把配置已写入当成已生效。


## 持久信箱与明确确认

优先用持久模式派活；旧的 `--ask` / `--reply` 保留兼容。两种模式不能混用。

```bash
S=~/.codex/skills/messaging-cmux-sessions/scripts
bash "$S/send.sh" demo-backend --durable '请阅读 /private/tmp/task.md，并按其中要求返回验收结果'
# 输出消息编号；重试沿用编号及原正文：
bash "$S/send.sh" demo-backend --durable --id m原消息编号 '请阅读 /private/tmp/task.md，并按其中要求返回验收结果'

# 收到指定编号后先只读完整正文；caller=null 也可执行：
bash "$S/send.sh" --status m原消息编号
cmux identify
```

收到“请查 cmux 收件箱：<编号>”通知时，先执行 `--status <编号>` 读取完整正文，再完整读取正文指向的 brief，不把门铃当作完整任务。`--status` 不调用 cmux、不修改消息状态。

`caller=null` 时不执行 `--inbox/--ack/--complete`，不取 `focused` 或伪造 `CMUX_SURFACE_ID`。在既定授权范围内继续处理任务、更新约定的进度与结果文件，并记录“协议接收确认不可用”，由协调者直接回读产物。caller 属于其他会话或 endpoint 已改变时，先核实归属，不冒用接收身份。只有记录中的真实目标且 endpoint 仍匹配时，才读取后 `--ack`、交付后 `--complete`。

读取正文或产物存在不会自动产生 ACK，不能据此手改 `received/completed`。`--complete` 是接收方显式声明完成，发送方仍需独立验收；该命令不会证明测试已通过。

以下命令仅适用于已核实身份的真实接收方，按接收、工作完成的实际阶段分别执行：

```bash
bash "$S/send.sh" --ack m原消息编号
bash "$S/send.sh" --complete m原消息编号
# 可额外列出自己的待处理消息：
bash "$S/send.sh" --inbox
```

状态区分为 `recorded`（正文原子落盘）、`notified`（终端通知确认提交）、`received`（目标确认接收）、`completed`（目标声明完成）。正文保存在共享台账目录的 `messages/<编号>.json`。通知失败仍保留正文；重试同编号与原正文，不创建新的重复任务。已确认通知、接收或完成时不再发送通知。通知提交后进程崩溃可能留下 recorded 状态，因此任何重复通知都应按编号处理，不重复执行已接收任务。

接收确认必须来自记录中的目标 surface，且 workspace UUID、tty 与 surface 组成的目标身份仍匹配。cmux 重启或目标改变后，旧编号不能盲目路由到新目标。外部调用方可用 `CMUX_FROM` 标注来源、查询 `--status` 并读取约定的回复文件；不会冒用 focused 会话作为 caller。

会话发现使用 `cmux --json tree --all`，需要 Python 3。完整读取并验证后才匹配目标或输出清单；数据缺失、格式异常或权限不足时直接报错，不使用部分结果。目标匹配保留原始标题，重名仍拒绝。`send.sh --list` 保留列格式，特殊标题中的竖线、控制字符和反斜杠在展示时转义；定位用原始标题或明确的 surface。

忙闲 hook 状态写入 `busy-v2/`，采用锁、原子替换及递增写入序号，绑定 workspace UUID、tty、surface。默认有效期 120 秒，可用 `CMUX_BUSY_TTL` 调整；旧格式、损坏、过期或身份不匹配均视为 unknown，再由调用方读取屏幕。序号表示加锁后的写入顺序，不能证明上游生命周期事件的发生顺序。现有 hooks 必须实际正常运行才能提供可靠状态；同一终端中重启 agent 但未改变 endpoint 的情况仍需生命周期正确清理，不能仅靠 endpoint 识别。

终端输入继续只发送一次正文或通知，补交只重试回车。读屏验证是启发式，持久信箱的接收确认比“输入框文本消失”更强。新命令仍通过已授信的 send.sh 入口调用，无需额外授信 protocol.py。

## 团队协作闭环

持久发送返回 2/3 时立即用 `delivery-wait.py --id <原编号> --state <绝对路径>` 继续等待，不能只报告后结束。它绑定原 endpoint/sender/body，权限与陌生输入只观察；到期未送达用 `--resume` 核实续挂，身份漂移停止旧路由。Codex 底栏识别支持 fast、上下文窗口提示和对齐的快捷键/告警文字；其它无法识别的行仍按陌生输入等待，不自动提交草稿。派单未送达由协调者前台分段等候，每次最多60秒；已完成交付的执行方按[后台移交步骤](references/team-workflow.md#交付后移交通知观察)核实PID、首轮状态及日志后恢复接收新任务。协调者按已登记的任务路径回读产物，不等门铃才验收；deadline_at和exit_code使投递到期可观察，后台存在不等于保证自动唤醒。

持久通知会在编号前显示短摘要，例如 `(from aimeter) [125604 契约核查] 请查 cmux 收件箱：<完整编号>`。使用 `send.sh <目标> --durable --summary '125604 契约核查' '<完整正文或文件指针>'` 指定简称；省略时摘取正文首个非空行，不读取引用文件。自动摘要移除路径和控制字符，纯路径使用“消息详情”；摘要最多32个显示列，窄窗口进一步截短或省略摘要。摘要和实际门铃首次落盘后固定，同编号重试不能更改，旧消息仍按原格式投递。接收方仍必须按完整编号读取正文，不能仅凭摘要执行任务。`--durable --dry-run` 可预览且不落盘，预览中的默认编号为占位值。

其它业务回复不能自动取消仍有效的门铃投递。SIGINT 停止监听会记录 `status=stopped`、`stopped_at` 和 `exit_code=130`，恢复需核实后显式 `--resume`；停止不会清除输入框或修改原信箱的送达状态。

有交付的派单默认附 task_id、仓库负责人、依赖、契约职责、产物和进度文件，并启动 `task-watch.py`。默认每 30 秒观察，5 分钟无进展提醒，2 小时监听到期；后台运行需核实 PID、首次状态及真实唤醒目标。已明确 blocked 时不额外制造 stalled；阻塞内容变化/解除会提醒，已恢复未送达告警保留历史并标为 superseded，抑制不等于送达。外部 caller=null 可显式 `--notify surface:N`，不冒充来源身份。

进度文件仅刷新 `updated_at` 时算心跳，不重置无进展计时，也不解除停滞告警；状态、产物、摘要等实际内容变化才算进展。已有监听状态恢复时兼容旧签名，避免把签名格式迁移误认为新进展。

监听区分权限等待、未交付的空闲、未知状态、会话身份变化和当次交付。通知只在状态变化时发送，失败沿用同消息编号重试。`completion_reported` 表示等待验收；协调者检查实际文件、diff 与跨项目契约后才标记 `accepted`。到期需要续挂或处理，不能当作任务完成。

团队讨论围绕证据和具体问题：生产方给出口径，消费方回读验证，各自在自己的仓库改动，协调者记录决策、整合待决项并验收。权限框由用户在目标会话处理；监听器不按键、不代批。

命令、进度 JSON、托管步骤与整改流程见 [团队工作流](references/team-workflow.md)。测试使用模拟 cmux，不向真实会话发送消息。
