import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/task-watch.py'


class TaskWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.config = self.directory / 'config.json'
        self.result = self.directory / 'progress.json'
        self.state = self.directory / 'watch.json'
        self.artifact = self.directory / '交付.txt'
        self.artifact.write_text('交付内容')
        self.options = dict(screen='› Ask Codex to do anything', generation='gen1', caller=None)
        self.mock = self.directory / 'cmux'
        self.mock.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
p = pathlib.Path(os.environ['MOCK_DIR'])
c = json.loads((p / 'config.json').read_text())
args = sys.argv[1:]
command = args[1] if args[0] == '--json' else args[0]
with (p / 'calls.jsonl').open('a') as f: f.write(json.dumps(args) + '\\n')
if command == 'tree':
 print(json.dumps({'windows':[{'ref':'window:1','workspaces':[{'id':c['generation'],'title':'group','panes':[{'surfaces':[{'ref':'surface:1','title':'receiver','type':'terminal','tty':'t1'},{'ref':'surface:2','title':'worker','type':'terminal','tty':'t2'}]}]}]}]}))
elif command == 'identify':
 print(json.dumps({'caller': {'surface_ref':c['caller']} if c.get('caller') else None, 'focused': {'surface_ref':'surface:2'}}))
elif command == 'read-screen':
 if c.get('screen_fail'): sys.exit(1)
 print('─' * 160)
 if args[args.index('--surface') + 1] == 'surface:1':
  print(c.get('receiver_screen', '› ' if (p / 'sent').exists() else '› Ask Codex to do anything'))
 else: print(c['screen'])
elif command == 'send':
 count = int((p / 'send-count').read_text()) + 1 if (p / 'send-count').exists() else 1
 (p / 'send-count').write_text(str(count))
 if c.get('send_fail') or count <= c.get('send_fail_count', 0): sys.exit(1)
 (p / 'sent').touch()
