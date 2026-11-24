"""Local, synthetic multi-device/API benchmark; never evidence of server capacity."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
import base64
import hashlib
import json
import math
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from fastapi.testclient import TestClient
from protocol import HEADER, ROWS
from server import create_app


def percentile(values, fraction):
    return sorted(values)[max(0, math.ceil(len(values)*fraction)-1)] if values else None


def run():
    """3 uploading identities, 2 readers, 60 s each; temporary data deleted on exit."""
    (ROOT/'.test_runs').mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=ROOT/'.test_runs') as directory:
        app = create_app(directory, auth_enabled=True, cookie_secure=False)
        stamp = datetime.now(timezone.utc).isoformat()
        users = [app.state.auth.create_user('bench'+str(i), 'Synthetic-benchmark-123!') for i in range(2)]
        keys = [app.state.devices.create('bench-pi-'+str(i), 'Synthetic device '+str(i),
                [users[i%2]['id']])['credentials']['token'] for i in range(3)]
        timings = {'upload_request_ms': [], 'read_request_ms': [], 'enqueue_request_ms': []}
        errors = []
        def request(client, method, url, metric, **kwargs):
            start = time.perf_counter()
            response = getattr(client,method)(url,**kwargs)
            timings[metric].append((time.perf_counter()-start)*1000)
            if not response.is_success:
                errors.append({'path':url,'status':response.status_code})
                raise RuntimeError('Synthetic benchmark request failed: '+url)
            return response.json()
        def upload(i):
            sid = 'synthetic_'+str(i)
            with closing(TestClient(app)) as client:
                client.headers['X-API-Key'] = keys[i]
                request(client,'put','/api/sessions/'+sid,'upload_request_ms',json={
                    'source':'serial','owner_id':users[i%2]['id'],'started_utc':stamp})
                manifest = []
                for chunk in range(6):
                    start = chunk*3600
                    rows = []
                    for sample in range(start,start+3600):
                        t = sample/360
                        value = int(2048 + 15*math.sin(2*math.pi*.7*t) + 350*math.exp(-.5*((t-round(t))/.025)**2))
                        rows.append(ROWS[2].pack(sample,0,sample,sample%256,value,0,0))
                    raw = HEADER.pack(b'ECGRAW01',2,ROWS[2].size,360,3600)+b''.join(rows)
                    digest = hashlib.sha256(raw).hexdigest()
                    request(client,'put',f'/api/sessions/{sid}/chunks/{chunk}','upload_request_ms',json={
                        'payload_base64':base64.b64encode(raw).decode(),'sha256':digest,'sample_count':3600,
                        'first_sample_id':start,'last_sample_id':start+3599,
                        'received_first_utc':stamp,'received_last_utc':stamp})
                    manifest.append({'chunk_id':chunk,'sha256':digest,'sample_count':3600})
                request(client,'post','/api/sessions/'+sid+'/complete','upload_request_ms',json={
                    'chunks':manifest,'sample_count':21600,'source_status':'complete'})
            return sid
        # Only this outer lifespan controls the shared worker; client contexts
        # below are avoided during analysis so one reader cannot stop it.
        with TestClient(app):
            started = time.perf_counter()
            with ThreadPoolExecutor(max_workers=3) as pool:
                sessions = list(pool.map(upload,range(3)))
            upload_seconds = time.perf_counter()-started
            clients = [TestClient(app) for _ in users]
            try:
                for i,client in enumerate(clients):
                    reply = client.post('/api/auth/login',json={'username':'bench'+str(i),'password':'Synthetic-benchmark-123!'}).json()
                    client.headers['X-CSRF-Token'] = reply['csrf_token']
                def reader(i):
                    client = clients[i]
                    for _ in range(20):
                        items = request(client,'get','/api/sessions','read_request_ms')['sessions']
                        for item in items:
                            if item['owner_id'] != users[i]['id']: raise AssertionError('Account isolation failed')
                            request(client,'get','/api/sessions/'+item['session_id']+'/physiology','read_request_ms')
                        time.sleep(.02)
                jobs = []
                started = time.perf_counter()
                for i,sid in enumerate(sessions):
                    jobs.append(request(clients[i%2],'post','/api/sessions/'+sid+'/physiology-jobs','enqueue_request_ms'))
                # Each reader owns its own HTTP client; poll jobs through SQLite.
                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(reader,range(2)))
                deadline = started+60
                while time.perf_counter() < deadline:
                    states = [app.state.jobs.get(j['job_id']) for j in jobs]
                    if all(j['status'] not in ('queued','running') for j in states): break
                    time.sleep(.1)
                if any(j['status']!='complete' for j in states): raise AssertionError(states)
                results = [clients[i%2].get('/api/sessions/'+sid+'/physiology').json() for i,sid in enumerate(sessions)]
                return {'scope':'local TestClient, SQLite and one spawned worker; synthetic mode-0 samples; no real devices, HTTP transport, TLS or production server',
                    'devices':3,'readers':2,'seconds_per_record':60,'samples_total':64800,'errors':errors,
                    'upload_seconds':upload_seconds,'analysis_batch_seconds':time.perf_counter()-started,
                    'requests':{name:{'count':len(v),'p50':percentile(v,.5),'p95':percentile(v,.95),'max':max(v)} for name,v in timings.items()},
                    'records':[{'qrs':r['summary']['qrs_count'],'rr_mean_ms':r['rr']['mean_ms'],
                                'processing_seconds':r['performance']['processing_seconds']} for r in results]}
            finally:
                for client in clients: client.close()


if __name__ == '__main__': print(json.dumps(run(),ensure_ascii=False,indent=2))
