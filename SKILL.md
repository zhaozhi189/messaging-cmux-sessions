---
name: messaging-cmux-sessions
description: 在本机 cmux 中发现、联系和协调多个 Codex 或 Claude Code 会话；用于跨项目派活、会话同步、请求回复台账和组看板。用户称 cmux-session 时使用此技能。
---

# cmux 多会话协作

cmux 发送的是终端输入，返回成功不代表任务完成。仅对已确认的 agent 会话发消息；普通 shell 会把消息当作命令执行。

## 定位与使用

以本次加载的 SKILL.md 所在目录为技能根目录，设置 `S=<技能根目录>/scripts`。Codex 用户级安装通常是 `~/.codex/skills/messaging-cmux-sessions`，不要引用 Claude 专属路径。

```bash
S=~/.codex/skills/messaging-cmux-sessions/scripts
bash "$S/send.sh" --list
bash "$S/sessions.sh" --all --status
bash "$S/send.sh" surface:7 '请阅读 /private/tmp/brief.md' --dry-run
bash "$S/send.sh" surface:7 --ask '请阅读 /private/tmp/brief.md'
bash "$S/pending.sh" list
```

发送前现查会话。优先注册表别名或明确的 `surface:N`，标题必须唯一。不得根据旧编号、默认目录标题猜目标。当前调用方通过 `cmux identify` 定位。

注册表默认沿用 `~/.claude/cmux-sessions.conf`，台账沿用 `~/.claude/cmux-messaging`，这是为了与现有 Claude 会话共享数据；可分别通过 `CMUX_SESSIONS_CONF`、`CMUX_MSG_DIR` 覆盖。所有协作会话必须使用同一份注册表和台账。没有注册表也可按 surface 或唯一标题寻址。

## 派活与验收

用户授权联系相关会话后，通过 `send.sh` 发消息。长内容、代码、Markdown 和多行内容写到双方可读的绝对路径，只发送简短指针。brief 写清目标、负责的仓库、允许修改的范围、验收条件和回复对象。

跨项目修改默认派给该项目自己的 cmux 会话。未找到对应会话时询问用户，不直接修改其他仓库。派出后可用 `watch.sh idle <目标>` 看护；它会注入唤醒消息，启动前需要已有协作授权。最终自行查文件、diff、测试结果，不能把空闲或对方回复当完成证明。Git 推送由用户执行。

不要把 cmux 的独立进程会话与当前 Codex 的内部 sub-agent 混淆；本技能通过 cmux socket 工作。

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

## Codex 兼容边界

脚本支持 Claude 的 `❯` 和 Codex 的 `›`，并识别不区分大小写的 `esc to interrupt` / `esc to cancel`。读屏失败、缺少当前输入框时保留未知状态，不当作空闲或已提交。默认 Codex 占位建议词可放行，其余已有文字保守拒发，避免拼接真人输入。

`busy-hook.sh` 是生命周期状态适配器，安装技能不会自动配置 hooks。Claude / Codex 应按各自运行版本配置并验证 hook；不要把配置文件存在当作已生效，也不要把未知状态升格为可靠空闲。看到 hook JSON 错误时单独排障，不自动覆盖用户的 hooks/notify 配置。

当前输入框检测仍是文本启发式，历史输出中的提示符或自定义终端主题可能影响判断；文件信箱加 ack 才能提供更强的确认。Claude 与 Codex 软链接到同一仓库时共享脚本；所有发送方使用同一 CMUX_MSG_DIR 才能共享目标锁。

高级模式按需阅读 [协作模式](references/collaboration-patterns.md)。其中旧 Claude 路径例子应换成实际技能目录。`group.sh` 用于组通知，`board.sh` 保存共享决策，`pending.sh` 查询回复台账，`watch.sh` 等待目标状态。广播不能替代按职责分派任务。

原 Claude 详细排障经验保留在 [旧版指南](references/claude-legacy-guide.md)，用于查阅历史行为；路径、并发锁和退出码以本入口及当前脚本为准。


