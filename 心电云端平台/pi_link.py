"""One-off SSH hardware demonstration. Password is entered locally and never saved.

The connection only transfers this task's uploader and executes board_stream.py.
The reverse tunnel binds to the Pi loopback address, with no Windows firewall change.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import queue
import re
import select
import shlex
import socket
import sqlite3
import threading
import time
import tkinter as tk
from tkinter import ttk

import paramiko

from protocol import decode_chunk

ROOT=Path(__file__).resolve().parent

# Only allowlisted settings/process fields are returned. No environment or full
# process command lines are read. The optional argument is a numeric PC UTC time.
PREFLIGHT_SCRIPT = r'''import ast,hashlib,json,os,platform,sqlite3,subprocess,sys,time
from contextlib import closing
from datetime import datetime,timezone
from pathlib import Path
began=time.monotonic()
home=Path.home(); terminal=home/'树莓派心电终端'
devices=sorted({str(p.resolve()) for pattern in ('ttyUSB*','ttyACM*') for p in Path('/dev').glob(pattern)})
owners=[]; processes=[]
for proc in Path('/proc').iterdir():
    if not proc.name.isdigit(): continue
    try:
        name=(proc/'comm').read_text().strip()
        cwd=str((proc/'cwd').resolve())
        held=[]
        try:
            for fd in (proc/'fd').iterdir():
                try:
                    device=str(fd.resolve())
                    if device in devices: held.append(device)
                except OSError: pass
        except OSError: pass
        if held: owners.append({'pid':int(proc.name),'name':name,'devices':sorted(set(held))})
        if ('python' in name.lower() and int(proc.name)!=os.getpid()) or held:
            processes.append({'pid':int(proc.name),'name':name,'working_directory':cwd})
    except OSError: pass
recordings=[]; scan_errors=[]
roots=[terminal/'data/sessions']+[p/'data/sessions' for p in home.glob('ecg-cloud-upload-*') if p.is_dir()]
for root in roots:
    for path in root.glob('*/index.sqlite'):
        try:
            with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True,timeout=1)) as db:
                for sid,status,count in db.execute('SELECT session_id,status,sample_count FROM sessions'):
                    if status=='recording': recordings.append({'session_id':sid,'status':status,'sample_count':count,'directory':str(path.parent)})
        except (OSError,sqlite3.Error) as exc: scan_errors.append({'directory':str(path.parent),'error_type':type(exc).__name__})
settings={}; settings_origin='unavailable'
source_hashes={}
for relative in ('app.py','controller.py','recording.py','acquisition.py','settings_store.py',
                 'time_sync.py','cardioedge/stream.py','cardioedge/numpy_candidate.py','cardioedge/portable_weights.npz'):
    path=terminal/relative
    if path.is_file(): source_hashes[relative]=hashlib.sha256(path.read_bytes()).hexdigest()
settings_path=terminal/'data/settings.json'
try:
    tree=ast.parse((terminal/'settings_store.py').read_text())
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='DEFAULTS' for t in node.targets):
            body=ast.literal_eval(node.value)
            settings={k:body[k] for k in ('classification_enabled','vref','auto_time_sync') if k in body}
            settings_origin='source_defaults'
except (OSError,ValueError,SyntaxError): pass
try:
    body=json.loads(settings_path.read_text())
    settings.update({k:body[k] for k in ('classification_enabled','vref','auto_time_sync') if k in body})
    settings_origin='saved_settings_over_source_defaults'
except (OSError,ValueError): pass
def command(args):
    try:
        value=subprocess.run(args,capture_output=True,text=True,timeout=5)
        return value.returncode,value.stdout
    except (OSError,subprocess.TimeoutExpired): return -1,''
code,ntp=command(['timedatectl','show','-p','NTP','-p','NTPSynchronized','-p','CanNTP'])
ntp={line.split('=',1)[0]:line.split('=',1)[1] for line in ntp.splitlines() if '=' in line}
sudo_ok=command(['sudo','-n','true'])[0]==0
safe=sudo_ok and not owners and not recordings and not scan_errors
calibration={'requested':len(sys.argv)>1,'performed':False}
if len(sys.argv)>1:
    if safe:
        pc_reference=float(sys.argv[1])
        if ntp.get('CanNTP')=='yes': calibration['ntp_enable_exit_code']=command(['sudo','-n','timedatectl','set-ntp','true'])[0]
        target=pc_reference+(time.monotonic()-began)
        calibration['date_exit_code']=command(['sudo','-n','date','-u','-s','@'+str(target)])[0]
        calibration['performed']=calibration['date_exit_code']==0
        calibration['method']='explicit_pc_utc_before_capture'
    else: calibration['reason']='active_capture_or_serial_owner_or_incomplete_scan_or_no_sudo'
result={'home':str(home),'host':platform.node(),'architecture':platform.machine(),'python':platform.python_version(),
 'usb_serial':devices,'usb_owners':owners,'recording_sessions':recordings,'session_scan_errors':scan_errors,
 'terminal_settings':settings,'terminal_settings_origin':settings_origin,'terminal_source_sha256':source_hashes,
 'terminal_runtime_model_state':'not_inferred_from_settings','terminal_processes':processes,'ntp':ntp,'ntp_query_exit_code':code,
 'sudo_n_available':sudo_ok,'can_calibrate_time':safe,'calibration':calibration,
 'pi_utc':datetime.now(timezone.utc).isoformat(),'pi_unix_seconds':time.time(),'probe_elapsed_seconds':time.monotonic()-began}
print(json.dumps(result,ensure_ascii=False))
'''


class BoardLink:
    def __init__(self, duration=180, destination_port=8766, inspect_first=False, test_upload_pause=False):
        self.duration=duration
        self.destination_port=destination_port
        self.events=queue.Queue()
        self.stop=threading.Event()
        self.client=None
        self.remote=None
        self.thread=None
        self.inspect_first=inspect_first or destination_port==8767
        self.test_upload_pause=test_upload_pause
        self.decisions=queue.Queue()
        self.capturing=False
        self.report_lock=threading.Lock()
        self.upload_paused=threading.Event()
        self.pause_started=False
        self.outage_lock=threading.Lock()
        self.outage={'kind':'local_forwarding_pause','scope':'this_run_only','physical_network_disconnected':False,
                     'requested':test_upload_pause,'requested_pause_seconds':15,'retry_events':[],
                     'recovered':False,'latest_source_samples':0}
        cloud=destination_port==8767
        self.run_id=datetime.now().strftime('%Y%m%d_%H%M%S_%f' if cloud else '%Y%m%d_%H%M%S')
        self.evidence=(ROOT/'evidence'/'cloud'/'pi-link'/self.run_id if cloud else ROOT/'evidence'/'pi-link')
        self.evidence.mkdir(parents=True,exist_ok=not cloud)

    def report(self, stage, **fields):
        message={'stage':stage,'time':datetime.now().astimezone().isoformat(),**fields}
        with self.report_lock:
            (self.evidence/'status.json').write_text(json.dumps(message,ensure_ascii=False,indent=2),encoding='utf-8')
            with (self.evidence/f'{self.run_id}.jsonl').open('a',encoding='utf-8') as out:
                out.write(json.dumps(message,ensure_ascii=False)+'\n')
        self.events.put(message)

    def preflight(self, filename='preflight.json', calibrate=False):
        before=time.time()
        command='python3 -c '+shlex.quote(PREFLIGHT_SCRIPT)
        if calibrate: command+=' '+shlex.quote(str(before))
        _,output,errors=self.client.exec_command(command,timeout=40)
        raw=output.read(); errors.read()
        if output.channel.recv_exit_status()!=0:
            raise RuntimeError('树莓派预检未完成，未启动采集或修改终端')
        after=time.time()
        result=json.loads(raw.decode('utf-8'))
        uncertainty=max(0,after-before-result['probe_elapsed_seconds'])/2
        result.update(pc_utc=datetime.fromtimestamp(after,timezone.utc).isoformat(),
                      pc_round_trip_seconds=after-before,clock_uncertainty_seconds=uncertainty,
                      clock_offset_seconds=result['pi_unix_seconds']-(after-uncertainty),
                      clock_reference='computer',clock_checked_at=datetime.fromtimestamp(after,timezone.utc).isoformat())
        (self.evidence/filename).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        return result

    def await_capture_decision(self, metadata):
        self.report('awaiting_capture',message=f"预检完成：树莓派与电脑相差 {metadata['clock_offset_seconds']:+.1f} 秒。等待确认后继续。",
                    can_calibrate_time=metadata['can_calibrate_time'],preflight_file=str(self.evidence/'preflight.json'),
                    control_file=str(self.evidence/'control.json'),run_id=self.run_id)
        while not self.stop.is_set():
            try: action=self.decisions.get(timeout=.3)
            except queue.Empty:
                control=self.evidence/'control.json'
                if not control.is_file(): continue
                try:
                    request=json.loads(control.read_text(encoding='utf-8'))
                    action=request['action'] if request.get('run_id')==self.run_id else 'invalid'
                    control.rename(self.evidence/('control-received-'+datetime.now().strftime('%H%M%S_%f')+'.json'))
                except (OSError,ValueError,KeyError): continue
            if action=='cancel': self.stop.set(); break
            if action not in ('continue','calibrate_then_continue'):
                self.report('awaiting_capture',message='控制动作无效，仍在等待确认。',can_calibrate_time=metadata['can_calibrate_time'])
                continue
            self.report('decision',action=action,message='正在重新核对采集与时间状态…')
            metadata=self.preflight('preflight-before-capture.json',calibrate=action=='calibrate_then_continue')
            if action=='calibrate_then_continue' and not metadata['calibration']['performed']:
                self.report('awaiting_capture',message='校时未执行：可能存在采集、串口占用、检查不完整或权限不足。可保持原时间继续。',can_calibrate_time=False)
                continue
            if action=='calibrate_then_continue':
                calibration=metadata['calibration']
                metadata=self.preflight('preflight-after-clock.json')
                metadata['calibration']=calibration
            return metadata
        return None

    def outage_update(self, **fields):
        with self.outage_lock:
            self.outage.update(fields)
            (self.evidence/'outage-test.json').write_text(json.dumps(self.outage,ensure_ascii=False,indent=2),encoding='utf-8')

    def pause_upload_once(self):
        if self.stop.wait(30): return
        self.upload_paused.set()
        self.outage_update(started_at=datetime.now(timezone.utc).isoformat(),
                           source_samples_before_pause=self.outage['latest_source_samples'])
        self.report('upload_pause_started',message='仅暂停本次上传通道15秒，采集与SSH连接继续。')
        interrupted=self.stop.wait(15)
        self.upload_paused.clear()
        self.outage_update(ended_at=datetime.now(timezone.utc).isoformat(),interrupted=interrupted)
        self.report('upload_pause_ended',message='本次上传通道已恢复，等待上传器补齐缺块。')

    def observe_remote(self, detail):
        event=detail.get('event')
        if self.test_upload_pause and event in ('capture_started','attached') and not self.pause_started:
            self.pause_started=True
            threading.Thread(target=self.pause_upload_once,daemon=True).start()
        fields={}
        if 'source_saved_samples' in detail: fields['latest_source_samples']=detail['source_saved_samples']
        if self.test_upload_pause and event=='retry':
            with self.outage_lock:
                self.outage['retry_events'].append({'pc_utc':datetime.now(timezone.utc).isoformat(),
                    'during_pause':self.upload_paused.is_set(),'source_saved_samples':detail.get('source_saved_samples'),
                    'capture_elapsed_seconds':detail.get('capture_elapsed_seconds')})
        if (event in ('uploaded','final_upload') and 'ended_at' in self.outage
                and self.outage['retry_events'] and not self.outage['recovered']):
            fields.update(recovered=True,recovered_at=datetime.now(timezone.utc).isoformat(),
                          source_samples_after_recovery=detail.get('source_saved_samples'),
                          confirmed_samples_after_recovery=detail.get('confirmed_samples'))
        if self.test_upload_pause: self.outage_update(**fields)

    def pipe_channel(self,channel):
        sock=None
        try:
            if self.upload_paused.is_set(): return
            sock=socket.create_connection(('127.0.0.1',self.destination_port),timeout=5)
            sock.settimeout(10)
            while not self.stop.is_set():
                if self.upload_paused.is_set(): break
                readable,_,_=select.select([sock,channel],[],[],1)
                if sock in readable:
                    data=sock.recv(32768)
                    if not data:break
                    channel.sendall(data)
                if channel in readable:
                    data=channel.recv(32768)
                    if not data:break
                    sock.sendall(data)
        except (OSError,EOFError):
            pass
        finally:
            channel.close()
            if sock:sock.close()

    def forwarding(self,transport):
        while transport.is_active() and not self.stop.is_set():
            channel=transport.accept(1)
            if channel:
                threading.Thread(target=self.pipe_channel,args=(channel,),daemon=True).start()

    def cancel(self):
        # Request a graceful recorder shutdown before closing the forwarding path.
        if not self.capturing:
            self.stop.set()
            self.decisions.put('cancel')
            return
        if self.client and self.remote:
            try:
                with self.client.open_sftp() as sftp:
                    with sftp.file(self.remote+'/STOP','w') as out:
                        out.write('stop\n')
            except Exception:
                pass

    def download_source(self, sftp):
        """Keep a consistent source index and byte-verified blocks for this cloud run."""
        board=json.loads((self.evidence/'board-result.json').read_text(encoding='utf-8'))
        sid=board['session_id']
        if not isinstance(sid,str) or not re.fullmatch(r'[A-Za-z0-9_][A-Za-z0-9_.-]{0,119}',sid):
            raise ValueError('源会话编号不符合约定，未下载')
        home=str(PurePosixPath(self.remote).parent)
        source=board['directory']
        if source not in (self.remote+'/data/sessions/'+sid, home+'/树莓派心电终端/data/sessions/'+sid):
            raise ValueError('源会话目录不属于本次采集或原终端，未下载')
        destination=ROOT/'cloud_sources'/self.run_id/sid
        destination.mkdir(parents=True,exist_ok=False)
        (destination/'chunks').mkdir()
        record={'session_id':sid,'remote_source_directory':source,'local_source_directory':str(destination),
                'source_status_in_board_report':board['status'],'board_manifest':board['manifest'],
                'download_complete':False,'all_file_hashes_match':False,
                'snapshot_method':'SQLite mode=ro backup; source status is preserved, never changed to complete.'}
        record_path=self.evidence/'source-copy.json'
        def save_record():
            record_path.write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
        save_record()
        self.report('source_copy',message='正在保存树莓派源记录及只读索引快照，供逐点核对…')
        remote_snapshot=self.remote+'/source-snapshot'
        # A SQLite backup includes committed WAL contents without changing the recorder.
        # The snapshot is created only inside this run's new upload directory.
        script='''import hashlib,json,sqlite3,sys,time
from contextlib import closing
from pathlib import Path
source,target=map(Path,sys.argv[1:])
target.mkdir(mode=0o700)
deadline=time.monotonic()+20
def progress(*unused):
    if time.monotonic()>deadline: raise TimeoutError('Source snapshot timed out')
with closing(sqlite3.connect(source.joinpath('index.sqlite').resolve().as_uri()+'?mode=ro',uri=True,timeout=3)) as src:
    with closing(sqlite3.connect(target/'index.sqlite')) as dst:
        src.backup(dst,pages=64,progress=progress,sleep=.1)
        if dst.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('Invalid source index')
        rows=dst.execute('SELECT session_id,status,sample_count FROM sessions').fetchall()
        if len(rows)!=1: raise ValueError('Ambiguous source session')
        sid,status,total=rows[0]
        descriptors=[json.loads(row[0]) for row in dst.execute('SELECT descriptor_json FROM chunks ORDER BY chunk_id')]
(target/'session.json').write_bytes((source/'session.json').read_bytes())
manifest=[{key:d[key] for key in ('chunk_id','sha256','sample_count')} for d in descriptors]
files={name:hashlib.sha256((target/name).read_bytes()).hexdigest() for name in ('index.sqlite','session.json')}
for d in descriptors if len(descriptors)<=300 else []:
    cid=d['chunk_id']
    if type(cid) is not int or not 0<=cid<=99999999: raise ValueError('Invalid chunk ID')
    for suffix in ('.bin','.json'):
        name=f'chunks/{cid:08d}{suffix}'
        digest=hashlib.sha256((source/name).read_bytes()).hexdigest()
        if suffix=='.bin' and digest!=d['sha256']: raise ValueError('Source block hash mismatch')
        files[name]=digest
report={'session_id':sid,'source_status':status,'sample_count':total,'manifest':manifest,'files':files,
        'snapshot_method':'sqlite_readonly_backup','within_chunk_limit':len(descriptors)<=300}
(target/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
'''
        _,output,errors=self.client.exec_command('python3 -c '+shlex.quote(script)+' '+shlex.quote(source)+' '+shlex.quote(remote_snapshot),timeout=40)
        output.read()
        errors.read()
        if output.channel.recv_exit_status()!=0:
            raise RuntimeError('源索引快照未完成；清单和快照方案已保留，原始采集不受影响')
        manifest_path=destination/'source-manifest.json'
        sftp.get(remote_snapshot+'/manifest.json',str(manifest_path))
        snapshot=json.loads(manifest_path.read_text(encoding='utf-8'))
        record.update(source_status=snapshot['source_status'],snapshot_manifest=snapshot['manifest'])
        save_record()
        if snapshot['session_id']!=sid or not snapshot['within_chunk_limit']:
            raise ValueError('源会话不匹配或超过300块；已保留清单和只读快照，未标记下载完成')
        expected=board['manifest']
        if snapshot['manifest'][:len(expected)]!=expected:
            raise ValueError('源快照与采集报告清单不一致，未标记下载完成')
        if board['status']=='complete' and (snapshot['source_status']!='complete' or snapshot['manifest']!=expected
                                          or snapshot['sample_count']!=board['sample_count']):
            raise ValueError('已完成源记录在快照时发生变化，未标记下载完成')
        allowed={'index.sqlite','session.json'}
        for d in snapshot['manifest']:
            cid=d['chunk_id']
            if type(cid) is not int or not 0<=cid<=99999999:
                raise ValueError('源清单块编号无效')
            allowed.update((f'chunks/{cid:08d}.bin',f'chunks/{cid:08d}.json'))
        if set(snapshot['files'])!=allowed:
            raise ValueError('源快照文件清单不完整')
        for name,digest in snapshot['files'].items():
            remote=(remote_snapshot if name in ('index.sqlite','session.json') else source)+'/'+name
            path=destination/name
            sftp.get(remote,str(path))
            if hashlib.sha256(path.read_bytes()).hexdigest()!=digest:
                raise ValueError('下载源文件SHA-256不一致，文件已保留供检查')
        with closing(sqlite3.connect((destination/'index.sqlite').resolve().as_uri()+'?mode=ro',uri=True)) as db:
            if db.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                raise ValueError('下载的源索引完整性检查失败')
            rows=db.execute('SELECT session_id,status,sample_count FROM sessions').fetchall()
            descriptors=[json.loads(r[0]) for r in db.execute('SELECT descriptor_json FROM chunks ORDER BY chunk_id')]
        if rows!=[(sid,snapshot['source_status'],snapshot['sample_count'])] or [
                {key:d[key] for key in ('chunk_id','sha256','sample_count')} for d in descriptors]!=snapshot['manifest']:
            raise ValueError('下载的源索引与快照清单不一致')
        total=0
        for d in descriptors:
            base=destination/'chunks'/f'{d["chunk_id"]:08d}'
            if json.loads(base.with_suffix('.json').read_text(encoding='utf-8'))!=d:
                raise ValueError('源块描述文件与索引不一致')
            samples=decode_chunk(base.with_suffix('.bin').read_bytes(),d['sha256'])
            if len(samples)!=d['sample_count']:
                raise ValueError('源块采样数与索引不一致')
            total+=len(samples)
        if total!=snapshot['sample_count']:
            raise ValueError('源快照总采样数与索引不一致')
        record.update(download_complete=True,all_file_hashes_match=True,
                      complete_recording=snapshot['source_status']=='complete',samples=total,
                      note='源仍在录制时仅保存截至快照的已提交数据，保留源状态；这不是完整采集验收。')
        save_record()
        self.report('source_saved',message='源记录快照已保存并核对哈希；云端逐点核对需另行运行。',
                    source_directory=str(destination),source_status=snapshot['source_status'])

    def run(self,password):
        client=paramiko.SSHClient()
        self.client=client
        try:
            client.load_host_keys(str(Path.home()/'.ssh/known_hosts'))
            client.set_missing_host_key_policy(paramiko.RejectPolicy())
            self.report('connecting',message='正在连接树莓派…')
            client.connect('192.168.137.59',username='pi2',password=password,
                           allow_agent=False,look_for_keys=False,timeout=8,auth_timeout=20,banner_timeout=10)
            password=None
            client.get_transport().set_keepalive(15)
            self.report('connected',message='登录成功，正在只读预检时间、串口和原终端状态…')
            metadata=self.preflight()
            if self.inspect_first:
                metadata=self.await_capture_decision(metadata)
            if metadata is None or self.stop.is_set(): return
            offset=metadata['clock_offset_seconds']
            certain=metadata['clock_uncertainty_seconds']<=5
            provenance={'kind':'live','clock_status':('ok' if abs(offset)<=5 else 'offset') if certain else 'unknown',
                        'clock_offset_seconds':offset if certain else None,
                        'clock_reference':'computer' if certain else 'unknown',
                        'clock_checked_at':metadata['clock_checked_at'] if certain else None,
                        'origin_device':metadata['host'][:96],
                        'note':'本次新采，经电脑中转；独立采集器不运行模型。'+
                               ('仅暂停本次上传通道15秒以验证补传，不改变物理网络、SSH或原终端。' if self.test_upload_pause else '')}
            (self.evidence/'run-provenance.json').write_text(json.dumps(provenance,ensure_ascii=False,indent=2),encoding='utf-8')
            self.remote=metadata['home']+'/ecg-cloud-upload-'+self.run_id
            with client.open_sftp() as sftp:
                sftp.mkdir(self.remote,mode=0o700)
                content=(ROOT/'uploader.py').read_text(encoding='utf-8').replace('from protocol import decode_chunk','from cloud_protocol import decode_chunk')
                for name,body in [('uploader.py',content),('cloud_protocol.py',(ROOT/'protocol.py').read_text(encoding='utf-8')),
                                  ('board_stream.py',(ROOT/'board_stream.py').read_text(encoding='utf-8')),
                                  ('run-provenance.json',json.dumps(provenance,ensure_ascii=False))]:
                    with sftp.file(self.remote+'/'+name,'w') as target:target.write(body.encode('utf-8'))
            transport=client.get_transport()
            transport.set_keepalive(15)
            port=transport.request_port_forward('127.0.0.1',0)
            threading.Thread(target=self.forwarding,args=(transport,),daemon=True).start()
            command=['python3','-u',self.remote+'/board_stream.py','--terminal',metadata['home']+'/树莓派心电终端',
                     '--data-dir',self.remote+'/data','--url',f'http://127.0.0.1:{port}',
                     '--duration',str(self.duration),'--stop-file',self.remote+'/STOP',
                     '--provenance-file',self.remote+'/run-provenance.json']
            self.capturing=True
            self.report('running',message='正在接入串口并上传；独立采集器不运行模型，云端模型状态见网页。',remote_directory=self.remote,**metadata)
            _,output,errors=client.exec_command(shlex.join(command),timeout=None)
            for line in output:
                try:
                    detail=json.loads(line)
                    self.observe_remote(detail)
                    self.report('remote',detail=detail)
                except ValueError:
                    self.report('remote_text',message=line.strip())
            error_text=errors.read().decode('utf-8',errors='replace')[-3000:]
            code=output.channel.recv_exit_status()
            with client.open_sftp() as sftp:
                try:
                    sftp.get(self.remote+'/board-result.json',str(self.evidence/'board-result.json'))
                except OSError:
                    if self.destination_port==8767:
                        raise RuntimeError('采集结果清单未能下载，未进行源记录完整性验证') from None
                if self.destination_port==8767:
                    self.download_source(sftp)
            self.report('finished' if code==0 else 'failed',exit_code=code,message='本次验证结束，数据已保留' if code==0 else '设备接入未完成，请查看验证日志',error=error_text)
        except paramiko.AuthenticationException:
            self.report('failed',message='树莓派登录未通过，请核对密码后重新运行；密码没有保存')
        except Exception as exc:
            self.report('failed',message=str(exc))
        finally:
            password=None
            self.stop.set()
            self.upload_paused.clear()
            client.close()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--duration',type=int,default=180)
    p.add_argument('--destination-port',type=int,choices=(8766,8767),default=8766,
                   help='8766=电脑服务，8767=已连接的云端SSH通道')
    p.add_argument('--inspect-first','--preflight-only',dest='inspect_first',action='store_true',
                   help='先预检并保持SSH连接，等待确认后才开始采集（云端模式默认启用）')
    p.add_argument('--test-upload-pause',action='store_true',help='本次采集30秒后暂停此上传通道15秒，验证补传')
    args=p.parse_args()
    if args.test_upload_pause and args.duration<60: p.error('补传演示至少需要60秒采集时间')
    link=BoardLink(args.duration,args.destination_port,args.inspect_first,args.test_upload_pause)
    cloud=args.destination_port==8767
    root=tk.Tk()
    root.title('连接树莓派 · '+('上传云端' if cloud else '心电数据上传'))
    root.geometry('680x420')
    root.resizable(False,False)
    root.attributes('-topmost',True)
    main=ttk.Frame(root,padding=25);main.pack(fill='both',expand=True)
    ttk.Label(main,text='开发板 → '+('云端网页' if cloud else '电脑网页'),font=('Microsoft YaHei',17,'bold')).pack(anchor='w')
    ttk.Label(main,text='树莓派 pi2@192.168.137.59 · 原终端保持独立，云端模型状态见网页').pack(anchor='w',pady=(10,18))
    ttk.Label(main,text='请在此输入树莓派登录密码（不会写入文件或聊天）').pack(anchor='w')
    value=tk.StringVar()
    entry=ttk.Entry(main,textvariable=value,show='●',width=50);entry.pack(fill='x',pady=8)
    status=tk.StringVar(value=('请先连接云服务器。' if cloud else '')+
                       ('登录后只做预检，确认后才开始采集。' if link.inspect_first else '登录后开始采集上传。'))
    ttk.Label(main,textvariable=status,wraplength=625).pack(anchor='w',pady=12)
    buttons=ttk.Frame(main);buttons.pack(fill='x')
    def login():
        if link.thread and link.thread.is_alive(): return
        secret=value.get()
        if not secret:status.set('请先输入密码');return
        value.set('');entry.configure(state='disabled');start.configure(state='disabled')
        root.attributes('-topmost',False)
        link.thread=threading.Thread(target=link.run,args=(secret,),daemon=True)
        link.thread.start()
    start=ttk.Button(buttons,text='连接并预检' if link.inspect_first else '连接并上传',command=login);start.pack(side='left')
    decisions=ttk.Frame(main);decisions.pack(fill='x',pady=15)
    def decide(action):
        keep.configure(state='disabled');calibrate.configure(state='disabled')
        link.decisions.put(action)
        status.set('正在按已选操作重新核对设备状态…')
    calibrate=ttk.Button(decisions,text='校准时间并继续',state='disabled',command=lambda:decide('calibrate_then_continue'))
    calibrate.pack(side='left')
    keep=ttk.Button(decisions,text='保持时间继续',state='disabled',command=lambda:decide('continue'))
    keep.pack(side='left',padx=10)
    def close():
        if link.thread and link.thread.is_alive():
            status.set('正在结束本次采集并保存，请稍候…')
            threading.Thread(target=link.cancel,daemon=True).start()
        else:root.destroy()
    ttk.Button(buttons,text='停止 / 关闭',command=close).pack(side='right')
    root.protocol('WM_DELETE_WINDOW',close)
    entry.bind('<Return>',lambda _:login());entry.focus_set()
    def poll():
        while not link.events.empty():
            item=link.events.get_nowait()
            if item['stage']=='awaiting_capture':
                keep.configure(state='normal')
                calibrate.configure(state='normal' if item.get('can_calibrate_time') else 'disabled')
            detail=item.get('detail',{})
            if detail.get('event') in ('uploaded','final_upload'):
                status.set(f"已上传 {detail.get('confirmed_samples',0):,} 点 · 云端模型状态见网页")
            elif detail.get('event')=='error':status.set(detail.get('message','设备接入失败'))
            elif item.get('message'):status.set(item['message'])
        root.after(250,poll)
    link.report('awaiting_password',message='等待用户在本机窗口输入树莓派密码')
    poll();root.mainloop()


if __name__=='__main__':main()
