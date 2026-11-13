"""公开训练示例的隔离与拒绝边界；不加载真实模型或GPU。"""
import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from report_agent.common import load, read_rows, save, signature, write_rows
from report_agent.structured import validate_schema
from scripts.make_synthetic_sft import build
from training.data import validate_row
from training.train import train


class PublicTrainingTests(unittest.TestCase):
    def fixture(self, root):
        data = root / 'fixture'
        with contextlib.redirect_stdout(io.StringIO()):
            manifest = build(data)
        config = root / 'config.json'
        save(config, {})
        return data, config, manifest

    def test_generated_fixture_is_reproducible_group_isolated_and_schema_valid(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            first, _, manifest = self.fixture(root)
            with contextlib.redirect_stdout(io.StringIO()):
                second_manifest = build(root / 'second')
            rows = {split: read_rows(first / (split + '.jsonl')) for split in ('train', 'validation')}
            self.assertEqual(manifest, second_manifest)
            self.assertEqual(manifest['fingerprint'], signature(rows))
            self.assertEqual((len(rows['train']), len(rows['validation'])), (12, 4))
            self.assertFalse({r['group_id'] for r in rows['train']} & {r['group_id'] for r in rows['validation']})
            self.assertEqual(len({r['id'] for group in rows.values() for r in group}), 16)
            self.assertIs(manifest['formal_training_allowed'], False)
            for row in rows['train'] + rows['validation']:
                validate_row(row)
                validate_schema(json.loads(row['messages'][-1]['content']), row['output_schema'])
                self.assertIs(row['synthetic'], True)
                self.assertEqual(row['review']['kind'], 'synthetic_fixture_review')

    def test_machine_fixture_review_cannot_approve_real_or_unmarked_data(self):
        with tempfile.TemporaryDirectory() as folder:
            data, _, _ = self.fixture(Path(folder))
            row = read_rows(data / 'train.jsonl')[0]
            for value in (False, None, 'true'):
                bad = copy.deepcopy(row)
                if value is None:
                    bad.pop('synthetic')
                else:
                    bad['synthetic'] = value
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'synthetic=true'):
                    validate_row(bad)

    def test_formal_training_refuses_fixture_before_model_access(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data, config, _ = self.fixture(root)
            with patch('training.train.model_files', side_effect=AssertionError('must not access model')) as model:
                with self.assertRaisesRegex(ValueError, '只允许smoke'):
                    train(config, data, root / 'out', 'formal', prepare_only=True)
                model.assert_not_called()
            self.assertFalse((root / 'out').exists())

    def test_mixing_other_rows_cannot_bypass_fixture_formal_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data, config, manifest = self.fixture(root)
            rows = {split: read_rows(data / (split + '.jsonl')) for split in ('train', 'validation')}
            # 仅为边界测试构造另一种合法审核类型，不表示该行是真实课程材料。
            rows['train'][0]['synthetic'] = False
            rows['train'][0]['review']['kind'] = 'human_review'
            rows['train'][0]['review']['reviewer'] = 'unit_test_only'
            write_rows(data / 'train.jsonl', rows['train'])
            manifest.update(fingerprint=signature(rows), synthetic_only=False)
            save(data / 'dataset_manifest.json', manifest)
            with patch('training.train.model_files', side_effect=AssertionError('must not access model')) as model:
                with self.assertRaisesRegex(ValueError, '只允许smoke'):
                    train(config, data, root / 'out', 'formal', prepare_only=True)
                model.assert_not_called()

    def test_changed_fixture_is_not_accepted_with_old_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data, config, _ = self.fixture(root)
            rows = read_rows(data / 'train.jsonl')
            rows[0]['id'] += ':changed'
            write_rows(data / 'train.jsonl', rows)
            with patch('training.train.model_files', side_effect=AssertionError('must not access model')):
                with self.assertRaisesRegex(ValueError, '指纹不一致'):
                    train(config, data, root / 'out', 'smoke', prepare_only=True)

    def test_course_overlap_is_rejected_even_with_updated_fingerprint(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            data, config, manifest = self.fixture(root)
            rows = {split: read_rows(data / (split + '.jsonl')) for split in ('train', 'validation')}
            for row in rows['validation']:
                row['group_id'] = rows['train'][0]['group_id']
            write_rows(data / 'validation.jsonl', rows['validation'])
            manifest['fingerprint'] = signature(rows)
            save(data / 'dataset_manifest.json', manifest)
            with patch('training.train.model_files', side_effect=AssertionError('must not access model')):
                with self.assertRaisesRegex(ValueError, '课程组跨集合泄漏'):
                    train(config, data, root / 'out', 'smoke', prepare_only=True)

    def test_manual_helper_defaults_to_current_interpreter(self):
        from scripts import manual_finetune
        for name in ('DOC_PY', 'FT_PY', 'SERVICE_PY'):
            self.assertEqual(getattr(manual_finetune, name).resolve(), Path(sys.executable).resolve())

    def test_preparation_uses_explicit_training_and_tokenizer_paths(self):
        from training.preparation import prepare
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            adapter = root / 'adapter'
            adapter.mkdir()
            save(adapter / 'adapter_config.json', {'peft_type':'LORA'})
            (adapter / 'adapter_model.safetensors').write_bytes(b'synthetic identity fixture only')
            config = root / 'prepare.json'
            selected = root / 'custom_training.json'
            run = root / 'course'
            save(selected, {'initialization':'continue_adapter', 'model_path':str(root / 'base'), 'adapter_path':str(adapter)})
            save(config, {'training_config':str(selected), 'tokenizer_path':str(root / 'custom_tokenizer'), 'courses':[{'run':str(run)}]})
            with patch('model_service.loading.tokenizer_directory', side_effect=AssertionError('explicit tokenizer must take priority')) as fallback, \
                    patch('training.preparation.verify_inputs', side_effect=RuntimeError('source boundary reached')) as source:
                with self.assertRaisesRegex(RuntimeError, 'source boundary reached'):
                    prepare(config, root / 'out')
                source.assert_called_once_with(run)
                fallback.assert_not_called()

    def test_source_preparation_does_not_silently_switch_to_new_lora(self):
        from training.preparation import prepare
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            config = root / 'prepare.json'
            selected = root / 'new_lora.json'
            save(selected, {'initialization':'new_lora', 'adapter_path':None})
            save(config, {'training_config':str(selected)})
            with patch('training.preparation.model_files', side_effect=AssertionError('must not inspect model')) as model:
                with self.assertRaisesRegex(ValueError, '从基座新建LoRA'):
                    prepare(config, root / 'out')
                model.assert_not_called()


if __name__ == '__main__':
    unittest.main()
