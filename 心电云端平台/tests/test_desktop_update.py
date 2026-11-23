import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
import zipfile

spec=importlib.util.spec_from_file_location('desktop_update',Path(__file__).resolve().parents[1]/'deploy/linux/update_desktop.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


class UpdateArchiveTests(unittest.TestCase):
    def test_venv_link_survives_application_directory_switch(self):
        root=Path('/deployment').absolute()
        app=root/'ecg-cloud';previous=root/'backup/previous-app'
        self.assertEqual(module.venv_after_switch(app/'.venv',app,previous),previous/'.venv')
        external=root/'runtimes/ecg'
        self.assertEqual(module.venv_after_switch(external,app,previous),external)

    def test_verified_source_is_extracted_but_unlisted_and_traversal_files_are_rejected(self):
        body=b'print("source")\n';digest=hashlib.sha256(body).hexdigest()
        for extra,valid in [(None,True),('ecg-cloud/extra.py',False),('ecg-cloud/../escape.py',False)]:
            with self.subTest(extra=extra),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);archive=root/'bundle.zip'
                with zipfile.ZipFile(archive,'w') as z:
                    z.writestr('ecg-cloud/server.py',body)
                    z.writestr('ecg-cloud/MANIFEST.sha256',digest+'  server.py\n')
                    if extra:z.writestr(extra,b'unexpected')
                if valid:self.assertEqual((module.extract_verified(archive,root/'stage')/'server.py').read_bytes(),body)
                else:
                    with self.assertRaises(ValueError):module.extract_verified(archive,root/'stage')
                    self.assertFalse((root/'stage').exists())


if __name__=='__main__':unittest.main()
