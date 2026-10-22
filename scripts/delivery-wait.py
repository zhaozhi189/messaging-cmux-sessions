#!/usr/bin/env python3
"""等待已有持久消息安全投递；原编号重试，草稿与权限框只观察。"""
import argparse
import fcntl
import hashlib
import importlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

import protocol

watch = importlib.import_module('task-watch')
FINAL = ('notified', 'received', 'completed')
FOOTER = (r'\s*(?:[─━═]+|\? for shortcuts|\d+% context left.*|'
          r'GPT-\S+ (?:default|minimal|low|medium|high|xhigh|max|ultra)(?: fast)? · [^\n]+ · '
          r'Context \d+% used · weekly \d+% left · \d+\.\d+\.\d+'
          r'(?: · \d+[KM] window)?(?: · Main \[default\])?|'
          r'⚠ \d+ warnings? · f2 to view|tab to queue message|'
          r'← for agents · \? for shortcuts(?:\s+⚠ \d+ warnings? · f2 to view| · f2 to view)?)\s*')
PLACEHOLDERS = ('', 'Ask Codex to do anything', 'Implement {feature}',
                'Find and fix a bug in @filename', 'Write tests for @filename',
                'Summarize recent commits', 'Improve documentation in @filename',
                'Explain this codebase')


def arguments():
    parser = argparse.ArgumentParser(description='前台等待原持久消息投递；超时不是成功，无隐式自唤醒。')
    parser.add_argument('--id', required=True, help='已有信箱消息编号')
    parser.add_argument('--state', type=Path, help='监听状态绝对路径')
    parser.add_argument('--target', help='可显式校验 surface:N，默认取原消息目标')
    parser.add_argument('--interval', type=watch.positive, default=30, help='检查间隔，最多 60 秒')
    parser.add_argument('--timeout', type=watch.positive, default=1800, help='本轮等待上限秒数，默认 1800')
    parser.add_argument('--resume', action='store_true', help='核实原任务和路由后显式续挂一轮期限')
    parser.add_argument('--source-label', help='新通知展示来源，首次落盘后不得更改')
    parser.add_argument('--once', action='store_true', help='检查并尝试一轮，未送达退出 2')
    parser.add_argument('--probe', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    protocol.message_path(args.id)
    if args.interval > 60:
        parser.error('每次等待不得超过 60 秒')
    if not args.probe and (not args.state or not args.state.is_absolute()):
        parser.error('--state 必须是绝对路径')
    if args.state and args.state == protocol.message_path(args.id):
        parser.error('监听状态不能覆盖信箱记录')
    if args.target and not re.fullmatch(r'surface:\d+', args.target):
        parser.error('--target 必须是 surface:N')
    if args.source_label and any(c in args.source_label for c in '\n\r\x1b'):
        parser.error('展示来源不能含终端控制字符')
    return args


def message(identifier):
    data = json.loads(protocol.message_path(identifier).read_text())
    if (data.get('id') != identifier or data.get('state') not in ('recorded',) + FINAL
            or not re.fullmatch(r'surface:\d+', data.get('target', ''))
            or any(not isinstance(data.get(k), str) or not data[k] for k in ('sender', 'body', 'endpoint'))):
        raise ValueError('原消息记录缺失或不合法，不得重新构造任务')
    return data


def probe(data, target=None):
    if target and target != data['target']:
        raise ValueError('指定目标与原消息不一致')
    if data['state'] in FINAL:
        return data['state']
    rows = watch.discover()
    try:
        key, row = watch.endpoint(rows, data['target'])
    except ValueError:
        return 'session_changed'
    if key != data['endpoint']:
        return 'session_changed'
    result = subprocess.run([protocol.CMUX, 'read-screen', '--window', row['window'],
                             '--surface', row['ref'], '--scrollback', '--lines', '40'],
                            capture_output=True, text=True, timeout=10)
    if result.returncode:
        return 'unknown'
    observed = watch.classify_screen(result.stdout)
    if observed == 'approval_required':
        return observed
    if observed == 'busy' or protocol.busy(data['target'], current_endpoint=key) == 'busy':
        return 'busy'
    if observed != 'idle':
        return 'unknown'
    lines = result.stdout.splitlines()
    prompts = [(i, re.sub(r'^\s*[❯›][ \t]*', '', line).strip())
               for i, line in enumerate(lines) if re.match(r'^\s*[❯›][ \t]', line)]
    if not prompts:
        return 'unknown'
    index, text = prompts[-1]
    # 不把换行草稿或折行通知当空箱；只允许明确的分隔线与已知状态提示。
    for line in lines[index + 1:]:
        if line.strip() and not re.fullmatch(FOOTER, line, re.IGNORECASE):
            return 'foreign_input'
    notice = '请查 cmux 收件箱：' + data['id']
    if re.fullmatch(r'(?:\(from [^()\r\n]+\) )?' + re.escape(notice), text):
        return 'owned'
    return 'empty' if text in PLACEHOLDERS else 'foreign_input'


def identity(data):
    return dict(message_id=data['id'], target=data['target'], endpoint=data['endpoint'],
                sender=data['sender'], body_hash=hashlib.sha256(data['body'].encode()).hexdigest())


def load_state(args, data):
    expected = identity(data)
    if args.state.exists():
        state = json.loads(args.state.read_text())
        if any(state.get(k) != v for k, v in expected.items()):
            raise ValueError('监听状态与原消息身份不匹配，拒绝改派')
        if args.source_label and args.source_label != state.get('source_label'):
            raise ValueError('恢复监听不得更换已固定的来源标签')
    else:
        state = dict(expected, started_at=time.time(), attempts=0,
                     source_label=args.source_label or data['sender'])
    if args.resume:
        state.update(started_at=time.time(), resumed_at=time.time(), status='waiting')
        state.pop('error', None)
    return state


def stop_send(process):
    # 子进程独立进程组；超时或取消时必须一起停止，不能留下迟到的终端输入。
    for signum, seconds in ((signal.SIGTERM, 2), (signal.SIGKILL, 1)):
        try:
            os.killpg(process.pid, signum)
        except ProcessLookupError:
            pass
        try:
            process.communicate(timeout=seconds)
            return
        except subprocess.TimeoutExpired:
            continue
    raise RuntimeError('发送子进程未及时退出，请核对监听 PID 与日志')


def run_send(data, state):
    command = ['bash', str(Path(__file__).with_name('send.sh')), data['target'],
               '--durable', '--id', data['id'], '--retry', data['body']]
    env = dict(os.environ, CMUX_FROM=data['sender'], CMUX_FROM_LABEL=state['source_label'])
    process = subprocess.Popen(command, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, start_new_session=True)
    try:
        stdout, stderr = process.communicate(timeout=55)
        state.update(last_exit_code=process.returncode, last_output=(stdout + stderr)[-3000:])
    except subprocess.TimeoutExpired:
        stop_send(process)
        state.update(last_exit_code=3, last_output='单次投递检查超时，已停止本次发送子进程；沿原编号继续对账')
    except KeyboardInterrupt:
        stop_send(process)
        raise


def main():
    args = arguments()
    data = message(args.id)
    if args.target and args.target != data['target']:
        raise ValueError('指定目标与原消息不一致')
    if args.probe:
        print(probe(data, args.target))
        return 0
    args.state.parent.mkdir(parents=True, exist_ok=True)
    lock_path = protocol.ROOT / 'delivery-locks' / (args.id + '.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('已有进程监听此消息编号', file=sys.stderr)
            return 4
        state = load_state(args, data)
        try:
            return wait_for_delivery(args, state)
        except KeyboardInterrupt:
            state.update(status='stopped', stopped_at=time.time(), exit_code=130,
                         delivered=message(args.id)['state'] in FINAL,
                         error='投递等待已停止，终端可能仍有原门铃；须核对原消息和输入框，恢复需显式 --resume')
            protocol.atomic(args.state, state)
            print(state['error'], file=sys.stderr)
            return 130


def wait_for_delivery(args, state):
    # 每轮需重新观察实时输入，不能批量替代；默认本轮上限 1800 秒，单次等待最多 60 秒。
    while True:
        data = message(args.id)
        if identity(data) != {k: state[k] for k in identity(data)}:
            raise ValueError('原信箱消息身份已改变，停止旧任务')
        state.update(checked_at=time.time(), pid=os.getpid(), timeout=args.timeout,
                     deadline_at=state['started_at'] + args.timeout, exit_code=None)
        if data['state'] in FINAL:
            status = data['state']
        elif state.get('status') == 'stopped':
            status = 'stopped'
        elif state.get('status') == 'timed_out' or time.time() - state['started_at'] >= args.timeout:
            status = 'timed_out'
        else:
            try:
                status = probe(data, args.target)
                if status in ('empty', 'owned'):
                    state['attempts'] += 1
                    protocol.atomic(args.state, state)
                    run_send(data, state)
                    data = message(args.id)
                    status = probe(data, args.target)
                    if status in ('empty', 'owned'):
                        status = 'unconfirmed'
            except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
                status = 'unknown'
                state['error'] = str(exc)
        if status not in ('unknown', 'timed_out', 'session_changed', 'stopped'):
            state.pop('error', None)
        state.update(status=status, delivered=status in FINAL)
        if status == 'timed_out':
            state['error'] = '本轮等待到期，未送达；核实原任务及endpoint后用原参数 --resume 续挂，不把超时当完成'
        elif status == 'session_changed':
            state['error'] = '目标endpoint已改变，停止旧路由；需重新现查并决定任务归属，禁止盲目续挂'
        state['exit_code'] = (0 if status in FINAL else 6 if status == 'session_changed'
                              else 5 if status == 'timed_out' else 130 if status == 'stopped'
                              else 2 if args.once else None)
        protocol.atomic(args.state, state)
        print(json.dumps(dict(message_id=args.id, status=status, state=str(args.state)), ensure_ascii=False), flush=True)
        if status in FINAL:
            return 0
        if status == 'session_changed':
            return 6
        if status == 'timed_out':
            return 5
        if status == 'stopped':
            return 130
        if args.once:
            return 2
        time.sleep(min(args.interval, max(.01, args.timeout - (time.time() - state['started_at']))))


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (RuntimeError, OSError, ValueError, KeyError) as exc:
        print('投递等待失败：' + str(exc), file=sys.stderr)
        sys.exit(4)
    except KeyboardInterrupt:
        print('已停止投递等待；原消息仍需对账', file=sys.stderr)
        sys.exit(130)
