import os, pathlib, subprocess, tempfile
root=pathlib.Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory() as tmp:
    d=pathlib.Path(tmp)
    mock=d/'cmux'
    mock.write_text('''#!/usr/bin/env python3
import os,sys,pathlib,json
p=pathlib.Path(os.environ['MOCK_DIR'])
cmd=sys.argv[2] if sys.argv[1]=='--json' else sys.argv[1]
if cmd=='tree':
 if os.environ.get('MOCK_MODE')=='deny':
  print('Access denied: only processes started inside cmux',file=sys.stderr);sys.exit(1)
 print(json.dumps({'windows':[{'ref':'window:1','workspaces':[{'id':os.environ.get('MOCK_GEN','gen1'),'title':'group','panes':[{'surfaces':[{'ref':'surface:1','title':'sender','type':'terminal','tty':'t1'},{'ref':'surface:2','title':'target','type':'terminal','tty':'t2'}]}]}]}]}))
elif cmd=='identify': print(json.dumps({'caller':{'surface_ref':os.environ.get('MOCK_SELF','surface:1')}}))
elif cmd=='read-screen':
 mode=os.environ.get('MOCK_MODE','ok')
 if mode=='fail' or (mode=='fail_after' and (p/'sent').exists()): sys.exit(1)
 if mode=='shell': print('user@host % ')
 else:
  print('─'*160)
  print('› '+('draft' if mode=='draft' else ('Ask Codex to do anything' if not (p/'sent').exists() else '')))
elif cmd=='send':
 with (p/'calls').open('a') as f: f.write(repr(sys.argv)+'\\n')
 (p/'sent').touch()
''')
    mock.chmod(0o755)
    env=dict(os.environ,CMUX_BIN=str(mock),MOCK_DIR=tmp,CMUX_MSG_DIR=str(d/'state'),SEND_SETTLE='0')
    cases=[('ok', ['hello'],0),('shell',['hello'],5),('draft',['hello'],3),('fail',['hello'],5),('fail_after',['hello'],3),('ok',['line\nnext'],4),('ok',['--ask','hello'],0)]
    for mode,args,expected in cases:
        for name in ['sent','calls']:
            (d/name).unlink(missing_ok=True)
        r=subprocess.run(['bash',str(root/'scripts/send.sh'),'target',*args],env=dict(env,MOCK_MODE=mode),text=True,capture_output=True)
        assert r.returncode==expected,(mode,r.returncode,r.stdout,r.stderr)
        if expected in (4,5) or mode=='draft': assert not (d/'calls').exists(),mode
        if mode=='fail_after': assert len((d/'calls').read_text().splitlines())==1
        print(f'通过：{mode} {args[0]!r} → {expected}')
    records=list((d/'state/pending').glob('*'))
    assert len(records)==1
    record=records[0].read_text()
    assert 'reply_target=surface:1' in record and str(root/'scripts/send.sh') in record
    lock=d/'state/send-locks/surface_2';lock.mkdir()
    r=subprocess.run(['bash',str(root/'scripts/send.sh'),'target','hello'],env=env,capture_output=True)
    assert r.returncode==4
    print('通过：回复路径、来源编号和目标锁冲突')

    result=subprocess.run(['bash',str(root/'scripts/send.sh'),'--list'],env=dict(env,MOCK_MODE='deny'),capture_output=True,text=True)
    assert result.returncode!=0 and 'Access denied' in result.stderr and '无法连接' in result.stderr
    print('通过：连接被拒返回失败并保留原因，不伪装成空会话列表')

    lock.rmdir()
    for name in ['sent','calls']:
        (d/name).unlink(missing_ok=True)
    command=['bash',str(root/'scripts/send.sh'),'target','--durable','--id','mtest','多行正文\n第二行']
    result=subprocess.run(command,env=env,text=True,errors='replace',capture_output=True)
    assert result.returncode==0,(result.stdout,result.stderr)
    path=d/'state/messages/mtest.json'
    import json
    data=json.loads(path.read_text())
    assert data['state']=='notified' and data['body']=='多行正文\n第二行',(data,result.stdout,result.stderr)
    calls=(d/'calls').read_text()
    assert '[多行正文]' in calls and '第二行' not in calls and 'mtest' in calls
    result=subprocess.run(command,env=env,text=True,errors='replace',capture_output=True)
    assert result.returncode==0 and (d/'calls').read_text()==calls
    for option in ['--complete','--ack']:
        result=subprocess.run(['bash',str(root/'scripts/send.sh'),option,'mtest'],env=env,capture_output=True)
        assert result.returncode!=0
    receiver=dict(env,MOCK_SELF='surface:2')
    for option in ['--ack','--complete']:
        result=subprocess.run(['bash',str(root/'scripts/send.sh'),option,'mtest'],env=receiver,capture_output=True,text=True)
        assert result.returncode==0,result.stderr
    assert json.loads(path.read_text())['state']=='completed'
    bad=subprocess.run(command[:-1]+['不同正文'],env=env,capture_output=True)
    assert bad.returncode!=0
    print('通过：正文落盘、短通知、重复编号幂等、身份校验、接收与完成状态')
    helper=root/'scripts/protocol.py'
    subprocess.run(['python3',str(helper),'busy-write','busy'],env=receiver,check=True)
    state=subprocess.check_output(['python3',str(helper),'busy','surface:2'],env=env,text=True).strip()
    assert state=='busy'
    state=subprocess.check_output(['python3',str(helper),'busy','surface:2'],env=dict(env,MOCK_GEN='gen2'),text=True).strip()
    assert state=='unknown'
    busy_path=d/'state/busy-v2/surface_2.json'
    data=json.loads(busy_path.read_text());data['ts']=0;busy_path.write_text(json.dumps(data))
    state=subprocess.check_output(['python3',str(helper),'busy','surface:2'],env=env,text=True).strip()
    assert state=='unknown'
    print('通过：忙闲会话身份不匹配、过期状态降级 unknown')
