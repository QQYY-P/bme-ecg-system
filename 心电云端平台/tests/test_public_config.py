import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from deploy.prepare_public import render
from server import create_app


class PublicConfigTests(unittest.TestCase):
    def test_only_canonical_public_ipv4_and_loopback_upstream(self):
        for bad in ('127.0.0.1','192.168.137.59','::1','8.8.8.8; return 200;','example.com'):
            with self.assertRaises(ValueError):render(bad)
        config=render('62.234.150.104')
        self.assertIn('proxy_pass http://127.0.0.1:8766',config['nginx-https.conf'])
        self.assertNotIn('proxy_pass',config['nginx-bootstrap.conf'])
        self.assertIn('ECG_AUTH_REQUIRED=1',config['public-access.conf'])
        self.assertIn('ECG_COOKIE_SECURE=1',config['public-access.conf'])

    def test_public_mode_refuses_shared_login_insecure_cookie_or_no_admin(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'.test_runs') as folder:
            with patch.dict(os.environ,{'ECG_PUBLIC_ACCESS':'1'}):
                for auth,secure in ((False,True),(True,False),(True,True)):
                    with self.assertRaises(ValueError):create_app(folder,auth_enabled=auth,cookie_secure=secure)
