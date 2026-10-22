#!/usr/bin/env python3
"""cmux 结构化发现、持久消息与状态校验。"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(os.environ.get('CMUX_MSG_DIR', str(Path.home() / '.claude/cmux-messaging')))
CMUX = os.environ.get('CMUX_BIN') or __import__('shutil').which('cmux') or '/Applications/cmux.app/Contents/Resources/bin/cmux'
UNKNOWN_SOURCE = '来源未识别（外部进程）'
MONITOR_PREFIX = '监听器 / '


class CommandError(ValueError):
    def __init__(self, message, exit_code):
        super().__init__(message)
        self.exit_code = exit_code


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
    for row in rows:
        if any(not isinstance(row.get(key), str) for key in ('ref', 'window', 'workspace', 'workspace_id', 'title')):
            raise ValueError('会话清单字段缺失或类型不正确')
    return rows


def display_field(value):
    return re.sub(r'[\x00-\x1f\x7f\\|]', lambda match: '\\u%04x' % ord(match[0]), value)


def resolve(target, registry=None):
    rows = endpoints()
    if registry and Path(registry).exists():
        for line in Path(registry).read_text().splitlines():
            if not line.strip() or line.lstrip().startswith('#'):
                continue
            fields = [field.strip() for field in line.split('|')]
            if len(fields) >= 2 and fields[0] == target:
                target = fields[1]
                break
    if re.fullmatch(r'surface:\d+', target):
        matches = [row for row in rows if row['ref'] == target]
    else:
        matches = [row for row in rows if row['title'] == target]
        if not matches:
            matches = [row for row in rows if target in row['title']]
    if not matches:
        raise CommandError('未找到会话：' + display_field(target), 1)
    if len(matches) != 1:
        candidates = ', '.join(row['ref'] for row in matches)
        raise CommandError('目标不唯一：' + display_field(target) + '；候选：' + candidates, 4)
    return matches[0]


def sender(message_id=None):
    declared = os.environ.get('CMUX_FROM', '')
    label = os.environ.get('CMUX_FROM_LABEL', '')
    monitor = os.environ.get('SEND_SELF_OK') == '1' and label.startswith(MONITOR_PREFIX)
    original = message_path(message_id) if message_id else None
    original_exists = original is not None and original.exists()
    if original_exists:
        source = json.loads(original.read_text())['sender']
        if declared and declared != source:
            raise CommandError('来源与原消息 sender 冲突，重试必须保留原来源', 4)
    elif monitor:
        source = declared or UNKNOWN_SOURCE
    else:
        actor = identity()
        if actor:
            rows = [row for row in endpoints() if row['ref'] == actor]
            if len(rows) != 1:
                raise ValueError('当前 caller 不存在或不唯一，不能确定来源')
            source = rows[0]['title'] or actor
            if declared and declared != source:
                raise CommandError('CMUX_FROM 与真实 caller 来源冲突：' + display_field(source), 4)
        else:
            source = declared or UNKNOWN_SOURCE
    for value in (source, label):
        if not isinstance(value, str) or re.search(r'[\x00-\x1f\x7f]', value):
            raise CommandError('来源名称不能包含终端控制字符', 4)
    if not source:
        raise ValueError('原消息 sender 不能为空')
    if label and label != source and not (monitor or original_exists):
        raise CommandError('CMUX_FROM_LABEL 不能覆盖新消息的实际来源', 4)
    return source


def identity():
    caller = rpc('identify').get('caller')
    return caller.get('surface_ref', '') if caller else ''


def receiver_identity(message_id=None):
    actor = identity()
    if not actor:
        message_id = message_id or '<消息编号>'
        raise ValueError('外部进程没有接收会话身份；请用 send.sh --status ' + message_id
                         + ' 只读完整正文，按约定更新进度和结果文件；'
                         + '不能执行 --ack/--complete，也不要用 focused 冒充 caller')
    return actor


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


def busy(ref, state=None, current_endpoint=None):
    path = ROOT / 'busy-v2' / (ref.replace(':', '_') + '.json')
    key = current_endpoint or endpoint(ref)
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
    parser.add_argument('command', choices=['list', 'resolve', 'source', 'self', 'enqueue', 'notified', 'ack', 'complete', 'status', 'inbox', 'busy', 'busy-write'])
    parser.add_argument('args', nargs='*')
    args = parser.parse_args()
    command, values = args.command, args.args
    counts = {'list': (0,), 'resolve': (1, 2), 'source': (0, 1), 'self': (0,), 'enqueue': (3, 4), 'notified': (1,), 'ack': (1,), 'complete': (1,), 'status': (1,), 'inbox': (0,), 'busy': (1,), 'busy-write': (1,)}
    if len(values) not in counts[command]:
        raise ValueError('参数数量不正确')
    if command == 'list':
        lines = ['|'.join(display_field(row[key]) for key in ('ref', 'window', 'workspace', 'title'))
                 for row in endpoints()]
        if lines:
            print('\n'.join(lines))
    elif command == 'resolve':
        print(json.dumps(resolve(*values), ensure_ascii=False))
    elif command == 'source':
        print(sender(*values))
    elif command == 'self':
        print(identity())
    elif command == 'enqueue':
        print(enqueue(*values))
    elif command in ('notified', 'ack', 'complete'):
        actor = identity() if command == 'notified' else receiver_identity(values[0])
        transition(values[0], {'notified': 'notified', 'ack': 'received', 'complete': 'completed'}[command], actor)
    elif command == 'status':
        print(message_path(values[0]).read_text())
    elif command == 'inbox':
        actor = receiver_identity()
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
    except CommandError as exc:
        print('cmux 协议错误：' + str(exc), file=sys.stderr)
        sys.exit(exc.exit_code)
    except (RuntimeError, ValueError, KeyError, TypeError, OSError) as exc:
        print('cmux 协议错误：' + str(exc), file=sys.stderr)
        sys.exit(6)
