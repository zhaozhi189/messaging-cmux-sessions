#!/usr/bin/env python3
"""每 30 秒监听协作任务；只报告权限阻塞，交付报告仍须协调者独立验收。

前台运行，可用 nohup 托管。--once 仅检查一轮。状态文件原子写入，
同一路径只允许一个监听进程；失败通知保留编号及正文，下一轮有限重试。
"""
import argparse
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

import protocol


PROGRESS_SIGNATURE_VERSION = 2


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def positive(value):
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError('必须是大于零的有限秒数')
    return number


def arguments():
    parser = argparse.ArgumentParser(description='按任务监听 cmux；权限确认只报告，完成报告不代表验收通过。')
    parser.add_argument('--task-id', required=True, help='与结构化结果一致的任务编号')
    parser.add_argument('--target', required=True, help='已确认的目标 surface:N')
    parser.add_argument('--result', required=True, type=Path, help='结构化进度 JSON 的绝对路径')
    parser.add_argument('--state', required=True, type=Path, help='监听状态 JSON 的绝对路径')
    parser.add_argument('--notify', help='显式指定接收通知的 surface:N；不使用 focused 身份')
    parser.add_argument('--since', type=float, help='派单 Unix 时间戳；拒绝早于此时间的旧结果')
    parser.add_argument('--interval', type=positive, default=30, help='检查间隔秒数，默认 30')
    parser.add_argument('--stale-after', type=positive, default=300, help='无进展告警秒数，默认 300')
    parser.add_argument('--timeout', type=positive, default=7200, help='监听到期秒数，默认 7200；到期不代表完成')
    parser.add_argument('--notify-grace', type=positive, default=300, help='停止观察后通知重试宽限期秒数，默认 300；到期失败退出码 5')
    parser.add_argument('--once', action='store_true', help='只检查一次，不 sleep')
    args = parser.parse_args()
    for path in (args.result, args.state):
        if not path.is_absolute():
            parser.error('--result 和 --state 必须是绝对路径')
    if args.result == args.state:
        parser.error('结果文件和监听状态文件必须使用不同路径')
    for ref in (args.target, args.notify):
        if ref is not None and not re.fullmatch(r'surface:\d+', ref):
            parser.error('目标和通知接收方必须显式使用 surface:N')
    if args.since is not None and (not math.isfinite(args.since) or args.since < 0):
        parser.error('--since 必须是非负的有限 Unix 时间戳')
    return args


def endpoint(rows, ref):
    matches = [row for row in rows if row['ref'] == ref]
    if len(matches) != 1:
        raise ValueError('目标会话不存在或不唯一：' + ref)
    row = matches[0]
    if not row.get('workspace_id') or not row.get('tty'):
        raise ValueError('会话缺少 workspace UUID 或 tty，不能绑定身份')
    return '|'.join((row['workspace_id'], row['tty'], ref)), row


def discover():
    completed = subprocess.run([protocol.CMUX, '--json', 'tree', '--all'],
                               capture_output=True, text=True, timeout=10)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or 'cmux 发现失败')
    data = json.loads(completed.stdout)
    return [dict(surface, window=window['ref'], workspace_id=workspace['id'])
            for window in data['windows'] for workspace in window['workspaces']
            for pane in workspace['panes'] for surface in pane['surfaces']
            if surface.get('type') == 'terminal']


def load_state(args, now):
    if args.state.exists():
        state = json.loads(args.state.read_text())
        expected = (args.task_id, args.target, str(args.result))
        if (state.get('task_id'), state.get('target'), state.get('result_path')) != expected:
            raise ValueError('状态路径已绑定其它任务，请使用新路径')
        if args.since is not None and state['since'] != args.since:
            raise ValueError('恢复监听时 --since 必须与原派单时间一致')
        if state.get('notify') and args.notify != state['notify']:
            raise ValueError('恢复监听不得更换通知接收会话')
        return state
    return dict(task_id=args.task_id, target=args.target, result_path=str(args.result),
                started_at=now, since=args.since if args.since is not None else now,
                last_progress_at=now, idle_streak=0, events=[], status=None,
                stalled=False, stopped=False)


def read_result(args, state):
    try:
        result = json.loads(args.result.read_text())
        stamp = result.get('updated_at')
        if (result.get('task_id') != args.task_id or result.get('target_endpoint') != state.get('target_endpoint')
                or result.get('status') not in ('running', 'blocked', 'completed')
                or isinstance(stamp, bool) or not isinstance(stamp, (int, float))
                or not math.isfinite(stamp) or stamp < state['since']
                or stamp > time.time() + 5
                or stamp < state.get('result_updated_at', state['since'])):
            return None, '结果任务、会话身份或时间戳不匹配，已忽略'
        return result, ''
    except FileNotFoundError:
        return None, '尚未收到结构化进度'
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        return None, '结构化进度不可读：' + str(exc)


