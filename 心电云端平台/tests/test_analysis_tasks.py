import os
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from server import create_app


class AnalysisTaskTests(unittest.TestCase):
    def setUp(self):
        root = Path(__file__).resolve().parents[1] / '.test_runs'
        root.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        with patch.dict(os.environ, {'ECG_MODEL': 'cardioedge'}):
            self.app = create_app(self.data, token='')
        self.client = TestClient(self.app)
        self.addCleanup(self.client.close)
        self.addCleanup(lambda: self.app.state.jobs.close())
        self.client.put('/api/sessions/task_record', json={'source': 'demo', 'started_utc': '2026-11-23T00:00:00Z'})
        self.client.post('/api/sessions/task_record/complete', json={'chunks': [], 'sample_count': 0, 'source_status': 'complete'})

    def test_queued_job_survives_restart_and_repeated_request_is_idempotent(self):
        with patch.object(self.app.state.jobs, 'start'):
            first = self.client.post('/api/sessions/task_record/analysis-jobs')
            self.assertEqual(first.status_code, 202)
            second = self.client.post('/api/sessions/task_record/analysis-jobs')
            self.assertEqual(first.json()['job_id'], second.json()['job_id'])
            self.assertEqual(self.client.post('/api/sessions/task_record/trash').status_code, 409)
            self.assertEqual(self.client.get('/api/sessions/task_record/analysis').json()['analysis_status'], 'queued')
        other = create_app(self.data, token='')
        try:
            with TestClient(other) as restarted:
                saved = restarted.get('/api/analysis-jobs/' + first.json()['job_id'])
                self.assertEqual(saved.status_code, 200)
                self.assertEqual(saved.json()['session_id'], 'task_record')
        finally: other.state.jobs.close()

    def test_real_child_process_finishes_and_result_is_readable(self):
        started = time.monotonic()
        response = self.client.post('/api/sessions/task_record/analysis-jobs')
        self.assertEqual(response.status_code, 202)
        self.assertLess(time.monotonic()-started, 2)
        job_id = response.json()['job_id']
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            job = self.client.get('/api/analysis-jobs/' + job_id).json()
            if job['status'] not in ('queued', 'running'): break
            time.sleep(.1)
        self.assertEqual(job['status'], 'ineligible', job)
        self.assertNotEqual(job['worker_pid'], os.getpid())
        self.assertEqual(self.client.get('/api/sessions/task_record/analysis').json()['analysis_status'], 'ineligible')

    def test_incomplete_and_trashed_records_cannot_enqueue(self):
        self.client.put('/api/sessions/incomplete', json={'source': 'serial', 'started_utc': '2026-11-23T00:00:00Z'})
        self.assertEqual(self.client.post('/api/sessions/incomplete/analysis-jobs').status_code, 409)
        self.client.post('/api/sessions/task_record/trash')
        self.assertEqual(self.client.post('/api/sessions/task_record/analysis-jobs').status_code, 409)

    def test_stale_running_job_is_recovered_by_new_worker(self):
        with patch.object(self.app.state.jobs, 'start'):
            job = self.client.post('/api/sessions/task_record/analysis-jobs').json()
        with closing(sqlite3.connect(self.data/'cloud.sqlite')) as db, db:
            db.execute("UPDATE analysis_jobs SET status='running',worker_pid=999999 WHERE job_id=?", (job['job_id'],))
        self.app.state.jobs.start()
        deadline = time.monotonic()+25
        while time.monotonic() < deadline:
            state = self.client.get('/api/analysis-jobs/'+job['job_id']).json()
            if state['status'] not in ('queued','running'): break
            time.sleep(.1)
        self.assertEqual(state['status'], 'ineligible', state)
        self.assertNotEqual(state['worker_pid'], 999999)


if __name__ == '__main__': unittest.main()
