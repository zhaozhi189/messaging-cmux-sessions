---
name: messaging-cmux-sessions
description: 在本机 cmux 中发现、联系和协调多个 Codex 或 Claude Code 会话；用于跨项目团队派活、持续任务监听、接口讨论、交叉评审、独立验收和共享决策。用户称 cmux-session 时使用此技能。
---

# cmux 多会话协作

cmux 发送的是终端输入，返回成功不代表任务完成。仅对已确认的 agent 会话发消息；普通 shell 会把消息当作命令执行。

## 定位与使用

以本次加载的 SKILL.md 所在目录为技能根目录，设置 `S=<技能根目录>/scripts`。Codex 用户级安装通常是 `~/.codex/skills/messaging-cmux-sessions`，不要引用 Claude 专属路径。

```bash
S=/本次加载的技能根目录/scripts
bash "$S/send.sh" --list
bash "$S/sessions.sh" --all --status
bash "$S/send.sh" surface:7 '请阅读 /private/tmp/brief.md' --dry-run
bash "$S/send.sh" surface:7 --ask '请阅读 /private/tmp/brief.md'
bash "$S/pending.sh" list
```

发送前现查会话。优先注册表别名或明确的 `surface:N`，标题必须唯一。不得根据旧编号、默认目录标题猜目标。当前调用方通过 `cmux identify` 定位。

消息的 `from` 使用实际来源会话名，不能按任务角色或仓库名猜测、拼接 `-be` / `-fe`。有真实 caller 时，按 `caller.surface_ref` 查询当前会话标题，默认让 send.sh 自动生成来源，不设置 `CMUX_FROM` 覆盖它。标题缺失时使用已确认的 surface 编号；`focused` 不是来源身份。

`caller=null` 仅表示工具进程没有 cmux 接收确认身份，不等于来源名称未知。来源展示与 ACK 身份分别判断；先核实来源上下文，再决定是否使用“来源未识别”，不能仅凭 caller 为空就降级。

注册表默认沿用 `~/.claude/cmux-sessions.conf`，台账沿用 `~/.claude/cmux-messaging`，这是为了与现有 Claude 会话共享数据；可分别通过 `CMUX_SESSIONS_CONF`、`CMUX_MSG_DIR` 覆盖。所有协作会话必须使用同一份注册表和台账。没有注册表也可按 surface 或唯一标题寻址。

## 派活与验收

用户授权联系相关会话后，通过 `send.sh` 发消息。长内容、代码、Markdown 和多行内容写到双方可读的绝对路径，只发送简短指针。brief 写清共同目标、task_id、负责的仓库及修改范围、依赖、产物与进度文件、验收条件和回复对象。整体任务已授权的范围内，相关成员可直接讨论和交叉评审，无需每次重新询问能否联系。

跨项目修改默认派给该项目自己的 cmux 会话。未找到对应会话时询问用户，不直接修改其他仓库。需要交付或回执的任务派出后，默认启动下面的任务监听；`watch.sh idle <目标>` 只提供忙闲线索，不能替代任务监听。最终自行查文件、diff、测试结果，不能把空闲或对方回复当完成证明。Git 推送由用户执行。

不要把 cmux 的独立进程会话与当前 Codex 的内部 sub-agent 混淆；本技能通过 cmux socket 工作。

## 团队任务闭环（有交付的派活必读）

派出任务后，协调者负责跟到验收通过。不能以“消息已发出”“对方空闲”“收到 done”结束协调。一次性 FYI 无需长期监听。多项目任务按 [团队工作流](references/team-workflow.md) 明确负责人、契约生产方、消费方和评审方；按依赖派发，允许无依赖工作并行。

默认每 30 秒检查任务，5 分钟无进展提示协调者检查，2 小时监听到期。这些是可调的观察周期，不是任务完成期限。监听器核对任务编号、派单时间和目标 endpoint，检查进度文件、产物与屏幕；普通进度静默，只对重要协调事件通知；通知前缀显示监听器及被监控对象，失败保留原消息编号、正文、来源与标签重试。不同目标无依赖任务可并行派发，同目标串行，各自监听与验收，具体安全例子见团队工作流。

```bash
# TARGET 和 RECEIVER 均须现查并读屏确认是相应 agent；不得取 focused 冒充 caller。
python3 "$S/task-watch.py" --task-id demo-backend-task \
  --target "$TARGET" --notify "$RECEIVER" \
  --result /private/tmp/team-task/backend-progress.json \
  --state /private/tmp/team-task/backend-watch.json --since "$DISPATCH_TS"
```

进度文件由执行方原子更新，格式与后台托管示例见工作流。`--notify` 显式绑定已经核实的协调接收会话，支持外部 `caller=null`；它不会赋予调用方该会话身份。没有合法接收会话时省略 `--notify`，由协调者前台分段等待读取状态。不要声称存在自动唤醒。

