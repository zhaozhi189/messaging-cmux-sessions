import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


class RoutingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = self.directory / 'config.json'
        self.options = dict(caller='surface:1', surfaces=[
            dict(ref='surface:1', title='aimeter', type='terminal', tty='t1'),
            dict(ref='surface:2', title='aimeter-fe', type='terminal', tty='t2'),
        ])
        self.mock = self.directory / 'cmux'
        self.mock.write_text('''#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
root = Path(os.environ['MOCK_DIR'])
config = json.loads((root / 'config.json').read_text())
args = sys.argv[1:]
if args == ['--json', 'tree', '--all']:
    if config.get('deny'):
        print('Access denied', file=sys.stderr)
        sys.exit(1)
    workspaces = [{'id':'gen1', 'title':'group', 'panes':[{'surfaces':config['surfaces']}]}]
    if config.get('malformed_tail'):
        workspaces.append({'id':'broken', 'title':'broken'})
    print(json.dumps({'windows':[{'ref':'window:1', 'workspaces':workspaces}]}))
elif args == ['identify']:
    caller = config.get('caller')
    print(json.dumps({'caller':{'surface_ref':caller} if caller else None,
                      'focused':{'surface_ref':'surface:2'}}))
elif args[0] == 'read-screen':
    print('─' * 240)
    print('› ' if (root / 'sent').exists() else '› Ask Codex to do anything')
elif args[0] == 'send':
    with (root / 'sends.jsonl').open('a') as stream:
        stream.write(json.dumps(args, ensure_ascii=False) + '\\n')
    (root / 'sent').touch()
else:
    sys.exit(1)
''')
        self.mock.chmod(0o755)
        self.env = dict(os.environ, CMUX_BIN=str(self.mock), MOCK_DIR=str(self.directory),
                        CMUX_MSG_DIR=str(self.directory / 'state'),
                        CMUX_SESSIONS_CONF=str(self.directory / 'registry'), SEND_SETTLE='0')
        for name in ('CMUX_FROM', 'CMUX_FROM_LABEL', 'CMUX_REPLY_TO', 'SEND_SELF_OK'):
            self.env.pop(name, None)

    def run_send(self, *args, **env):
        self.config.write_text(json.dumps(self.options))
        return subprocess.run(['bash', str(ROOT / 'scripts/send.sh'), *args],
                              env=dict(self.env, **env), capture_output=True, text=True, errors='replace', timeout=10)

    def add_special_title(self):
        self.options['surfaces'].append(dict(ref='surface:3', title='shell | 调试\t窗口\n第二行',
                                             type='terminal', tty='t3'))

    def test_unrelated_special_title_does_not_block_exact_surface(self):
        self.add_special_title()
        result = self.run_send('surface:2', 'hello', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, '')
        self.assertIn('目标: surface:2', result.stdout)

    def test_list_escapes_special_titles_without_losing_sessions(self):
        self.add_special_title()
        result = self.run_send('--list')
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(all(len(line.split('\t')) == 4 for line in lines))
        self.assertIn('surface:3', lines[-1])

    def test_duplicate_title_after_special_title_is_rejected(self):
        self.add_special_title()
        self.options['surfaces'].append(dict(ref='surface:4', title='aimeter-fe', type='terminal', tty='t4'))
        result = self.run_send('aimeter-fe', 'hello', '--durable', '--id', 'mduplicate')
        self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
        self.assertIn('surface:2', result.stderr)
        self.assertIn('surface:4', result.stderr)
        self.assertFalse((self.directory / 'sends.jsonl').exists())
        self.assertFalse((self.directory / 'state/messages/mduplicate.json').exists())

    def test_special_title_can_be_addressed_exactly(self):
        self.add_special_title()
        result = self.run_send(self.options['surfaces'][-1]['title'], 'hello', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('目标: surface:3', result.stdout)

    def test_malformed_discovery_emits_no_partial_list_or_message(self):
        self.options['malformed_tail'] = True
        for args in (('--list',), ('surface:2', 'hello', '--durable', '--id', 'mbroken')):
            with self.subTest(args=args):
                result = self.run_send(*args)
                self.assertEqual(result.returncode, 6, result.stdout + result.stderr)
                self.assertEqual(result.stdout, '')
                self.assertFalse((self.directory / 'sends.jsonl').exists())
                self.assertFalse((self.directory / 'state/messages/mbroken.json').exists())

    def test_registry_alias_and_missing_target_keep_exit_codes(self):
        (self.directory / 'registry').write_text('frontend | aimeter-fe | /tmp/frontend | demo | 前端\n')
        result = self.run_send('frontend', 'hello', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('目标: surface:2', result.stdout)
        self.assertEqual(self.run_send('missing', 'hello', '--dry-run').returncode, 1)

    def test_conflicting_source_is_rejected_before_enqueue(self):
        for index, env in enumerate((dict(CMUX_FROM='invented-be'), dict(CMUX_FROM_LABEL='invented-be'))):
            with self.subTest(env=env):
                message_id = 'mconflict' + str(index)
                for name in ('sent', 'sends.jsonl'):
                    (self.directory / name).unlink(missing_ok=True)
                result = self.run_send('surface:2', 'hello', '--durable', '--id', message_id, **env)
                self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
                self.assertFalse((self.directory / ('state/messages/' + message_id + '.json')).exists())
                self.assertFalse((self.directory / 'sends.jsonl').exists())

    def test_real_source_and_declared_external_source_are_visible(self):
        result = self.run_send('surface:2', 'hello', '--dry-run', CMUX_FROM='aimeter')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('(from aimeter) hello', result.stdout)
        self.options['caller'] = None
        result = self.run_send('surface:2', 'hello', '--dry-run', CMUX_FROM='aimeter')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('(from aimeter) hello', result.stdout)
        result = self.run_send('surface:2', 'hello', '--dry-run')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('来源未识别', result.stdout)

    def test_existing_message_preserves_sender_after_caller_changes(self):
        record = self.directory / 'state/messages/moriginal.json'
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps(dict(id='moriginal', target='surface:2',
                                         endpoint='gen1|t2|surface:2', sender='original-sender',
                                         body='hello', state='recorded', ts=1)))
        result = self.run_send('surface:2', 'hello', '--durable', '--id', 'moriginal',
                               CMUX_FROM='original-sender')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(record.read_text())['sender'], 'original-sender')
        self.assertIn('(from original-sender)', (self.directory / 'sends.jsonl').read_text())

    def test_source_control_characters_are_rejected_without_sending(self):
        self.options['caller'] = None
        for value in ('aimeter\nhello', 'aimeter\rhello', 'aimeter\x1bhello'):
            with self.subTest(value=value):
                result = self.run_send('surface:2', 'hello', CMUX_FROM=value)
                self.assertEqual(result.returncode, 4, result.stdout + result.stderr)
                self.assertFalse((self.directory / 'sends.jsonl').exists())


if __name__ == '__main__':
    unittest.main()
