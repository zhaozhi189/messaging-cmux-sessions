# 团队任务工作流

多项目会话共同负责一个结果。协调者维护目标、依赖与验收；项目负责人只修改自己的仓库；契约生产方负责字段及行为口径；消费方和评审方拿实际文件验证。一个会话可以承担多个职责，不必为每项工作新增 agent。

## 1. 派单前建立共同上下文

现查会话和仓库，读屏确认目标是 agent。命令中的 window/surface 必须使用当前发现结果：

```bash
bash "$S/send.sh" --list
cmux read-screen --window "$TARGET_WINDOW" --surface "$TARGET" --scrollback --lines 80
```

没有目标项目会话时按入口规则询问用户，不把普通 shell 当 agent。用 `board.sh <组> --init` 建共享看板，已有看板直接读取。只发送需要处理的成员，更新看板例如：

```bash
bash "$S/board.sh" "$GROUP" --set-status "$TARGET" "$TASK_ID" running
bash "$S/board.sh" "$GROUP" --ask '具体契约分歧及证据路径' --owner "$CONTRACT_OWNER"
bash "$S/board.sh" "$GROUP" --decide "$DECISION_ID" '已讨论的技术口径' '证据版本、理由和适用范围'
```

任务 brief 放共享绝对路径，使用以下骨架：

```text
共同目标：最终用户需要什么结果。
task_id：本轮唯一编号；消息编号由 send.sh 管理，二者不同。
负责人：目标会话、仓库、允许修改的文件或模块；不覆盖他人改动。
依赖：先决产物；无依赖部分可继续；依赖验收后再推进消费方。
契约职责：谁提供口径，谁回读，谁记录技术建议，哪些需用户决定。
基线：当下 HEAD、已有改动、参考需求和原型的绝对路径。
交付：产物绝对路径、验证结果、未决项、变更版本或提交哈希。
进度：progress.json 路径、目标 endpoint、派单时间戳、状态更新约定。
验收：具体行为与边界、必须对齐的接口、风险相称的检查。
回复：结果文件和真实协调接收会话；外部 caller=null 不伪造身份。
执行约定：范围内连续推进；阻塞马上写明原因、需要谁处理。
```

共享看板状态建议使用 `running → completion_reported → reviewing → accepted`，有阻塞记 `blocked` 和责任人。这些是任务状态，不替换持久信箱的 `recorded/notified/received/completed` 传输状态。ACK 只表示接收，`--complete` 只表示自报完成，验收由协调者负责。

目标 busy 或停在权限框时，先把 brief 和待通知项落盘，等安全可接收时才发送门铃；当前 send.sh 没有文档化的仅入队不通知模式，不要为了入队向权限框发送文字。用持久模式发送短指针，保存返回的消息编号；失败重试同编号及原正文。收到指定编号后先用 `--status <编号>` 只读完整正文，再读取其中的 brief。外部调用方 `caller=null` 时不执行 `--inbox/--ack/--complete`，在既定授权范围内继续处理并更新约定的进度与结果文件，由协调者直接回读，不冒用接收身份或手改消息状态。

### 独立目标并行派单

不同目标且没有依赖的工作可以同时发送；同一目标串行，有依赖时先验收前置产物。各目标各自保存消息编号、退出码、结果文件与监听状态，并逐个独立验收。锁冲突保留原编号重试，不能丢弃失败或无差别广播。并行只是派单方式，团队仍须围绕共同目标讨论证据、互相回读契约并总结可复用的结论。

下面两个目标必须已经现查、读屏确认可安全输入，brief 已写好。先生成各自编号，发送失败仍保留编号与原正文；不要使用 busy Tab 入队或向权限框输入：

