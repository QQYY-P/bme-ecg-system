"""Regression cases for documentation integrity, including portable checkouts."""
from pathlib import Path
import tempfile
import unittest

from check_docs import anchors, check_links, check_members, check_packages, links, python_manifest
from sync_docs import offline_readme, stale_files


class DocumentationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def test_unicode_encoded_spaces_and_duplicate_anchors(self):
        self.write('README.md', '[中文](docs/说明%20文档.md#安装-步骤-1)\n[图](images/a(1).png)')
        self.write('docs/说明 文档.md', '# 安装 步骤\n# 安装 步骤\n')
        self.write('images/a(1).png', 'placeholder')
        errors, count = check_links(self.root, {'README.md', 'docs/说明 文档.md', 'images/a(1).png'})
        self.assertEqual(errors, [])
        self.assertEqual(count, 2)

    def test_untracked_file_cannot_mask_broken_link(self):
        self.write('README.md', '[local](only-on-my-machine.md)')
        self.write('only-on-my-machine.md', '# Present locally')
        errors, _ = check_links(self.root, {'README.md'})
        self.assertIn('untracked or missing target', errors[0])

    def test_missing_case_mismatch_and_missing_anchor(self):
        self.write('README.md', '[missing](gone.md)\n[case](guide.md)\n[anchor](Guide.md#removed)')
        self.write('Guide.md', '# Available')
        errors, _ = check_links(self.root, {'README.md', 'Guide.md'})
        self.assertEqual(len(errors), 3)
        self.assertTrue(any('missing anchor' in e for e in errors))

    def test_escape_is_rejected(self):
        self.write('README.md', '[outside](../private.txt)')
        errors, _ = check_links(self.root, {'README.md'})
        self.assertIn('outside repository', errors[0])

    def test_code_and_external_urls_are_not_local_links(self):
        text = ('```md\n[x](missing.md)\n```\n~~~\n[y](other.md)\n~~~\n'
                '`[z](inline.md)`\n    [indented](example.md)\n'
                '[web](https://example.com/page#part)')
        self.write('README.md', text)
        errors, count = check_links(self.root, {'README.md'})
        self.assertEqual((errors, count), ([], 0))

    def test_reference_links_and_undefined_reference(self):
        actual = list(links('[a][target]\n[b][undefined]\n[target][]\n[target]\n\n'
                            '[target]: <a file.md> "Title"'))
        self.assertEqual(actual, ['a file.md', 'missing-reference:undefined'])

    def test_heading_markup_and_explicit_html_anchor(self):
        actual = anchors('# **Hello** `World`!\nTitle\n=====\n<a id="custom"></a>\n'
                         '```\n# ignored\n```')
        self.assertTrue({'hello-world', 'title', 'custom'} <= actual)
        self.assertNotIn('ignored', actual)

    def test_package_requires_tracked_regular_file(self):
        self.write('module/a.py', '')
        self.write('module/local.py', '')
        errors = check_members(self.root, {'module/a.py'}, 'module',
                               ['a.py', 'missing.py', 'local.py', '../escape.py', 'C:/private.py'])
        self.assertEqual(len(errors), 4)

    def test_static_manifest_never_executes_packager(self):
        self.write('manifest.py', "raise RuntimeError('must not run')\nA=('a.py',)\nB=('b.py',)\nFILES=set(A+B)|{'c.py'}\n")
        members, _ = python_manifest(self.root / 'manifest.py', 'FILES')
        self.assertEqual(members, {'a.py', 'b.py', 'c.py'})

    def test_dynamic_manifest_fails_closed(self):
        self.write('manifest.py', 'FILES = make_package_files()\n')
        with self.assertRaises(ValueError):
            python_manifest(self.root / 'manifest.py', 'FILES')

    def test_deleted_manifest_is_a_failure(self):
        errors, count = check_packages(self.root, set())
        self.assertEqual(len(errors), 4)
        self.assertEqual(count, 0)

    def test_offline_readme_has_portable_links_and_staleness(self):
        for page in ('快速开始.md', '使用说明.md', '故障排查.md'):
            self.write('模块/docs/' + page, '# 标题\n[入口](../README.md)\n')
        output = offline_readme(self.root, '模块')
        self.assertIn('https://github.com/XiongBoT/bme-ecg-system/blob/main/模块/README.md', output)
        expected = {'模块/README.txt': output}
        self.assertEqual(stale_files(self.root, expected), ['模块/README.txt'])
        self.write('模块/README.txt', output)
        self.assertEqual(stale_files(self.root, expected), [])
        self.write('模块/docs/使用说明.md', '# Changed\n')
        self.assertNotEqual(offline_readme(self.root, '模块'), output)


if __name__ == '__main__':
    unittest.main()
