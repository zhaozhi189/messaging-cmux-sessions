#!/usr/bin/env python3
"""cmux 结构化发现、持久消息与状态校验。"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(os.environ.get('CMUX_MSG_DIR', str(Path.home() / '.claude/cmux-messaging')))
CMUX = os.environ.get('CMUX_BIN') or __import__('shutil').which('cmux') or '/Applications/cmux.app/Contents/Resources/bin/cmux'


def rpc(*args):
    result = subprocess.run([CMUX, *args], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or 'cmux 调用失败')
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError('cmux 未返回有效 JSON') from exc


def endpoints():
    data = rpc('--json', 'tree', '--all')
    rows = []
    for window in data['windows']:
        for workspace in window['workspaces']:
            for pane in workspace['panes']:
                for surface in pane['surfaces']:
                    if surface.get('type') == 'terminal':
                        rows.append(dict(surface, window=window['ref'], workspace=workspace['title'], workspace_id=workspace['id']))
    return rows


def identity():
    caller = rpc('identify').get('caller')
    return caller.get('surface_ref', '') if caller else ''


def endpoint(ref):
    matches = [row for row in endpoints() if row['ref'] == ref]
    if len(matches) != 1:
        raise RuntimeError('目标不存在或不唯一')
    row = matches[0]
    return '|'.join([row['workspace_id'], row.get('tty', ''), ref])


def atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def message_path(message_id):
    if not message_id or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in message_id):
        raise ValueError('消息编号不合法')
    return ROOT / 'messages' / (message_id + '.json')


def enqueue(ref, sender, body, message_id=None):
    message_id = message_id or 'm' + uuid.uuid4().hex
    current_endpoint = endpoint(ref)
    path = message_path(message_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with (path.parent / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if path.exists():
            old = json.loads(path.read_text())
            if old['endpoint'] != current_endpoint:
                raise ValueError('目标会话已改变，旧消息编号不能派给新会话')
            if (old['target'], old['sender'], old['body']) != (ref, sender, body):
                raise ValueError('同一编号不能用于不同消息')
            return message_id
        atomic(path, dict(id=message_id, target=ref, endpoint=current_endpoint, sender=sender, body=body, state='recorded', ts=int(time.time())))
    return message_id


def transition(message_id, state, actor):
    path = message_path(message_id)
    with (path.parent / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(path.read_text())
        if state in ('received', 'completed'):
            if actor != data['target'] or endpoint(actor) != data['endpoint']:
                raise ValueError('接收确认必须来自记录中的目标会话')
        allowed = {'recorded': {'notified', 'received'}, 'notified': {'received'}, 'received': {'completed'}, 'completed': set()}
        if state == 'notified' and data['state'] in ('received', 'completed'):
            return
        if state == data['state']:
            return
        if state not in allowed[data['state']]:
            raise ValueError('状态转换不合法；完成前必须先确认接收')
        data.update(state=state, updated_at=int(time.time()))
        atomic(path, data)


def busy(ref, state=None):
    path = ROOT / 'busy-v2' / (ref.replace(':', '_') + '.json')
    key = endpoint(ref)
    if state is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with (path.parent / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            old = json.loads(path.read_text()) if path.exists() else {}
            sequence = old.get('seq', 0) + 1 if old.get('endpoint') == key else 1
            atomic(path, dict(endpoint=key, seq=sequence, state=state, ts=int(time.time()), source='lifecycle-hook'))
        return
    if not path.exists():
        return 'unknown'
    data = json.loads(path.read_text())
    age = int(time.time()) - data['ts']
    if data.get('endpoint') != key or not 0 <= age < int(os.environ.get('CMUX_BUSY_TTL', '120')):
        return 'unknown'
    return data['state'] if data.get('state') in ('busy', 'idle') else 'unknown'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('command', choices=['list', 'self', 'enqueue', 'notified', 'ack', 'complete', 'status', 'inbox', 'busy', 'busy-write'])
    parser.add_argument('args', nargs='*')
    args = parser.parse_args()
    command, values = args.command, args.args
    counts = {'list': (0,), 'self': (0,), 'enqueue': (3, 4), 'notified': (1,), 'ack': (1,), 'complete': (1,), 'status': (1,), 'inbox': (0,), 'busy': (1,), 'busy-write': (1,)}
    if len(values) not in counts[command]:
        raise ValueError('参数数量不正确')
    if command == 'list':
        for row in endpoints():
            fields = [row['ref'], row['window'], row['workspace'], row['title']]
            if any('|' in field or '\n' in field or '\t' in field for field in fields):
                raise ValueError('会话标题包含分隔符，请重命名')
            print('|'.join(fields))
    elif command == 'self':
        print(identity())
    elif command == 'enqueue':
        print(enqueue(*values))
    elif command in ('notified', 'ack', 'complete'):
        transition(values[0], {'notified': 'notified', 'ack': 'received', 'complete': 'completed'}[command], identity())
    elif command == 'status':
        print(message_path(values[0]).read_text())
    elif command == 'inbox':
        actor = identity()
        if not actor:
            raise ValueError('外部进程没有接收会话身份')
        key = endpoint(actor)
        for path in sorted((ROOT / 'messages').glob('*.json')):
            data = json.loads(path.read_text())
            if data['target'] == actor and data['endpoint'] == key and data['state'] != 'completed':
                print(json.dumps(data, ensure_ascii=False))
    elif command == 'busy':
        print(busy(values[0]))
    else:
        ref = identity()
        if ref:
            busy(ref, values[0])


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, ValueError, KeyError, OSError) as exc:
        print('cmux 协议错误：' + str(exc), file=sys.stderr)
        sys.exit(6)