```bash
BACKEND_ID="m$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
FRONTEND_ID="m$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
printf '%s\n' "$BACKEND_ID" > /private/tmp/team-task/backend-message-id
printf '%s\n' "$FRONTEND_ID" > /private/tmp/team-task/frontend-message-id
bash "$S/send.sh" "$BACKEND_TARGET" --durable --id "$BACKEND_ID" \
  '请阅读 /private/tmp/team-task/backend-brief.md' > /private/tmp/team-task/backend-send.log 2>&1 &
BACKEND_PID=$!
bash "$S/send.sh" "$FRONTEND_TARGET" --durable --id "$FRONTEND_ID" \
  '请阅读 /private/tmp/team-task/frontend-brief.md' > /private/tmp/team-task/frontend-send.log 2>&1 &
FRONTEND_PID=$!
# 即使调用方启用 set -e，也收集两个退出码。
if wait "$BACKEND_PID"; then BACKEND_RC=0; else BACKEND_RC=$?; fi
if wait "$FRONTEND_PID"; then FRONTEND_RC=0; else FRONTEND_RC=$?; fi
printf '%s\n' "$BACKEND_RC" > /private/tmp/team-task/backend-send-exit
printf '%s\n' "$FRONTEND_RC" > /private/tmp/team-task/frontend-send-exit
```

逐项读取日志及原编号的 `--status`；2/3 立即按入口的忙时投递规则挂原编号的 delivery-wait.py，4 的锁冲突核实后沿原编号重试，不丢失败；每个成功派出的任务按下节挂独立监听，不能用一个 wait 或一个总状态代替。投递退出码 0 也不代表验收通过。

下面示例在首次持久发送遇到 2/3 时立即进入前台投递等待。台账必须已有该编号，状态文件不能覆盖信箱记录。投递成功后再挂任务监听，投递到期与任务完成分别处理。

```bash
SEND_RC=0
bash "$S/send.sh" "$TARGET" --durable --id "$MESSAGE_ID" \
  '请阅读 /private/tmp/team-task/brief.md' || SEND_RC=$?
case "$SEND_RC" in
  0) : ;;
  2|3)
    python3 "$S/delivery-wait.py" --id "$MESSAGE_ID" \
      --state /private/tmp/team-task/delivery-watch.json --interval 30 --timeout 1800
    ;;
  *) printf '投递失败，退出码 %s，请核查原编号与日志\n' "$SEND_RC" ;;
esac
```

等待脚本重试时通过 send.sh 的 `--retry` 在目标发送锁内再次验证原记录、endpoint、权限和完整输入。该选项只允许与已有 `--durable --id` 使用；协调者通常直接调用等待脚本，它从信箱读取原 sender/body。`--source-label` 仅可首次指定可信展示标签，恢复时不得改变。原门铃补交成功后才记录 notified；陌生文本及无法辨认的折行内容不会被提交。到期退出 5，核实归属后用原参数 `--resume` 续挂；身份漂移退出 6，停止旧路由并组织核实。

## 2. 交付文件与持续监听

`S` 是本次加载的技能根目录下的 scripts。派单前核实两个真实 surface，取得目标 endpoint 和 Unix 派单时间，写入 brief。endpoint 格式是 `workspace UUID|tty|surface`，可从现查的 `cmux --json tree --all` 取得，或调用已有 Python 函数：

```bash
TARGET_ENDPOINT=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); import protocol; print(protocol.endpoint(sys.argv[2]))' "$S" "$TARGET")
DISPATCH_TS=$(date +%s)
```

把取得值写入 brief。不要从旧标题猜编号。

执行方写进度时先写同目录临时文件，再用原子替换。状态契约：

```json
{
  "task_id": "demo-backend-task",
  "target_endpoint": "当前 workspace UUID|当前 tty|surface:N",
  "status": "running",
  "updated_at": 1791600000,
  "artifacts": [],
  "summary": "正在核对接口约束"
}
```

`updated_at` 是 Unix 秒；`status` 仅为 `running/blocked/completed`。在接收、实质进展、阻塞和交付时更新。`completed` 必须附存在的绝对路径产物；仅提交号、屏幕文字、文件 mtime 或 HEAD 移动不足以证明任务完成。`blocked` 写明需要谁处理及原因。业务未决项保留在产物里，不伪装已经批准。

需要交付的每个子任务默认一个监听。以下变量均来自本轮已核实的 brief；路径避免复用昨天任务的文件：