''')
        self.mock.chmod(0o755)
        self.env = dict(os.environ, CMUX_BIN=str(self.mock), MOCK_DIR=str(self.directory),
                        CMUX_MSG_DIR=str(self.directory / 'messages'), SEND_SETTLE='0')
        self.since = time.time() - 10

    def run_tick(self, *extra, expected=0, once=True):
        self.config.write_text(json.dumps(self.options))
        command = [sys.executable, str(SCRIPT), '--task-id', 'task-a', '--target', 'surface:2',
                   '--result', str(self.result), '--state', str(self.state), '--since', str(self.since), *extra]
        if once:
            command.append('--once')
        completed = subprocess.run(command, env=self.env, capture_output=True, text=True, timeout=10)
        self.assertEqual(completed.returncode, expected, completed.stdout + completed.stderr)
        return json.loads(self.state.read_text()) if self.state.exists() else None

    def progress(self, **changes):
        data = dict(task_id='task-a', target_endpoint='gen1|t2|surface:2', status='running',
                    updated_at=time.time(), artifacts=[], summary='正在处理')
        data.update(changes)
        self.result.write_text(json.dumps(data))

    def calls(self):
        return [json.loads(line) for line in (self.directory / 'calls.jsonl').read_text().splitlines()]

    def test_permission_prompt_precedes_running_and_never_approves(self):
        self.progress()
        for screen in ['Do you want to proceed?\n1. Yes\n2. No\n› ',
                       'Would you like to run the following command?\n1. Yes\n2. No\n› ',
                       'Do you want to make this edit?\n1. Yes\n2. No\n❯ ']:
            self.options['screen'] = screen
            state = self.run_tick()
            self.assertEqual(state['status'], 'approval_required')
        self.assertFalse(any(call[0] == 'send' for call in self.calls()))

    def test_old_or_wrong_task_completed_is_not_delivery(self):
        for changes in [dict(updated_at=self.since - 1), dict(task_id='old-task'),
                        dict(target_endpoint='gen0|t2|surface:2')]:
            self.progress(status='completed', artifacts=[str(self.artifact)], **changes)
            state = self.run_tick()
            self.assertNotEqual(state['status'], 'completion_reported')

    def test_future_result_does_not_hide_subsequent_progress(self):
        self.progress(status='completed', artifacts=[str(self.artifact)], updated_at=time.time() + 1000)
        self.assertNotEqual(self.run_tick()['status'], 'completion_reported')
        self.progress(status='completed', artifacts=[str(self.artifact)])
        self.assertEqual(self.run_tick()['status'], 'completion_reported')

    def test_completed_is_only_reported_and_requires_existing_absolute_artifacts(self):
        self.progress(status='completed', artifacts=[str(self.artifact)])
        state = self.run_tick()
        self.assertEqual(state['status'], 'completion_reported')
        self.assertNotIn(state['status'], ('accepted', 'verified'))
        for artifacts in [[], ['relative.txt'], [str(self.directory / 'missing')]]:
            self.state.unlink()
            self.progress(status='completed', artifacts=artifacts)
            state = self.run_tick()
            self.assertEqual(state['status'], 'delivery_invalid')

    def test_idle_requires_two_ticks_and_read_failure_is_unknown(self):
        self.assertNotEqual(self.run_tick()['status'], 'idle_unfinished')
        self.assertEqual(self.run_tick()['status'], 'idle_unfinished')
        self.options['screen_fail'] = True
        for _ in range(2):
            state = self.run_tick()
            self.assertEqual(state['status'], 'unknown')
            self.assertEqual(state['idle_streak'], 0)

    def test_ready_prompt_supersedes_old_approval_and_spinner(self):
        self.options['screen'] = 'Do you want to proceed?\nWorking (esc to interrupt)\n› Ask Codex to do anything'
        self.assertNotEqual(self.run_tick()['status'], 'approval_required')
        self.assertEqual(self.run_tick()['status'], 'idle_unfinished')

    def test_live_busy_banner_is_not_idle_with_ready_placeholder(self):
        self.options['screen'] = '• Working (1m 58s • esc to interrupt)\n› Ask Codex to do anything'
        for _ in range(2):
            state = self.run_tick()
            self.assertEqual(state['screen_state'], 'busy')
            self.assertNotEqual(state['status'], 'idle_unfinished')

    def test_live_approval_precedes_busy_banner_and_placeholder(self):
        self.options['screen'] = ('• Working (1m 58s • esc to interrupt)\n'
                                  'Do you want to proceed?\n1. Yes\n2. No\n› Ask Codex to do anything')
        state = self.run_tick()
        self.assertEqual(state['status'], 'approval_required')
        self.assertFalse(any(call[0] == 'send' for call in self.calls()))

    def test_completed_banner_makes_old_working_and_approval_historical(self):
        self.options['screen'] = ('• Working (1m 58s • esc to interrupt)\n'
                                  'Do you want to proceed?\n1. Yes\n2. No\n'
                                  '• Worked for 2m 10s\n› Ask Codex to do anything')
        self.assertNotEqual(self.run_tick()['status'], 'approval_required')
        self.assertEqual(self.run_tick()['status'], 'idle_unfinished')

    def test_notice_sender_is_stable_across_resume(self):
        self.options.update(screen='Do you want to proceed?\n1. Yes\n2. No', send_fail=True)
        self.env['CMUX_FROM'] = 'original-sender'
        first = self.run_tick('--notify', 'surface:1')
        identifier = first['events'][0]['id']
        self.env['CMUX_FROM'] = 'another-sender'
        self.options['send_fail'] = False
        state = self.run_tick('--notify', 'surface:1')
        self.assertTrue(state['events'][0]['delivered'])
        self.assertEqual(state['events'][0]['id'], identifier)
        record = self.directory / 'messages/messages' / (identifier + '.json')
        self.assertEqual(json.loads(record.read_text())['sender'], 'original-sender')

    def test_legacy_event_recovers_sender_from_original_message(self):
        self.options.update(screen='Do you want to proceed?\n1. Yes\n2. No', send_fail=True)
        self.env['CMUX_FROM'] = 'original-sender'
        first = self.run_tick('--notify', 'surface:1')
        identifier = first['events'][0]['id']
        first.pop('notify_sender', None)
        first['events'][0].pop('sender', None)
        self.state.write_text(json.dumps(first))
        self.env['CMUX_FROM'] = 'another-sender'
        self.options['send_fail'] = False
        state = self.run_tick('--notify', 'surface:1')
        self.assertTrue(state['events'][0]['delivered'])
        self.assertEqual(state['events'][0]['sender'], 'original-sender')
        self.assertEqual(state['events'][0]['id'], identifier)

    def test_running_is_silent_and_suppressed_is_not_delivered(self):
        self.options['screen'] = '• Working (1m • esc to interrupt)\n› Ask Codex to do anything'
        state = self.run_tick('--notify', 'surface:1')
        self.assertTrue(state['events'][0]['suppressed'])
        self.assertFalse(state['events'][0]['delivered'])
        self.assertFalse(any(call[0] == 'send' for call in self.calls()))

    def test_notice_shows_monitor_target_and_preserves_label_on_retry(self):
        self.options.update(screen='Do you want to proceed?\n1. Yes\n2. No', send_fail=True)
        first = self.run_tick('--notify', 'surface:1')
        event = first['events'][0]
        self.assertEqual(event['source_label'], '监听器 / worker')
        self.options['send_fail'] = False
        self.env['CMUX_FROM'] = 'changed'
        state = self.run_tick('--notify', 'surface:1')
        self.assertEqual(state['events'][0]['source_label'], event['source_label'])
        texts = [call[-1] for call in self.calls() if call[0] == 'send']
        self.assertTrue(any('(from 监听器 / worker)' in text for text in texts))
        self.assertEqual(state['events'][0]['id'], event['id'])

    def test_legacy_generic_source_is_visible_without_rewriting_record(self):
        self.options.update(screen='Do you want to proceed?\n1. Yes\n2. No', send_fail=True)
        self.env['CMUX_FROM'] = 'codex-external'
        first = self.run_tick('--notify', 'surface:1')
        event = first['events'][0]
        event.pop('source_label', None)
        event.pop('sender', None)
        self.state.write_text(json.dumps(first))
        record = self.directory / 'messages/messages' / (event['id'] + '.json')
        identity = json.loads(record.read_text())
        self.options['send_fail'] = False
        self.env['CMUX_FROM'] = 'changed'
        state = self.run_tick('--notify', 'surface:1')
        restored = json.loads(record.read_text())
        for field in ('id', 'body', 'sender'):
            self.assertEqual(restored[field], identity[field])
        self.assertTrue(any('(from codex-external)' in call[-1] for call in self.calls() if call[0] == 'send'))

    def test_old_pending_running_is_suppressed_and_does_not_hold_terminal(self):
        self.options['screen'] = '• Working (1m • esc to interrupt)\n› Ask Codex to do anything'
        state = self.run_tick('--notify', 'surface:1')
        state['events'][0].pop('suppressed', None)
        state.update(stopped=True, terminal_at=time.time() - 100, status='running')
        self.state.write_text(json.dumps(state))
        state = self.run_tick('--notify', 'surface:1', '--notify-grace', '.1', once=False)
        self.assertTrue(state['events'][0]['suppressed'])
        self.assertFalse(state['events'][0]['delivered'])
        self.assertNotIn('delivery_error', state)
        self.assertFalse(any(call[0] == 'send' for call in self.calls()))

    def test_session_generation_change_stops_monitor(self):
        self.run_tick()
        self.options['generation'] = 'gen2'
        state = self.run_tick()
        self.assertEqual(state['status'], 'session_changed')
        self.assertTrue(state['stopped'])

    def test_failed_notice_retains_id_and_body_and_does_not_spam(self):
        self.options.update(screen='Do you want to proceed?\n1. Yes\n2. No', send_fail=True)
        first = self.run_tick('--notify', 'surface:1')
        event = first['events'][0]
        self.assertFalse(event['delivered'])
        second = self.run_tick('--notify', 'surface:1')
        self.assertEqual(len(second['events']), 1)
        self.assertEqual((second['events'][0]['id'], second['events'][0]['body']), (event['id'], event['body']))
        self.options['send_fail'] = False
        state = self.run_tick('--notify', 'surface:1')
        self.assertTrue(state['events'][0]['delivered'])
        count = sum(call[0] == 'send' for call in self.calls())
        self.run_tick('--notify', 'surface:1')
        self.assertEqual(sum(call[0] == 'send' for call in self.calls()), count)

    def test_approval_notice_is_prioritized_over_failed_running_notice(self):
        self.options.update(screen='Working (esc to interrupt)\n› ', send_fail=True)
        self.run_tick('--notify', 'surface:1')
        self.options.update(screen='Do you want to proceed?\n1. Yes\n2. No', send_fail=False)
        state = self.run_tick('--notify', 'surface:1')
        event = next(event for event in state['events'] if event['type'] == 'approval_required')
        self.assertTrue(event['delivered'])
        self.assertEqual(state['events'][0]['attempts'], 0)
        self.assertTrue(state['events'][0]['suppressed'])

    def test_explicit_notice_works_without_caller_and_no_notice_only_writes_state(self):
        self.options['screen'] = 'Do you want to proceed?\n1. Yes\n2. No'
        state = self.run_tick()
        self.assertFalse(any(call[0] == 'send' for call in self.calls()))
        state = self.run_tick('--notify', 'surface:1')
        self.assertTrue(state['events'][0]['delivered'])
        self.assertEqual(state['notify_endpoint'], 'gen1|t1|surface:1')

    def test_notice_waits_while_receiver_busy_approval_or_unknown(self):
        self.options['screen'] = 'Do you want to proceed?\n1. Yes\n2. No'
        first_id = None
        for screen in ['Do you want to proceed?\n1. Yes\n2. No\n› ',
                       'Working (esc to interrupt)\n› ', 'user@host % ']:
            self.options['receiver_screen'] = screen
            state = self.run_tick('--notify', 'surface:1')
            event = state['events'][0]
            first_id = first_id or event['id']
            self.assertEqual(event['id'], first_id)
            self.assertFalse(event['delivered'])
            self.assertEqual(event['attempts'], 0)
            self.assertFalse(any(call[0] == 'send' for call in self.calls()))
        self.options['receiver_screen'] = '› Ask Codex to do anything'
        state = self.run_tick('--notify', 'surface:1')
        self.assertTrue(state['events'][0]['delivered'])
        self.assertEqual(state['events'][0]['id'], first_id)

    def test_changed_notice_endpoint_does_not_receive_old_event(self):
        self.options.update(screen='Do you want to proceed?\n1. Yes\n2. No', send_fail=True)
        self.run_tick('--notify', 'surface:1')
        count = sum(call[0] == 'send' for call in self.calls())
        self.options.update(generation='gen2', send_fail=False)
        state = self.run_tick('--notify', 'surface:1')
        self.assertEqual(state['status'], 'session_changed')
        self.assertTrue(state['stopped'])
        self.assertIn('身份已改变', state['notify_error'])
        self.assertEqual(sum(call[0] == 'send' for call in self.calls()), count)

    def test_regressed_result_timestamp_does_not_report_completion(self):
        stamp = time.time()
        self.progress(updated_at=stamp)
        self.run_tick()
        self.progress(status='completed', artifacts=[str(self.artifact)], updated_at=stamp - 1)
        self.assertNotEqual(self.run_tick()['status'], 'completion_reported')

    def test_result_blocked_is_reported_and_busy_keeps_running(self):
        self.progress(status='blocked', summary='缺少权限')
        self.assertEqual(self.run_tick()['status'], 'blocked')
        self.progress()
        self.options['screen'] = 'Working (esc to interrupt)\n› '
        state = self.run_tick()
        self.assertEqual(state['status'], 'running')
        self.assertFalse(state['stopped'])

    def test_stalled_is_alert_not_forced_stop_and_new_progress_clears_it(self):
        self.progress()
        self.options['screen'] = 'Working (esc to interrupt)\n› '
        self.run_tick()
        data = json.loads(self.state.read_text())
        data['last_progress_at'] = time.time() - 100
        self.state.write_text(json.dumps(data))
        state = self.run_tick('--stale-after', '30')
        self.assertTrue(state['stalled'])
        self.assertFalse(state['stopped'])
        self.assertIn('stalled', [event['type'] for event in state['events']])
        self.progress(summary='进入下一阶段')
        state = self.run_tick('--stale-after', '30')
        self.assertFalse(state['stalled'])
        self.assertIn('stalled_cleared', [event['type'] for event in state['events']])

    def test_timeout_stops_without_completion_and_once_never_sleeps(self):
        self.run_tick()
        data = json.loads(self.state.read_text())
        data['started_at'] = time.time() - 100
        self.state.write_text(json.dumps(data))
        started = time.monotonic()
        state = self.run_tick('--timeout', '30', '--interval', '30')
        self.assertLess(time.monotonic() - started, 5)
        self.assertEqual(state['status'], 'timed_out')
        self.assertTrue(state['stopped'])

    def test_timeout_notice_is_attempted_before_old_failed_notice(self):
        self.options.update(screen='Do you want to proceed?\n1. Yes\n2. No', send_fail=True)
        self.run_tick('--notify', 'surface:1')
        data = json.loads(self.state.read_text())
        data['started_at'] = time.time() - 100
        self.state.write_text(json.dumps(data))
        state = self.run_tick('--notify', 'surface:1', '--timeout', '30')
        timeout_event = next(event for event in state['events'] if event['type'] == 'timed_out')
        self.assertEqual(timeout_event['attempts'], 1)
        self.assertFalse(timeout_event['delivered'])
        self.assertTrue(state['stopped'])

    def test_terminal_notice_recovers_without_resuming_target_observation(self):
        self.options.update(screen='Do you want to proceed?\n1. Yes\n2. No', send_fail_count=2)
        state = self.run_tick('--notify', 'surface:1', '--timeout', '.01', '--interval', '.01',
                              '--notify-grace', '5', once=False)
        self.assertEqual(state['status'], 'timed_out')
        self.assertTrue(all(event['delivered'] for event in state['events']))
        target_reads = [call for call in self.calls() if call[0] == 'read-screen'
                        and call[call.index('--surface') + 1] == 'surface:2']
        self.assertEqual(len(target_reads), 2)

    def test_terminal_notice_expiry_records_error_and_exits_nonzero(self):
        self.options.update(screen='Do you want to proceed?\n1. Yes\n2. No', send_fail=True)
        state = self.run_tick('--notify', 'surface:1', '--timeout', '.01', '--interval', '.01',
                              '--notify-grace', '.1', once=False, expected=5)
        self.assertTrue(state['delivery_error'])
        self.assertTrue(any(not event['delivered'] for event in state['events']))

    def test_completed_report_stops_observation_during_notice_grace(self):
        self.progress(status='completed', artifacts=[str(self.artifact)])
        self.options['send_fail_count'] = 2
        state = self.run_tick('--notify', 'surface:1', '--interval', '.01', '--notify-grace', '5', once=False)
        self.assertEqual(state['status'], 'completion_reported')
        self.assertTrue(state['stopped'])
        self.assertTrue(all(event['delivered'] for event in state['events']))
        target_reads = [call for call in self.calls() if call[0] == 'read-screen'
                        and call[call.index('--surface') + 1] == 'surface:2']
        self.assertEqual(len(target_reads), 1)

    def test_duplicate_monitor_cannot_acquire_lock_and_exit_releases_lock(self):
        lock = Path(str(self.state) + '.lock')
        with lock.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.run_tick(expected=4)
        self.assertIsNotNone(self.run_tick())


if __name__ == '__main__':
    unittest.main()
