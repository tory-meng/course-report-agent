"""发布检查器使用临时合成文件验证；不读取私有资料或请求网络。"""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts.check_release import scan_release


class ReleaseChecksTests(unittest.TestCase):
    def test_small_source_and_documented_synthetic_examples_are_allowed(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'README.md').write_text('Demo project; prepare your own local model.')
            (root / 'examples/demo_course').mkdir(parents=True)
            (root / 'examples/demo_course/grades.csv').write_text('student_id,name,total\nD001,Synthetic student,80\n')
            result = scan_release(root, mode='all')
            self.assertEqual(result['status'], 'passed')
            self.assertEqual(result['files_checked'], 2)

    def test_weights_local_data_and_oversized_files_fail(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'models').mkdir()
            (root / 'models/adapter.safetensors').write_bytes(b'dummy')
            (root / 'data').mkdir()
            (root / 'data/training.jsonl').write_text('{"source":"private"}\n')
            (root / 'large.txt').write_bytes(b'x' * 100)
            result = scan_release(root, mode='all', max_bytes=50)
            rules = {x['rule'] for x in result['findings']}
            self.assertEqual(result['status'], 'failed')
            self.assertIn('model_weight_or_checkpoint', rules)
            self.assertIn('local_artifact_directory', rules)
            self.assertIn('file_too_large', rules)

    def test_sensitive_matches_report_location_without_secret_value(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            sensitive = 'gh' + 'p_' + 'A' * 36
            local = '/home/' + 'hy/private/model'
            (root / 'config.txt').write_text('credential=' + sensitive + '\npath=' + local)
            result = scan_release(root, mode='all')
            self.assertEqual(result['status'], 'failed')
            self.assertEqual({r['rule'] for r in result['findings']}, {'github_token', 'machine_specific_path'})
            self.assertNotIn(sensitive, str(result))
            self.assertNotIn(local, str(result))

    def test_external_symlink_is_not_read(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'outside').symlink_to(root.parent / 'private-model.bin')
            result = scan_release(root, mode='all')
            self.assertEqual(result['findings'], [{'rule': 'symlink_not_allowed_in_release', 'path': 'outside'}])

    def test_forced_tracked_configs_cases_datasets_weights_and_other_users_are_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            allowed = {'configs/runtime.example.json':'{}', 'configs/template_registry.json':'{}',
                       'configs/material_intake.json':'{}',
                       'examples/demo_course/grades.csv':'student_id,name,total\nD001,Synthetic student,80\n'}
            blocked = {'configs/runtime.json':'{}', 'cases/course.json':'{}', 'assets/weights.bin':'dummy',
                       'assets/scores.csv':'id,total\nD001,80\n', 'assets/scores.tsv':'id\ttotal\nD001\t80\n',
                       'assets/train.jsonl':'{"synthetic":false}\n'}
            unix = '/home/' + 'alice/private/model'
            mac = '/Users/' + 'sam/private/model'
            windows = 'C:' + chr(92) + 'Users' + chr(92) + 'alex' + chr(92) + 'models'
            blocked['settings.txt'] = '\n'.join((unix, mac, windows))
            for name, content in {**allowed, **blocked}.items():
                file = root / name
                file.parent.mkdir(parents=True, exist_ok=True)
                file.write_text(content)
            # 模拟这些文件被git add -f强制暂存；不能仅依赖.gitignore保护。
            files = [p for p in root.rglob('*') if p.is_file()]
            with patch('scripts.check_release._git_files', return_value=files):
                result = scan_release(root, mode='tracked')
            self.assertEqual(result['status'], 'failed')
            findings = result['findings']
            pairs = {(r['path'], r['rule']) for r in findings}
            self.assertIn(('configs/runtime.json', 'active_local_configuration'), pairs)
            self.assertIn(('cases/course.json', 'local_artifact_directory'), pairs)
            self.assertIn(('assets/weights.bin', 'model_weight_or_checkpoint'), pairs)
            for name in ('assets/scores.csv', 'assets/scores.tsv', 'assets/train.jsonl'):
                self.assertIn((name, 'dataset_file'), pairs)
            self.assertEqual(sum(r['rule']=='machine_specific_path' for r in findings), 3)
            self.assertFalse(any(r['path'] in allowed for r in findings))


if __name__ == '__main__':
    unittest.main()
