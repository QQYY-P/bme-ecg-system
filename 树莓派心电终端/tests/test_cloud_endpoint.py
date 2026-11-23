import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from cloud.service import CloudService
from test_cloud import MemoryCloud


class CloudEndpointTests(unittest.TestCase):
    def test_configured_cloud_url_is_used_by_background_clients(self):
        root=Path(__file__).resolve().parents[1]/'work'/'multi-device'/'tmp'
        root.mkdir(parents=True,exist_ok=True)
        seen=[]
        class Capture(MemoryCloud):
            def __init__(self,url,*a,**kw):seen.append(url);super().__init__(url,*a,**kw)
        with tempfile.TemporaryDirectory(dir=root) as folder,patch.dict(os.environ,{'ECG_CLOUD_URL':'https://cloud.example.test'}):
            service=CloudService(folder,client_factory=Capture,heartbeat_interval=.02)
            try:
                self.assertEqual(service.url,'https://cloud.example.test')
                service.heartbeat_thread.join(.06)
                self.assertIn('https://cloud.example.test',seen)
            finally:
                service.close();service.thread.join(3);service.heartbeat_thread.join(3)

    def test_explicit_url_overrides_environment_for_isolated_testing(self):
        root=Path(__file__).resolve().parents[1]/'work'/'multi-device'/'tmp'
        root.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as folder,patch.dict(os.environ,{'ECG_CLOUD_URL':'https://cloud.example.test'}):
            service=CloudService(folder,url='http://127.0.0.1:1',client_factory=MemoryCloud)
            try:self.assertEqual(service.url,'http://127.0.0.1:1')
            finally:
                service.close();service.thread.join(3);service.heartbeat_thread.join(3)


if __name__=='__main__':unittest.main()
