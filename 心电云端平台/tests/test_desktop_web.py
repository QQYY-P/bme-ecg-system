from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient
from server import create_app


class DesktopDownloadTests(unittest.TestCase):
    def test_public_download_allowlist_does_not_expose_account_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            app=create_app(root/'data',auth_enabled=True,cookie_secure=False)
            downloads=root/'deploy/dist/desktop'
            downloads.mkdir(parents=True)
            (downloads/'心电工作台-Windows免安装.zip').write_bytes(b'PK-test-package')
            (downloads/'心电工作台-Windows免安装.zip.sha256').write_text('test digest')
            with TestClient(app) as client,patch('server.ROOT',root):
                response=client.get('/downloads/ecg-workspace-windows.zip')
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.content,b'PK-test-package')
                self.assertIn('attachment',response.headers['content-disposition'])
                self.assertEqual(client.get('/downloads/ecg-workspace-windows.zip.sha256').status_code,200)
                self.assertEqual(client.get('/downloads/cloud.sqlite').status_code,404)
                self.assertEqual(client.get('/api/sessions').status_code,401)
                (downloads/'心电工作台-Windows免安装.zip').unlink()
                self.assertEqual(client.get('/downloads/ecg-workspace-windows.zip').status_code,404)


if __name__=='__main__':unittest.main()