```bash
# 先前台单次观察，检查参数、endpoint 和状态输出。
python3 "$S/task-watch.py" --task-id "$TASK_ID" --target "$TARGET" \
  --notify "$RECEIVER" --since "$DISPATCH_TS" \
  --result "$RESULT_FILE" --state "$WATCH_STATE" --once

# 先读取状态，确认 target_endpoint 与派单 brief 一致，notify_endpoint 与已核实接收方一致。
# --once 不能托管任务；未完成时沿用同一状态文件启动持续监听。
nohup python3 "$S/task-watch.py" --task-id "$TASK_ID" --target "$TARGET" \
  --notify "$RECEIVER" --since "$DISPATCH_TS" \
  --result "$RESULT_FILE" --state "$WATCH_STATE" \
  --interval 30 --stale-after 300 --timeout 7200 > "$WATCH_LOG" 2>&1 &
WATCH_PID=$!
printf '%s\n' "$WATCH_PID" > "$WATCH_PID_FILE"
kill -0 "$WATCH_PID"
```

等到首轮状态落盘后读取 `$WATCH_STATE` 和日志，确认本轮任务、目标 endpoint、通知 endpoint 与 PID。启动 PID 存在仍不足以证明首次观察成功。再核对 `events` 中首次事件的 `id/delivered/attempts` 以及 `notify_error`：

```bash
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("notify_endpoint")); print(d.get("notify_error")); print([(e["id"], e["delivered"], e["attempts"]) for e in d["events"]])' "$WATCH_STATE"
bash "$S/send.sh" --status "$FIRST_EVENT_ID"
```

事件 `delivered=true` 及信箱 `notified/received/completed` 证明通知已确认提交，仍不能证明接收方已验收任务。首次通知失败且尚未重试成功时不声称唤醒路由已经验证；协调者仍按登记任务直接回读进度和产物，执行方按下面的后台移交规则处理通知。协调会话收到门铃后必须读取自己的信箱与对应状态文件，按 task_id 恢复任务，不把门铃视作最终结论。普通 running、每轮检查及恢复 running 只写状态，不发通知。重要事件 completion_reported、blocked、approval_required、stalled、idle_unfinished、session_changed、timed_out、unknown、delivery_invalid、blocked_cleared 才通过 `send.sh --durable` 发短状态指针；对相同事件去重，失败保留队列并按原编号重试。相同状态文件持有互斥锁，避免重复监听。

新通知前缀显示“监听器 / 被监控对象”，来源标签与目标标签随事件落盘；项目主动消息使用实际来源会话的标题，有真实 caller 时默认由 send.sh 自动生成，不按任务角色拼接 `-be` / `-fe`。外部进程按 SKILL.md 的来源核实规则使用实际会话名，例如已明确来源 `aimeter` 就显示 `(from aimeter)`；外部工具进程信息写入 brief。`caller=null` 不直接导致来源未知，仅在来源依据缺失或歧义无法消除时声明“来源未识别（外部进程）”。监听器通过 CMUX_FROM_LABEL 提供展示标签，CMUX_FROM 仍保存原发送身份；项目主动通知不设置该展示变量。来源标签只是展示，不赋予 ACK 身份。旧事件沿用原 sender 显示来源，包括 codex-external；不改原信箱编号、正文或 sender。普通事件含旧 pending running 标记 suppressed，delivered 仍为 false，不再重试也不拖住终止宽限。通知事件在落盘时固定 sender；重启后即使 `CMUX_FROM` 改变，同一事件仍沿用原 sender、编号和正文。旧事件缺少 sender 时从原信箱消息记录恢复，不改写已落盘消息的身份。

旧告警失效要有明确依据：后续 stalled_cleared 可 supersede 之前未送达的 stalled；后续 blocked 内容变化、blocked_cleared 或 completion_reported 可取代之前未送达的 blocked。事件保留原 id/body/sender，写 suppressed=true、superseded=true、superseded_by及 suppressed_reason，delivered仍为false；原信箱文件不改写，输入框中的旧门铃不清空。已 delivered 的历史不修改；权限、身份变化、交付与到期通知不因普通恢复被抑制。尚未恢复的新一轮 stalled 仍保留。

明确 blocked 时继续每30秒观察，同一阻塞不额外触发 stalled。仅updated_at变化视为心跳，不增加 blocked 通知；实质内容变化产生新 blocked 提醒；有效结构化结果由blocked转running产生一次 blocked_cleared 提醒，完成仍走completion_reported独立验收。旧状态没有 blocked_signature 时以已有summary/artifacts比较并建立基线，同内容不重复告警。