## 外部进程访问

cmux 的 `automation.socketControlMode` 应设为 `automation`，允许同一 macOS 用户的外部进程访问本机 socket。该授权与 Codex 脚本审批规则分别生效。访问失败时 `send.sh --list` 返回非零并保留原始错误，不把失败报告成没有会话。

外部进程没有 cmux 来源 surface，不要伪造 `CMUX_SURFACE_ID`。普通发送可设置 `CMUX_FROM=codex-external` 标明来源；需要 `--ask` 时同样设置来源名，并在 brief 中指定回复文件。若有真实的回复接收会话，可设置 `CMUX_REPLY_TO=surface:N`；该编号必须现查。没有真实接收会话时由外部调用方读取回复文件，不使用 watcher 自唤醒。

修改 socket 模式后执行 `cmux reload-config` 并用外部进程实际测试；若运行实例仍拒绝连接，需从 Automation 设置应用模式，或安排重启 cmux，不能把配置已写入当成已生效。


## 持久信箱与明确确认

优先用持久模式派活；旧的 `--ask` / `--reply` 保留兼容。两种模式不能混用。

```bash
S=~/.codex/skills/messaging-cmux-sessions/scripts
bash "$S/send.sh" demo-backend --durable '请阅读 /private/tmp/task.md，并按其中要求返回验收结果'
# 输出消息编号；重试沿用编号及原正文：
bash "$S/send.sh" demo-backend --durable --id m原消息编号 '请阅读 /private/tmp/task.md，并按其中要求返回验收结果'

# 接收方在自己的真实 cmux 会话中执行：
bash "$S/send.sh" --inbox
bash "$S/send.sh" --ack m原消息编号
# 工作完成且验收证据已按 brief 约定保存后：
bash "$S/send.sh" --complete m原消息编号

# 发送方查询状态：
bash "$S/send.sh" --status m原消息编号
```

收到“请查 cmux 收件箱”通知时，先执行 `--inbox` 读取对应编号的完整正文，再 `--ack` 确认接收。不要把通知文本当作完整任务。`--complete` 是接收方显式声明完成，发送方仍需独立验收；该命令不会证明测试已通过。

状态区分为 `recorded`（正文原子落盘）、`notified`（终端通知确认提交）、`received`（目标确认接收）、`completed`（目标声明完成）。正文保存在共享台账目录的 `messages/<编号>.json`。通知失败仍保留正文；重试同编号与原正文，不创建新的重复任务。已确认通知、接收或完成时不再发送通知。通知提交后进程崩溃可能留下 recorded 状态，因此任何重复通知都应按编号处理，不重复执行已接收任务。

接收确认必须来自记录中的目标 surface，且 workspace UUID、tty 与 surface 组成的目标身份仍匹配。cmux 重启或目标改变后，旧编号不能盲目路由到新目标。外部调用方可用 `CMUX_FROM` 标注来源、查询 `--status` 并读取约定的回复文件；不会冒用 focused 会话作为 caller。

会话发现使用 `cmux --json tree --all`，需要 Python 3。当前数据缺失、格式异常或权限不足时直接报错，不回退到空列表。`send.sh --list` 的列格式保留兼容。

忙闲 hook 状态写入 `busy-v2/`，采用锁、原子替换及递增写入序号，绑定 workspace UUID、tty、surface。默认有效期 120 秒，可用 `CMUX_BUSY_TTL` 调整；旧格式、损坏、过期或身份不匹配均视为 unknown，再由调用方读取屏幕。序号表示加锁后的写入顺序，不能证明上游生命周期事件的发生顺序。现有 hooks 必须实际正常运行才能提供可靠状态；同一终端中重启 agent 但未改变 endpoint 的情况仍需生命周期正确清理，不能仅靠 endpoint 识别。

终端输入继续只发送一次正文或通知，补交只重试回车。读屏验证是启发式，持久信箱的接收确认比“输入框文本消失”更强。新命令仍通过已授信的 send.sh 入口调用，无需额外授信 protocol.py。