def screen_state(row):
    completed = subprocess.run([protocol.CMUX, 'read-screen', '--window', row['window'],
                                '--surface', row['ref'], '--scrollback', '--lines', '30'],
                               capture_output=True, text=True, timeout=10)
    if completed.returncode:
        return 'unknown'
    return classify_screen(completed.stdout)


def classify_screen(output):
    screen = '\n'.join(output.splitlines()[-30:])
    finished = list(re.finditer(r'^\s*(?:[•✶✻✽]\s+)?(?:Worked|Brewed|Cogitated)\s+for\b',
                                screen, re.MULTILINE | re.IGNORECASE))
    if finished:
        screen = screen[finished[-1].end():]
    approval = r'do you want to (?:proceed|allow|make this edit)|would you like to (?:proceed|allow|run the following command)|approval required|permission required|requires approval|allow (?:this|once)|yes,?\s*(?:proceed|allow)|是否(?:允许|批准|继续)|需要(?:批准|授权)|允许此次'
    busy = r'esc to interrupt|esc to cancel|…\s*\('
    ready = list(re.finditer(r'^\s*[❯›]\s+(?:Ask Codex to do anything|How can I help)', screen, re.MULTILINE | re.IGNORECASE))
    active = list(re.finditer(approval + '|' + busy, screen, re.IGNORECASE))
    has_yes = re.search(r'^\s*(?:[❯›]\s*)?\d+[.)]\s+Yes\b', screen, re.MULTILINE | re.IGNORECASE)
    has_no = re.search(r'^\s*(?:[❯›]\s*)?\d+[.)]\s+No\b', screen, re.MULTILINE | re.IGNORECASE)
    if has_yes and has_no and re.search(approval, screen, re.IGNORECASE):
        return 'approval_required'
    live_busy = re.search(r'^\s*[•✶✻✽]\s+.+\([^\n]*esc to (?:interrupt|cancel)',
                          screen, re.MULTILINE | re.IGNORECASE)
    if live_busy:
        return 'busy'
    if ready and (not active or ready[-1].start() > active[-1].start()):
        return 'idle'
    if re.search(approval, screen, re.IGNORECASE):
        return 'approval_required'
    if re.search(busy, screen, re.IGNORECASE):
        return 'busy'
    if re.search(r'^\s*[❯›]\s+', screen, re.MULTILINE):
        return 'idle'
    return 'unknown'


def delivery_valid(result):
    artifacts = result.get('artifacts')
    return (isinstance(artifacts, list) and bool(artifacts)
            and all(isinstance(path, str) and Path(path).is_absolute() and Path(path).exists()
                    for path in artifacts))


IMPORTANT_EVENTS = ('approval_required', 'blocked', 'session_changed', 'timed_out',
                    'completion_reported', 'stalled', 'idle_unfinished', 'unknown', 'delivery_invalid', 'blocked_cleared')


def suppress_quiet_events(state):
    for event in state['events']:
        if not event['delivered'] and event['type'] not in IMPORTANT_EVENTS:
            event['suppressed'] = True
            event.setdefault('suppressed_reason', '普通进度静默，不代表送达')
    supersede_resolved_events(state)


def supersede_resolved_events(state):
    events = state['events']
    for kind, resolutions in (('stalled', ('stalled_cleared',)),
                              ('blocked', ('blocked', 'blocked_cleared', 'completion_reported'))):
        resolved = [(index, event) for index, event in enumerate(events) if event['type'] in resolutions]
        if not resolved:
            continue
        cutoff, evidence = resolved[-1]
        for event in events[:cutoff]:
            if event['type'] == kind and not event['delivered'] and not event.get('superseded'):
                event.update(suppressed=True, superseded=True, superseded_by=evidence['id'],
                             suppressed_reason=kind + ' 已被后续 ' + evidence['type'] + ' 取代；历史和信箱身份保留')


def pending_events(state):
    return [event for event in state['events'] if not event['delivered'] and not event.get('suppressed')]