迁移由监听所有者核实PID和完整命令后停旧进程；原task_id/target/notify/since/result/state不变，用新代码沿原参数启动。原状态兼容，无需删除事件或重建消息。活动状态首轮重新观察后迁移已明确blocked造成的旧stalled，已有恢复事件也会在读入和通知前抑制旧告警；终止状态只重试仍有效的通知，不重启目标观察。核对新PID、首轮status、告警抑制依据、日志及路由。已过观察期限须按原续挂规则核实并采用新的观察状态，不能用迁移绕过终止状态；保留旧通知对账记录。

没有真实协调接收会话时省略 `--notify`，前台分段运行并读取状态，单次等待不超过 60 秒；不要在回合结束后声称会自动回来。报告完成、身份变化和到期时停止目标观察，仍按固定通知身份重试待发送事件，默认宽限 `--notify-grace 300` 秒；宽限期结束仍未送达时落盘 `delivery_error` 并以 5 退出。无通知路由时仅落盘。此限制必须如实报告，可沿用原参数 `--once` 对账重试，不能声称一定送达。通知接收会话处于忙碌、权限等待或未知状态时保留事件，不向其输入文字，后续沿用原编号重试。通知只是门铃：协调者醒来读状态文件、进度文件和看板，决定下一步。监听脚本不会自主批准、验收或扩大范围。

无进展提醒保存在状态的 `stalled` 布尔字段及同名事件中，恢复后记录恢复事件；它不是强制停工状态。权限框优先报告 `approval_required`，不向目标按键。告诉用户哪个会话、哪项动作、工具给出的理由；继续监听并推进其它独立工作。未知屏幕不当空闲。两轮可靠空闲且无交付时报告 `idle_unfinished`，核实后用原任务短指针提醒继续。无进展告警是检查线索，不强制中断工作。

Codex 可在工作 banner 下方显示可编辑的占位提示，不能据此判空闲；当前工作 banner 优先，明确的权限选择框优先于工作状态。已出现完成 banner 的旧输出不再作为当前权限或工作状态。这些判断仍是文本启发式，不能替代当次进度与独立验收。

若目标和协调接收会话一起重启并改变 endpoint，旧身份通知不能安全投递到新会话；此时无法保证自动唤醒，必须读取 delivery_error/log 后重新核实两端并恢复监听，不承诺无人值守恢复。目标身份改变时旧监听停止，重新现查、读屏、确认当前任务并取得新 endpoint；新绑定使用新的状态文件，要求执行方更新进度归属。时间戳、task_id 或 endpoint 不匹配的旧结果不算交付。监听到期能够续挂时必须续挂，不标记任务完成；环境确实不可用时明确报告阻塞及恢复条件，不能把报告限制当成托管成功。终止状态文件只用于通知对账，不自动恢复目标观察。任务到期后续挂，先核对原监听已结束及当前 endpoint 未漂移，再使用新的状态路径和监听期限，保留原 task_id、派单时间及未决记录；不要靠反复重启已终止的状态文件续挂。用户取消或暂停时根据已核实 PID 停止进程并更新看板；不会自动读取用户聊天来停止。

### 交付后移交通知观察

交付、传输和验收分开记录。执行方先原子写入本轮 completed 进度与绝对路径产物，再发送短交付指针。协调者在派单时保存 task_id、since、执行方 endpoint、进度、产物和监听路径；每次恢复协调、收到新消息或进入下一轮观察时，先按这些路径回读所有未验收任务。仅核对同 task_id、endpoint、派单之后的时间和实际文件，不能采信昨天的完成文件。不以终端门铃是否提交作为读产物或开始验收的前提。

交付通知返回2/3时，执行方不要一直前台等协调者输入框。已有后台任务监听覆盖这次完成事件时，核实其PID、状态和期限后使用该监听，不再重复发送完成通知。主动交付消息已经落盘且未被监听覆盖时，用原编号启动下面的后台等待；不得与已有同编号等待进程重复启动。所有变量来自本轮brief与发送记录，协调者可以从交付文件或看板读到这些路径。

```bash
# 使用新状态路径；如恢复原状态，先核对旧PID、完整命令、任务和endpoint。
nohup python3 "$S/delivery-wait.py" --id "$MESSAGE_ID" \
  --state "$DELIVERY_STATE" --interval 30 --timeout 1800 \
  > "$DELIVERY_LOG" 2>&1 < /dev/null &
DELIVERY_PID=$!
printf '%s\n' "$DELIVERY_PID" > "$DELIVERY_PID_FILE"
kill -0 "$DELIVERY_PID"
```

