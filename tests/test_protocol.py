import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.record = self.directory / 'messages/mreceiver.json'
        self.record.parent.mkdir()
        self.data = dict(id='mreceiver', target='surface:2', endpoint='gen1|t2|surface:2',
                         sender='frontend', body='完整正文\n请阅读 /private/tmp/task.md',
                         state='notified', ts=1)
        self.record.write_text(json.dumps(self.data, ensure_ascii=False))
        self.mock = self.directory / 'cmux'
        self.mock.write_text('''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
if os.environ.get('MOCK_DENY') == '1':
    print('Access denied', file=sys.stderr)
    sys.exit(1)
if args == ['identify']:
    ref = os.environ.get('MOCK_CALLER')
    print(json.dumps({'caller': {'surface_ref': ref} if ref else None,
                      'focused': {'surface_ref': 'surface:2'}}))
elif args == ['--json', 'tree', '--all']:
    print(json.dumps({'windows': [{'ref': 'window:1', 'workspaces': [{
        'id': os.environ.get('MOCK_GENERATION', 'gen1'), 'title': 'group',
        'panes': [{'surfaces': [{'ref': 'surface:2', 'title': 'receiver',
                               'type': 'terminal', 'tty': 't2'}]}]}]}]}))
else:
    sys.exit(1)
''')
        self.mock.chmod(0o755)
        self.env = dict(os.environ, CMUX_BIN=str(self.mock), CMUX_MSG_DIR=str(self.directory),
                        MOCK_CALLER='', MOCK_DENY='0', MOCK_GENERATION='gen1')

    def run_send(self, *args, **env):
        return subprocess.run(['bash', str(ROOT / 'scripts/send.sh'), *args],
                              env=dict(self.env, **env), capture_output=True, text=True, timeout=10)

    def test_status_reads_full_body_without_cmux_and_preserves_record(self):
        before = self.record.read_bytes()
        result = self.run_send('--status', 'mreceiver', MOCK_DENY='1')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.data)
        self.assertEqual(self.record.read_bytes(), before)

    def test_external_inbox_suggests_read_only_status(self):
        before = self.record.read_bytes()
        result = self.run_send('--inbox')
        self.assertEqual(result.returncode, 6)
        self.assertEqual(result.stdout, '')
        self.assertIn('--status', result.stderr)
        self.assertEqual(self.record.read_bytes(), before)

    def test_external_confirmations_never_use_focused_identity(self):
        for state in ('recorded', 'notified', 'received', 'completed'):
            self.data['state'] = state
            self.record.write_text(json.dumps(self.data))
            for option in ('--ack', '--complete'):
                with self.subTest(state=state, option=option):
                    before = self.record.read_bytes()
                    result = self.run_send(option, 'mreceiver')
                    self.assertEqual(result.returncode, 6)
                    self.assertEqual(result.stdout, '')
                    self.assertEqual(self.record.read_bytes(), before)
                    self.assertIn('--status mreceiver', result.stderr)

    def test_real_receiver_can_read_ack_and_complete(self):
        result = self.run_send('--inbox', MOCK_CALLER='surface:2')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.data)
        for option, state in (('--ack', 'received'), ('--complete', 'completed')):
            result = self.run_send(option, 'mreceiver', MOCK_CALLER='surface:2')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(self.record.read_text())['state'], state)

    def test_mismatched_receiver_or_endpoint_cannot_confirm(self):
        before = self.record.read_bytes()
        for env in (dict(MOCK_CALLER='surface:1'),
                    dict(MOCK_CALLER='surface:2', MOCK_GENERATION='gen2')):
            for option in ('--ack', '--complete'):
                with self.subTest(env=env, option=option):
                    result = self.run_send(option, 'mreceiver', **env)
                    self.assertEqual(result.returncode, 6)
                    self.assertEqual(self.record.read_bytes(), before)


if __name__ == '__main__':
    unittest.main()