def add_event(state, kind, args, now):
    identifier = 'm' + uuid.uuid4().hex
    if 'notify_sender' not in state or state.get('source_error'):
        try:
            state['notify_sender'] = protocol.sender()
            state.pop('source_error', None)
        except protocol.CommandError:
            raise
        except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
            state.update(notify_sender=protocol.UNKNOWN_SOURCE, source_error=str(exc))
    sender = state['notify_sender']
    state['events'].append(dict(id=identifier, type=kind, created_at=now, delivered=False, attempts=0,
                               sender=sender, target_label=state.get('target_label', args.target),
                               source_label=protocol.MONITOR_PREFIX + state.get('target_label', args.target),
                               suppressed=kind not in IMPORTANT_EVENTS,
                               body=f'[task-watch] {args.task_id} {kind}；请检查 {args.state}'))


def notify_once(args, state, rows):
    suppress_quiet_events(state)
    if not args.notify or not pending_events(state):
        return
    try:
        key, receiver = endpoint(rows, args.notify)
        if key != state.get('notify_endpoint'):
            state['notify_error'] = '通知接收会话身份已改变；未向新会话发送'
            return
        observed = screen_state(receiver)
        if observed != 'idle':
            state['notify_error'] = '通知接收会话暂不可安全输入：' + observed + '；保留原事件稍后重试'
            return
    except (ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        state['notify_error'] = str(exc)
        return
    pending = None
    important = IMPORTANT_EVENTS
    if state['status'] in important:
        pending = next((event for event in reversed(state['events'])
                        if event['type'] == state['status'] and not event['delivered'] and not event.get('suppressed')), None)
    if pending is None:
        pending = next((event for event in state['events']
                        if event['type'] in important and not event['delivered'] and not event.get('suppressed')), None)
    if pending is None:
        pending = next((event for event in state['events'] if not event['delivered'] and not event.get('suppressed')), None)
    if not pending:
        return
    try:
        if not pending.get('sender'):
            recorded = protocol.message_path(pending['id'])
            sender = json.loads(recorded.read_text())['sender'] if recorded.exists() else state.setdefault(
                'notify_sender', os.environ.get('CMUX_FROM') or 'codex-external')
            if not isinstance(sender, str) or not sender:
                raise ValueError('原消息 sender 不合法，不能更换消息身份重试')
            pending['sender'] = sender
    except (OSError, ValueError, KeyError, TypeError) as exc:
        state['notify_error'] = '原通知来源不可恢复：' + str(exc)
        return
    pending['attempts'] += 1
    protocol.atomic(args.state, state)
    env = dict(os.environ, SEND_SELF_OK='1', CMUX_FROM=pending['sender'],
               CMUX_FROM_LABEL=pending.get('source_label') or pending['sender'])
    try:
        command = ['bash', str(Path(__file__).with_name('send.sh')), args.notify,
                   '--durable', '--id', pending['id']]
        if protocol.message_path(pending['id']).exists():
            command.append('--retry')
        completed = subprocess.run(command + [pending['body']],
                                   env=env, capture_output=True, text=True, timeout=20)
        pending['delivered'] = completed.returncode == 0
        state['notify_error'] = '' if pending['delivered'] else '通知未确认送达，事件保留并在下一轮重试'
    except (OSError, subprocess.TimeoutExpired) as exc:
        state['notify_error'] = '通知失败，事件保留：' + str(exc)


def tick(args, state):
    now = time.time()
    rows = []
    status = 'unknown'
    reason = ''
    result = None
    previous = state['status']
    previous_stalled = state['stalled']
    previous_blocked = state.get('blocked_active', previous == 'blocked')
    previous_blocked_signature = state.get('blocked_signature')
    previous_blocked_content = (state.get('summary', ''), state.get('artifacts', []))
    blocked_changed = False
    blocked_cleared = False
    state.update(checked_at=now, stopped=False)
    try:
        rows = discover()
        try:
            key, row = endpoint(rows, args.target)
        except ValueError:
            if state.get('target_endpoint'):
                status = 'session_changed'
                state['stopped'] = True
                raise
            raise
        if not state.get('target_endpoint'):
            state['target_endpoint'] = key
            state['target_label'] = row.get('title') or args.target
        elif key != state['target_endpoint']:
            status = 'session_changed'
            state['stopped'] = True
            raise ValueError('目标的 workspace UUID、tty 或 surface 已改变')
        if args.notify and not state.get('notify_endpoint'):
            state['notify_endpoint'], _ = endpoint(rows, args.notify)
            state['notify'] = args.notify
        result, reason = read_result(args, state)
        if result is not None:
            content = {key: value for key, value in result.items() if key != 'updated_at'}
            signature = fingerprint(content)
            previous_signature = state.get('progress_signature')
            if previous_signature and state.get('progress_signature_version') != PROGRESS_SIGNATURE_VERSION:
                previous_result = dict(result, updated_at=state.get('result_updated_at'))
                if fingerprint(previous_result) == previous_signature:
                    previous_signature = signature
            if signature != previous_signature:
                state['last_progress_at'] = now
            state.update(progress_signature=signature, progress_signature_version=PROGRESS_SIGNATURE_VERSION)
            state.update(result_updated_at=result['updated_at'], summary=result.get('summary', ''),
                         artifacts=result.get('artifacts', []))
        if result is not None:
            if result['status'] == 'blocked':
                blocked_changed = (signature != previous_blocked_signature if previous_blocked_signature else
                                   previous_blocked_content != (result.get('summary', ''), result.get('artifacts', [])))
                state.update(blocked_active=True, blocked_signature=signature)
            else:
                blocked_cleared = previous_blocked and result['status'] == 'running'
                state['blocked_active'] = False
        observed = screen_state(row)
        state['screen_state'] = observed
        state['idle_streak'] = state['idle_streak'] + 1 if observed == 'idle' else 0
        if observed == 'approval_required':
            status = observed
        elif result and result['status'] == 'completed':
            status = 'completion_reported' if delivery_valid(result) else 'delivery_invalid'
            reason = '报告完成，等待协调者独立验收' if status == 'completion_reported' else '完成报告缺少有效的绝对路径交付产物'
        elif result and result['status'] == 'blocked':
            status = 'blocked'
        elif observed == 'unknown':
            status = 'unknown'
        elif observed == 'idle' and state['idle_streak'] >= 2:
            status = 'idle_unfinished'
        else:
            status = 'running'
    except (RuntimeError, ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
        reason = str(exc)
        state['idle_streak'] = 0
    if now - state['started_at'] >= args.timeout and status != 'session_changed':
        status, reason = 'timed_out', '监听到期；任务尚未由协调者验收'
        state['stopped'] = True
    state['stalled'] = now - state['last_progress_at'] >= args.stale_after and status not in ('completion_reported', 'session_changed', 'timed_out', 'blocked')
    state.update(status=status, reason=reason)
    if status == 'completion_reported':
        state['stopped'] = True
    if state['stopped']:
        state.setdefault('terminal_at', now)
    if status != previous or (status == 'blocked' and blocked_changed):
        add_event(state, status, args, now)
    if blocked_cleared:
        add_event(state, 'blocked_cleared', args, now)
    if state['stalled'] and not previous_stalled:
        add_event(state, 'stalled', args, now)
    elif previous_stalled and not state['stalled']:
        add_event(state, 'stalled_cleared', args, now)
    protocol.atomic(args.state, state)
    notify_once(args, state, rows)
    protocol.atomic(args.state, state)
    print(json.dumps(dict(task_id=args.task_id, status=status, stalled=state['stalled'], state=str(args.state)), ensure_ascii=False), flush=True)


def main():
    args = arguments()
    args.state.parent.mkdir(parents=True, exist_ok=True)
    with Path(str(args.state) + '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('已有进程监听此状态路径', file=sys.stderr)
            return 4
        state = load_state(args, time.time())
        suppress_quiet_events(state)
        # 实时状态与通知必须串行采样；默认观察上限 7200 秒，终止通知宽限 300 秒。
        while True:
            if state['stopped']:
                if not args.once and args.notify and bool(pending_events(state)) and time.time() - state['terminal_at'] >= args.notify_grace:
                    state['delivery_error'] = '通知宽限期已到，仍有待发送事件；请人工检查状态文件，或沿用原参数 --once 重试'
                    protocol.atomic(args.state, state)
                    print(state['delivery_error'], file=sys.stderr)
                    return 5
                try:
                    notify_once(args, state, discover())
                except (RuntimeError, ValueError, KeyError, OSError, subprocess.TimeoutExpired) as exc:
                    state['notify_error'] = '通知接收会话暂不可验证：' + str(exc)
                protocol.atomic(args.state, state)
            else:
                tick(args, state)
            if not pending_events(state):
                state.pop('delivery_error', None)
                protocol.atomic(args.state, state)
            if args.once:
                return 0
            if state['stopped'] and (not args.notify or not pending_events(state)):
                state.pop('delivery_error', None)
                protocol.atomic(args.state, state)
                return 0
            time.sleep(args.interval)


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (OSError, ValueError, KeyError) as exc:
        print('任务监听失败：' + str(exc), file=sys.stderr)
        sys.exit(4)
    except KeyboardInterrupt:
        print('任务监听已停止；任务完成状态以协调者验收为准', file=sys.stderr)
        sys.exit(130)