读取首轮状态和日志，核对message_id、target、endpoint、sender、body_hash与原消息，PID必须匹配本次进程；deadline_at必须是本轮期限。进程若已正常退出则核对exit_code和原信箱状态，不要求已退出进程仍存活。只有PID存在而状态未落盘、锁冲突退出4、路径/身份校验失败或异常退出都不能宣称托管成功。将原消息编号、状态/日志/PID文件、期限写入交付产物的“通知观察”段：交付已完成、传输状态、协调观察负责人、未决项。执行方随后结束当前执行回合、恢复接收后续派单，不把业务进度改回blocked来等通知。确实无法后台启动时保留结果和未投递记录，明确缺失条件；协调者承担回读与重试责任，不转给用户。

协调者每轮核对后台PID及状态；exit_code=5是到期未送达，6是路由漂移，异常退出需要日志诊断，均不等于验收或收到通知。未决且仍有效的原任务可核实后沿原编号显式resume，不能盲目无限续挂。业务已独立验收或被后续任务取代时，由等待进程所有者核对PID完整命令后用SIGINT停止不再必要的观察并确认退出（delivery-wait会清理正在发送的子进程组），另存取消理由、原消息编号、task_id及验收/取代证据；不改原信箱state或delivered，不把业务accepted/review文件当transport ACK。停止等待不会清掉终端里已有文字。

若协调输入框被旧门铃占住，新的消息等待foreign_input是安全行为。协调者先按既有任务路径读产物并推进验收、派单；旧门铃只有记录目标的真实caller且endpoint仍匹配时才能读取完整正文后显式ack。外部进程只可status和读文件，不能冒caller或借accepted伪造received/notified。只有该旧编号自己的安全重试能在空闲且精确匹配完整门铃时补交；其它发送方不能清空、补交或按Tab。折行、陌生草稿、权限框、忙碌和身份漂移继续拒绝输入。无需建立新的receipt协议。

## 3. 有证据地共同讨论

发现契约矛盾、边界遗漏或实现风险时，只联系本任务相关成员。长问题写文件，短消息带 task_id 和问题编号。讨论记录最少包含：问题、实际文件与版本、影响、建议和负责回应的人。对方必须核对自己的代码或 spec，可以拿证据反驳协调者。

例如前后端 `schemaRevision` 一边数字、一边字符串：生产方先给出当前序列化和校验事实，消费方核对页面状态与请求；双方讨论兼容性并修订各自产物。协调者检查旧接口调用是否也更新，不能用“两边单测过了”代替契约验收。

`board.sh --ask` 记录待决及 owner；`--decide` 的理由写清证据来源、日期或版本、适用范围。契约改变后通知消费方回读，消费方更新自己的 spec 并说明验证结果。技术一致可以形成推荐方案；涉及用户业务选择、范围扩展或真实权限时由用户决定。团队意见不能替代该决定。

不要让多个会话反复向用户问同一个问题。协调者汇总一份有证据、明确影响和建议的问题，指定决定人；其它会话继续不依赖该答案的工作。已授权范围内的接口、表结构调整按职责推进并同步相关方，不因“改结构”一律停工。

## 4. 独立验收与整改

收到 `completion_reported` 后将看板置为 `reviewing`。读取实际产物、当前 diff/提交以及验证记录，核对需求、原型、契约和待决项。按风险亲自执行必要检查：文档交付可检查需求编号、示例合法性和字段一致性；实现改动运行相应测试或构建。不要无条件重跑全量构建，也不要在同一工作目录并行跑 Maven。

问题写进整改文件并派给原仓库负责人，沿用原工作项关联，更新验收条件；整改轮次关联原工作项，但采用新的监控 task_id、派单时间和状态文件，要求执行方写入该轮进度；这样不会再次接受上一轮 completed。重新监听新的交付轮次。通过后看板标为 `accepted`，清理已经结束的监听，向用户汇报实际结果、验证和仍待决定的事项。仅提交后告知用户可以自行推送，本技能不执行 push。

协调者的完成标准是共同目标已经验收。各方分别说 done、HEAD 变动、看板写 completed、监听退出，都不能单独满足这个标准。
