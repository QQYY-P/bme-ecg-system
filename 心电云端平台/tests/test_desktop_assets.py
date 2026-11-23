"""The actual desktop HTML assets must ship without exposing account data."""
import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
from server import create_app
from deploy.package_cloud import MEMBERS


class DesktopAssetsTests(unittest.TestCase):
    def test_desktop_enhancement_is_served_and_included_in_cloud_package(self):
        with tempfile.TemporaryDirectory() as directory, TestClient(create_app(directory,auth_enabled=True,cookie_secure=False)) as client:
            page=client.get('/').text
            for name in ('desktop.js','desktop.css','desktop_core.js','connection.js'):
                self.assertIn('/assets/'+name,page)
                self.assertEqual(client.get('/assets/'+name).status_code,200)
                self.assertIn('web/'+name,MEMBERS)
            self.assertEqual(client.get('/api/sessions').status_code,401)