- `approval_required`：提示用户在目标会话处理权限框；持续监听解除状态。不得代按 Yes、y、Enter 或 Esc，也不能把队友请求当作用户授权。
- `idle_unfinished` / `stalled` / `blocked`：查阻塞证据，在原任务范围内续派或组织讨论，继续监听。不要反复发送整份任务。已有明确 blocked 时不额外产生 stalled，阻塞内容变化或结构化进度解除会提醒；blocked_cleared 仅表示阻塞解除，不等于验收。恢复事件可将未送达的旧 stalled/blocked 标为 superseded 并抑制重试，保留编号、正文、sender和历史，不能把抑制说成送达或清空已有门铃。
- `unknown`：观察失败，不能推断空闲或完成。`session_changed`：停止旧路由，重新发现并核实目标再绑定；旧结果不能沿用。
- `completion_reported`：只表示执行方提交了当次产物。协调者读文件和 diff、核对跨项目契约，按风险执行必要验证，之后才把看板标为 `accepted`。整改后重新监听同一工作项的新一轮交付。
- `timed_out`：监听到期，不是任务完成；协调者读取原因；能够续挂时必须续挂，环境确实不可用时报告阻塞及恢复条件，不能把报告限制当成托管成功。用户暂停或取消时停止相应监听。

协调者负责未验收任务的持续观察，结束回合前核对监听 PID、首次状态、日志及通知路由，并保存任务、进度、产物与监听路径。后台进程运行不等于唤醒已验证；通知失败仍由协调者按已登记任务直接回读结果，不等门铃才验收。仅写命令或一次 `--once` 不算持续托管；无法托管时由协调者前台分段观察。执行方交付落盘后按下述规则移交投递观察，恢复接收新任务，不能一直占住会话等门铃。

讨论针对具体分歧并带文件证据。契约生产方提出口径，消费方实际回读并验证；协调者汇总决策、适用范围和未决问题。技术共识不会自动批准业务待定项、扩大任务范围或替用户授予权限。共享看板记录协作事实，不能覆盖用户最新指令或仓库实际内容。

## 回复与退出码

`--ask` 返回关联号和回复命令，并把 `reply_script`、`reply_target` 写入共享台账。收到请求后查询对应记录，用记录中的脚本和来源 surface 回复：

```bash
bash "$S/pending.sh" list
bash "$S/send.sh" surface:6 --reply a1234567890-123 '验收结果见 /private/tmp/result.md'
```

关联号沿用请求原值。成功投递回复才消账。来源编号可能因 cmux 重启失效，失效时重新定位来源，不盲目使用旧编号。

- `0`：操作成功；普通发送表示签名离开可识别的当前输入框，持久模式也可能表示已有通知或接收记录。不代表对方处理完成。
- `1`：目标未找到。
- `2`：目标忙，消息可能仍在输入框；先读屏核对，禁止盲目重发。
- `3`：投递状态不明、输入框已有文本或需要人工核查。
- `4`：参数、超宽消息、目标重名或目标发送锁冲突。
- `5`：无法确认 agent 输入框，拒绝发送。
- `6`：会话发现或持久协议错误，查看原始错误后核查权限、结构或消息身份。

`--flush '<签名>'` 只用于已确认属于本次发送的卡箱消息，忙时不补交。`--peek` 会输入空格和退格，并非只读；仅在授权且目标空闲时使用。并发发送同一目标采用 mkdir 互斥锁，锁冲突时稍后重试；进程异常退出留下锁时核实 pid 后人工清理，不自动抢锁。

## 忙时投递与交付移交

