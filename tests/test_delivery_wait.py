import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import threading
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'scripts/delivery-wait.py'


class DeliveryWaitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / 'config.json'
        self.state = self.root / 'delivery.json'
        self.record = self.root / 'messages/messages/mdelivery.json'
        self.record.parent.mkdir(parents=True)
        self.record.write_text(json.dumps(dict(id='mdelivery', target='surface:2',
            endpoint='gen1|t2|surface:2', sender='original-sender', body='原任务正文', state='recorded', ts=time.time())))
        self.options = dict(screen='› Ask Codex to do anything', generation='gen1')
        self.mock = self.root / 'cmux'
        self.mock.write_text('''#!/usr/bin/env python3
import json,os,pathlib,sys
p=pathlib.Path(os.environ['MOCK_DIR']);c=json.loads((p/'config.json').read_text());a=sys.argv[1:]
with (p/'calls').open('a') as f:f.write(json.dumps(a)+'\\n')
cmd=a[1] if a[0]=='--json' else a[0]
if cmd=='tree':
 print(json.dumps({'windows':[{'ref':'window:1','workspaces':[{'id':c['generation'],'title':'group','panes':[{'surfaces':[{'ref':'surface:2','title':'worker','type':'terminal','tty':'t2'}]}]}]}]}))
elif cmd=='identify': print(json.dumps({'caller':None,'focused':{'surface_ref':'surface:2'}}))
elif cmd=='read-screen':
 print('─'*180)
 print('› ' if (p/'sent').exists() else c['screen'])
elif cmd=='send':
 if a[-1]==r'\\r' or a[-1].endswith(r'\\r'): (p/'sent').touch()
''')
        self.mock.chmod(0o755)
        self.env = dict(os.environ, CMUX_BIN=str(self.mock), MOCK_DIR=str(self.root),
                        CMUX_MSG_DIR=str(self.root/'messages'), SEND_SETTLE='0', CMUX_FROM='changed')

    def run_wait(self, *extra, expected=0, once=True):
        self.config.write_text(json.dumps(self.options))
        cmd=[sys.executable,str(SCRIPT),'--id','mdelivery','--state',str(self.state),*extra]
        if once:cmd.append('--once')
        result=subprocess.run(cmd,env=self.env,capture_output=True,text=True,timeout=12)
        self.assertEqual(result.returncode,expected,result.stdout+result.stderr)
        return json.loads(self.state.read_text())

    def sends(self):
        if not (self.root/'calls').exists():return []
        return [a for a in map(json.loads,(self.root/'calls').read_text().splitlines()) if a[0]=='send']

    def test_busy_then_idle_only_flushes_original_notice(self):
        notice='(from original-sender) 请查 cmux 收件箱：mdelivery'
        self.options['screen']='• Working (1m • esc to interrupt)\n› '+notice
        self.assertEqual(self.run_wait(expected=2)['status'],'busy')
        self.assertEqual(self.sends(),[])
        self.options['screen']='› '+notice
        self.assertEqual(self.run_wait()['status'],'notified')
        self.assertEqual([a[-1] for a in self.sends()],['\\r'])
        self.assertEqual(json.loads(self.record.read_text())['state'],'notified')

    def test_foreign_draft_waits_then_retries_same_id_and_sender(self):
        self.options['screen']='› 用户正在编辑的文本'
        self.assertEqual(self.run_wait(expected=2)['status'],'foreign_input')
        self.assertEqual(self.sends(),[])
        self.options['screen']='› Ask Codex to do anything'
        self.assertEqual(self.run_wait()['status'],'notified')
        self.assertEqual(len(self.sends()),1)
        self.assertIn('mdelivery',self.sends()[0][-1])
        self.assertNotIn('原任务正文',self.sends()[0][-1])
        self.assertEqual(json.loads(self.record.read_text())['sender'],'original-sender')

    def test_ack_or_notified_stops_without_resending(self):
        for status in ['received','completed','notified']:
            data=json.loads(self.record.read_text());data['state']=status;self.record.write_text(json.dumps(data))
            self.assertEqual(self.run_wait()['status'],status)
            self.assertEqual(self.sends(),[])

    def test_endpoint_change_stops_old_route(self):
        self.options['generation']='gen2'
        self.assertEqual(self.run_wait(expected=6)['status'],'session_changed')
        self.assertEqual(self.sends(),[])

    def test_approval_never_flushes_or_presses_buttons(self):
        self.options['screen']='Do you want to proceed?\n1. Yes\n2. No\n› (from original-sender) 请查 cmux 收件箱：mdelivery'
        self.assertEqual(self.run_wait(expected=2)['status'],'approval_required')
        self.assertEqual(self.sends(),[])

    def test_notice_with_other_text_is_not_ours_to_flush(self):
        for suffix in [' 用户草稿','\n  后续草稿内容']:
            self.options['screen']='› (from original-sender) 请查 cmux 收件箱：mdelivery'+suffix
            self.assertEqual(self.run_wait(expected=2)['status'],'foreign_input')
            self.assertEqual(self.sends(),[])

    def test_summary_notice_only_flushes_exact_stored_text(self):
        notice = '[125604 契约核查] 请查 cmux 收件箱：mdelivery'
        data = json.loads(self.record.read_text())
        data.update(summary='125604 契约核查', notice=notice)
        self.record.write_text(json.dumps(data))
        for screen, expected in [
                ('• Working (1m • esc to interrupt)\n› (from original-sender) ' + notice, 'busy'),
                ('Do you want to proceed?\n1. Yes\n2. No\n› ' + notice, 'approval_required'),
                ('› [其它简称] 请查 cmux 收件箱：mdelivery', 'foreign_input'),
                ('› ' + notice + ' 用户草稿', 'foreign_input'),
                ('› ' + notice + '\n  用户草稿续行', 'foreign_input')]:
            self.options['screen'] = screen
            self.assertEqual(self.run_wait(expected=2)['status'], expected)
            self.assertEqual(self.sends(), [])
        self.options['screen'] = '› (from original-sender) ' + notice
        self.assertEqual(self.run_wait()['status'], 'notified')
        self.assertEqual([a[-1] for a in self.sends()], ['\\r'])

    def test_empty_box_retry_reuses_stored_short_summary(self):
        data = json.loads(self.record.read_text())
        data.update(summary='最初指定的完整简称', notice='[最初…] 请查 cmux 收件箱：mdelivery')
        self.record.write_text(json.dumps(data))
        self.assertEqual(self.run_wait()['status'], 'notified')
        self.assertEqual(len(self.sends()), 1)
        self.assertIn('[最初…] 请查 cmux 收件箱：mdelivery', self.sends()[0][-1])
        self.assertEqual(json.loads(self.record.read_text())['notice'], data['notice'])

    def test_unsafe_stored_notice_is_never_flushed(self):
        data = json.loads(self.record.read_text())
        data['notice'] = '[核查\\r/quit] 请查 cmux 收件箱：mdelivery'
        self.record.write_text(json.dumps(data))
        self.options['screen'] = '› ' + data['notice']
        self.assertEqual(self.run_wait(expected=2)['status'], 'unknown')
        self.assertEqual(self.sends(), [])
        self.assertEqual(json.loads(self.record.read_text())['state'], 'recorded')

    def test_timeout_requires_explicit_resume_and_does_not_say_delivered(self):
        self.options['screen']='• Working (1m • esc to interrupt)\n› '
        state=self.run_wait('--timeout','.05','--interval','.01',once=False,expected=5)
        self.assertEqual(state['status'],'timed_out')
        self.assertEqual(state['exit_code'], 5)
        self.assertEqual(state['deadline_at'], state['started_at'] + state['timeout'])
        self.assertEqual(json.loads(self.record.read_text())['state'],'recorded')
        self.options['screen']='› '
        self.run_wait(expected=5)
        self.assertEqual(self.run_wait('--resume')['status'],'notified')

    def test_observable_pending_and_delivered_exit_codes(self):
        self.options['screen'] = '› 用户草稿'
        state = self.run_wait(expected=2)
        self.assertEqual(state['exit_code'], 2)
        self.assertFalse(state['delivered'])
        self.assertEqual(state['deadline_at'], state['started_at'] + 1800)
        self.options['screen'] = '› '
        state = self.run_wait()
        self.assertEqual(state['exit_code'], 0)
        self.assertTrue(state['delivered'])

    def test_real_codex_footer_allows_empty_box_and_owned_notice(self):
        footer = ('\n  GPT-6.1-Sol medium · messaging-cmux-sessions · main · Context 39% used · weekly 80% left · 0.162.0'
                  '\n  ← for agents · ? for shortcuts ⚠ 4 warnings · f2 to view')
        for text in ['Ask Codex to do anything', '(from original-sender) 请查 cmux 收件箱：mdelivery']:
            self.options['screen'] = '› ' + text + footer
            self.assertEqual(self.run_wait()['status'], 'notified')
            data = json.loads(self.record.read_text()); data['state'] = 'recorded'
            self.record.write_text(json.dumps(data)); self.state.unlink(); (self.root / 'sent').unlink()
        self.assertEqual(len(self.sends()), 2)
        self.options['screen'] = '› (from original-sender) 请查 cmux 收件箱：mdelivery' + footer + '\n  用户草稿续行'
        self.assertEqual(self.run_wait(expected=2)['status'], 'foreign_input')
        self.assertEqual(len(self.sends()), 2)

    def test_fast_window_footer_only_flushes_owned_notice_when_idle(self):
        footer = ('\n  GPT-6.1-Sol high fast · demo-project · main · Context 38% used · weekly 49% left · 0.163.0 · 475K window'
                  '\n  ← for agents · ? for shortcuts                                                                                   ⚠ 4 warnings · f2 to view')
        notice = '(from original-sender) 请查 cmux 收件箱：mdelivery'
        for prefix, suffix, expected in [
                ('• Working (1m • esc to interrupt)\n', '', 'busy'),
                ('Do you want to proceed?\n1. Yes\n2. No\n', '', 'approval_required'),
                ('', '\n  用户草稿续行', 'foreign_input'),
                ('', '\n  未识别的界面文字', 'foreign_input')]:
            with self.subTest(expected=expected, suffix=suffix):
                self.options['screen'] = prefix + '› ' + notice + footer + suffix
                self.assertEqual(self.run_wait(expected=2)['status'], expected)
                self.assertEqual(self.sends(), [])
        self.options['screen'] = '› ' + notice + footer
        self.assertEqual(self.run_wait()['status'], 'notified')
        self.assertEqual([a[-1] for a in self.sends()], ['\\r'])

    def test_window_footer_accepts_placeholder_without_warning(self):
        self.options['screen'] = ('› Ask Codex to do anything'
            '\n  GPT-6.1-Sol high · demo-project · main · Context 38% used · weekly 49% left · 0.163.0 · 475K window · Main [default]'
            '\n  ← for agents · ? for shortcuts')
        self.assertEqual(self.run_wait()['status'], 'notified')
        self.assertEqual(len(self.sends()), 1)

    def test_unknown_status_suffix_remains_foreign_input(self):
        self.options['screen'] = ('› (from original-sender) 请查 cmux 收件箱：mdelivery'
            '\n  GPT-6.1-Sol high fast · demo-project · main · Context 38% used · weekly 49% left · 0.163.0 · 475K window 用户草稿')
        self.assertEqual(self.run_wait(expected=2)['status'], 'foreign_input')
        self.assertEqual(self.sends(), [])

    def test_main_default_queue_footer_keeps_busy_and_draft_protection(self):
        footer = ('\n  GPT-6.1-Sol medium · demo-project · main · Context 39% used · weekly 80% left · 0.162.0 · Main [default]'
                  '\n  tab to queue message')
        notice = '(from original-sender) 请查 cmux 收件箱：mdelivery'
        self.options['screen'] = '• Working (1m • esc to interrupt)\n› ' + notice + footer
        self.assertEqual(self.run_wait(expected=2)['status'], 'busy')
        self.assertEqual(self.sends(), [])
        self.options['screen'] = '› ' + notice + footer + '\n  用户草稿续行'
        self.assertEqual(self.run_wait(expected=2)['status'], 'foreign_input')
        self.assertEqual(self.sends(), [])
        self.options['screen'] = '› ' + notice + footer
        self.assertEqual(self.run_wait()['status'], 'notified')
        self.assertEqual([a[-1] for a in self.sends()], ['\\r'])

    def test_main_default_queue_footer_accepts_idle_placeholder(self):
        self.options['screen'] = ('› Ask Codex to do anything'
            '\n  GPT-6.1-Sol medium · demo-project · main · Context 39% used · weekly 80% left · 0.162.0 · Main [default]'
            '\n  tab to queue message')
        self.assertEqual(self.run_wait()['status'], 'notified')
        self.assertEqual(len(self.sends()), 1)

    def test_standalone_warning_footer_allows_idle_but_not_draft(self):
        notice = '(from original-sender) 请查 cmux 收件箱：mdelivery'
        footer = '\n  ⚠ 4 warnings · f2 to view'
        self.options['screen'] = '› ' + notice + footer + '\n  用户草稿续行'
        self.assertEqual(self.run_wait(expected=2)['status'], 'foreign_input')
        self.assertEqual(self.sends(), [])
        self.options['screen'] = '› ' + notice + footer
        self.assertEqual(self.run_wait()['status'], 'notified')
        self.assertEqual([a[-1] for a in self.sends()], ['\\r'])

    def test_default_reasoning_footer_keeps_busy_and_draft_protection(self):
        notice = '(from original-sender) 请查 cmux 收件箱：mdelivery'
        footer = '\n  GPT-6.1-Sol default · demo-project · demo-branch · Context 57% used · weekly 77% left · 0.162.0'
        self.options['screen'] = '• Working (1m • esc to interrupt)\n› ' + notice + footer
        self.assertEqual(self.run_wait(expected=2)['status'], 'busy')
        self.assertEqual(self.sends(), [])
        self.options['screen'] = '› ' + notice + footer + '\n  用户草稿续行'
        self.assertEqual(self.run_wait(expected=2)['status'], 'foreign_input')
        self.assertEqual(self.sends(), [])
        self.options['screen'] = '› ' + notice + footer
        self.assertEqual(self.run_wait()['status'], 'notified')

    def test_continuous_wait_rechecks_busy_until_idle_without_new_id(self):
        self.options['screen'] = '• Working (1m • esc to interrupt)\n› (from original-sender) 请查 cmux 收件箱：mdelivery'
        def release():
            for _ in range(100):
                if (self.root / 'calls').exists() and 'read-screen' in (self.root / 'calls').read_text():
                    time.sleep(.1)
                    config = dict(self.options, screen='› (from original-sender) 请查 cmux 收件箱：mdelivery')
                    temp = self.config.with_suffix('.tmp')
                    temp.write_text(json.dumps(config)); temp.replace(self.config)
                    return
                time.sleep(.05)
        thread = threading.Thread(target=release)
        thread.start()
        try:
            self.assertEqual(self.run_wait('--interval', '.1', '--timeout', '8', once=False)['status'], 'notified')
            self.assertEqual([a[-1] for a in self.sends()], ['\\r'])
        finally:
            thread.join(timeout=6)

    def test_second_listener_cannot_monitor_same_id_with_another_state(self):
        lock = self.root / 'messages/delivery-locks/mdelivery.lock'
        lock.parent.mkdir(parents=True)
        self.config.write_text(json.dumps(self.options))
        with lock.open('a') as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            result = subprocess.run([sys.executable, str(SCRIPT), '--id', 'mdelivery', '--state', str(self.state), '--once'],
                                    env=self.env, capture_output=True)
            self.assertEqual(result.returncode, 4)
        self.assertEqual(self.sends(), [])

    def test_sigint_records_stopped_state_and_requires_explicit_resume(self):
        self.options['screen'] = '• Working (1m • esc to interrupt)\n› (from original-sender) 请查 cmux 收件箱：mdelivery'
        self.config.write_text(json.dumps(self.options))
        original = self.record.read_bytes()
        process = subprocess.Popen([sys.executable, str(SCRIPT), '--id', 'mdelivery',
            '--state', str(self.state), '--interval', '30'], env=self.env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            deadline = time.monotonic() + 5
            while not self.state.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.02)
            self.assertTrue(self.state.exists(), '监听未生成首轮状态')
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=5)
            self.assertEqual(process.returncode, 130, stdout + stderr)
            state = json.loads(self.state.read_text())
            self.assertEqual(state['status'], 'stopped')
            self.assertEqual(state['exit_code'], 130)
            self.assertFalse(state['delivered'])
            self.assertGreaterEqual(state['stopped_at'], state['checked_at'])
            self.assertEqual(self.record.read_bytes(), original)
            self.assertEqual(self.sends(), [])
            self.options['screen'] = '› (from original-sender) 请查 cmux 收件箱：mdelivery'
            self.assertEqual(self.run_wait(expected=130)['status'], 'stopped')
            self.assertEqual(self.sends(), [])
            self.assertEqual(self.run_wait('--resume')['status'], 'notified')
            self.assertEqual([a[-1] for a in self.sends()], ['\\r'])
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)

    def test_missing_agent_is_unknown_and_wait_interval_is_bounded(self):
        self.options['screen']='user@host % '
        self.assertEqual(self.run_wait(expected=2)['status'],'unknown')
        self.assertEqual(self.sends(),[])
        self.config.write_text(json.dumps(self.options))
        result=subprocess.run([sys.executable,str(SCRIPT),'--id','mdelivery','--state',str(self.state),'--interval','61','--once'],env=self.env,capture_output=True)
        self.assertNotEqual(result.returncode,0)


if __name__=='__main__':unittest.main()