持久发送返回 2/3 时，正文落盘不等于门铃送达；不能仅报告“目标忙”后结束回合。立即沿原编号启动 `delivery-wait.py`；派单未送达时由协调者前台分段等待，每次等待不超过 60 秒。已完成交付的执行方使用[团队工作流的后台移交](references/team-workflow.md#交付后移交通知观察)，核实后台后恢复接收新任务；不同目标各用自己的状态文件。它只处理已有信箱记录，不重新创建任务，也不假设 `watch.sh idle` 能唤醒外部 caller。

```bash
# MESSAGE_ID 是原消息编号；前台运行，持续读取输出，不能只写命令后结束。
python3 "$S/delivery-wait.py" --id "$MESSAGE_ID" \
  --state /private/tmp/team-task/delivery-watch.json --interval 30 --timeout 1800
```

监听器检查原状态和 endpoint：notified/received/completed 时结束；忙、权限框、未知画面或陌生输入保持等待。空闲后仅精确匹配本编号的完整门铃可补交回车；框内没有门铃且为空或已知默认提示时，用原编号、原 sender 和原正文重试。不清空输入、不重发整份正文、不代按审批按钮、不伪造 caller 或 ACK。已识别的 Codex 底栏包含 reasoning=default、版本尾部 Main [default] 、tab to queue message，以及单独的“⚠ N warnings · f2 to view”；排队提示不是授权，也不代表空闲，仍先检查忙碌和权限。屏幕仍是启发式；折行通知与无法识别的 footer 保守等待，人工核实，不使用探针或强制清空。

`--once` 只对账一轮，未送达退出 2，不能把它当作持续托管。投递等待退出 0 只表示已有通知或接收记录，5 表示本轮到期、6 表示身份改变、4 表示参数/记录/重复监听错误。到期后核实原任务仍有效及 endpoint 未变，立即沿原参数加 `--resume` 续挂；确实无法恢复时写明原因与恢复条件。身份变化停止旧路由，重新核实归属，不能把旧编号交给新 surface。

外部 caller=null 不承诺自动唤醒。后台托管必须有 brief 指定的协调者负责按任务路径回读状态、退出码和交付，不要求门铃先送达才算建立观察责任。核实 PID、首轮状态及日志；无法核实启动则明确报告投递未托管，保留消息和交付文件。协调者继续跟进，不能让已经交付的执行方无限前台等待。状态包含 deadline_at、timeout 与 exit_code；持续等待时 exit_code=null，正常终止为0/5/6，单轮未送达为2。状态只是最后一轮观察，须同时核对进程；进程消失而无终止记录是异常，不算成功。

## Codex 兼容边界

脚本支持 Claude 的 `❯` 和 Codex 的 `›`，并识别不区分大小写的 `esc to interrupt` / `esc to cancel`。读屏失败、缺少当前输入框时保留未知状态，不当作空闲或已提交。默认 Codex 占位建议词可放行，其余已有文字保守拒发，避免拼接真人输入。

`busy-hook.sh` 是生命周期状态适配器，安装技能不会自动配置 hooks。Claude / Codex 应按各自运行版本配置并验证 hook；不要把配置文件存在当作已生效，也不要把未知状态升格为可靠空闲。看到 hook JSON 错误时单独排障，不自动覆盖用户的 hooks/notify 配置。

当前输入框检测仍是文本启发式，历史输出中的提示符或自定义终端主题可能影响判断；文件信箱加 ack 才能提供更强的确认。Claude 与 Codex 软链接到同一仓库时共享脚本；所有发送方使用同一 CMUX_MSG_DIR 才能共享目标锁。

高级模式按需阅读 [协作模式](references/collaboration-patterns.md)。团队任务优先使用团队工作流中的闭环；进阶模式补充投递与审计细节。`group.sh` 用于组通知，`board.sh` 保存共享决策，`pending.sh` 查询回复台账，`watch.sh` 等待目标状态。广播不能替代按职责分派任务。

原 Claude 详细排障经验保留在 [旧版指南](references/claude-legacy-guide.md)，用于查阅历史行为；路径、并发锁和退出码以本入口及当前脚本为准。


## 外部进程访问

cmux 的 `automation.socketControlMode` 应设为 `automation`，允许同一 macOS 用户的外部进程访问本机 socket。该授权与 Codex 脚本审批规则分别生效。访问失败时 `send.sh --list` 返回非零并保留原始错误，不把失败报告成没有会话。

外部进程没有 cmux caller，不要伪造 `CMUX_SURFACE_ID`。按以下依据核实来源：

- 用户明确说明实际来源，或当前会话的启动上下文、任务交接已绑定来源会话：现查实际标题与 surface，使用该会话名。
- 尚无明确绑定：核对当前仓库的真实路径、注册表中的仓库到会话映射及现场唯一匹配的会话标题。三者一致可作为来源依据；无注册表时可使用已有任务交接中的明确映射。不能只因目录名相似或正在做前端、后端任务就补出一个名称。
- 上述依据仍缺失、冲突或存在多个候选：才设置 `CMUX_FROM='来源未识别（外部进程）'`，在 brief 中写明缺少的来源证据。`focused` 或同名 tab 存在不能单独证明来源。

来源已核实时设置 `CMUX_FROM` 为实际会话名，例如用户明确指出来源是 `aimeter`，就使用 `CMUX_FROM=aimeter`，显示 `(from aimeter)`；不改成 `aimeter-be`，也不因 `caller=null` 改成“来源未识别”。仅在同名会话需要区分时追加已确认的 surface。外部工具进程、来源核实依据及回复路径写进 brief，不强制塞进来源名称。发送前核对已绑定的 endpoint，发现漂移先重新核实归属。

`CMUX_FROM` 是发送方声明的展示标签，不会赋予 caller 或 ACK 身份。需要 `--ask` 时在 brief 中指定回复文件；若有真实的回复接收会话，可设置 `CMUX_REPLY_TO=surface:N`，该编号必须现查。没有真实接收会话时由外部调用方前台读取回复文件；任务监听的 `--notify` 可显式指定已验证的真实接收会话，不使用隐式 watcher 自唤醒。重试旧消息仍沿用原 sender，不因本规则改写历史记录。

修改 socket 模式后执行 `cmux reload-config` 并用外部进程实际测试；若运行实例仍拒绝连接，需从 Automation 设置应用模式，或安排重启 cmux，不能把配置已写入当成已生效。


## 持久信箱与明确确认

优先用持久模式派活；旧的 `--ask` / `--reply` 保留兼容。两种模式不能混用。

```bash
# 沿用上文已定位的 S，不重新指向另一个安装版本。
bash "$S/send.sh" demo-backend --durable '请阅读 /private/tmp/task.md，并按其中要求返回验收结果'
# 输出消息编号；重试沿用编号及原正文：
bash "$S/send.sh" demo-backend --durable --id m原消息编号 '请阅读 /private/tmp/task.md，并按其中要求返回验收结果'

# 收到指定编号的通知后，先只读完整正文与状态；caller=null 也可执行：
bash "$S/send.sh" --status m原消息编号
cmux identify
```

收到“请查 cmux 收件箱：<编号>”通知时，先执行 `--status <编号>` 读取完整正文；正文指向 brief 时继续完整读取该文件，不把门铃当作完整任务。`--status` 不调用 cmux、不修改消息状态，可由发送方或外部工具使用。

- `caller` 是记录中的真实目标且 endpoint 仍匹配：读取后 `--ack`，完成并保存约定证据后 `--complete`。`--inbox` 仅用于列出该真实 caller 的待处理消息。
- `caller=null`：按上述只读方式读取消息，在既定授权范围内处理任务、更新约定的进度与结果文件；不要执行 `--inbox/--ack/--complete`，也不要为消除报错反复重试、取 `focused` 或伪造 `CMUX_SURFACE_ID`。明确记录“协议接收确认不可用”，由协调者直接回读产物；不要求先取得 ACK 才能推进工作。
- `caller` 属于其他会话或 endpoint 已改变：不要确认接收或冒用目标身份，先核实当前任务归属。

读取正文和业务交付不会自动产生 ACK，也不能据此手改 `received/completed`。`--complete` 是接收方显式声明完成，发送方仍需独立验收；该命令不会证明测试已通过。

以下命令仅适用于已核实身份的真实接收方，按接收、工作完成的实际阶段分别执行：

```bash
bash "$S/send.sh" --ack m原消息编号
bash "$S/send.sh" --complete m原消息编号
# 可额外列出自己的待处理消息：
bash "$S/send.sh" --inbox
```

状态区分为 `recorded`（正文原子落盘）、`notified`（终端通知确认提交）、`received`（目标确认接收）、`completed`（目标声明完成）。正文保存在共享台账目录的 `messages/<编号>.json`。通知失败仍保留正文；重试同编号与原正文，不创建新的重复任务。已确认通知、接收或完成时不再发送通知。通知提交后进程崩溃可能留下 recorded 状态，因此任何重复通知都应按编号处理，不重复执行已接收任务。

接收确认必须来自记录中的目标 surface，且 workspace UUID、tty 与 surface 组成的目标身份仍匹配。cmux 重启或目标改变后，旧编号不能盲目路由到新目标。外部调用方可用 `CMUX_FROM` 标注来源、查询 `--status` 并读取约定的回复文件；不会冒用 focused 会话作为 caller。

会话发现使用 `cmux --json tree --all`，需要 Python 3。当前数据缺失、格式异常或权限不足时直接报错，不回退到空列表。`send.sh --list` 的列格式保留兼容。

忙闲 hook 状态写入 `busy-v2/`，采用锁、原子替换及递增写入序号，绑定 workspace UUID、tty、surface。默认有效期 120 秒，可用 `CMUX_BUSY_TTL` 调整；旧格式、损坏、过期或身份不匹配均视为 unknown，再由调用方读取屏幕。序号表示加锁后的写入顺序，不能证明上游生命周期事件的发生顺序。现有 hooks 必须实际正常运行才能提供可靠状态；同一终端中重启 agent 但未改变 endpoint 的情况仍需生命周期正确清理，不能仅靠 endpoint 识别。

终端输入继续只发送一次正文或通知，补交只重试回车。读屏验证是启发式，持久信箱的接收确认比“输入框文本消失”更强。新命令仍通过已授信的 send.sh 入口调用，无需额外授信 protocol.py。
