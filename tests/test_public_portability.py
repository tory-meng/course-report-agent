"""公开包运行环境和本机配置另存行为；不需要模型、GPU或私有材料。"""
import contextlib
import importlib.util
import io
import json
import shlex
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from report_agent.common import ROOT, digest


def load_script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublicPortabilityTests(unittest.TestCase):
    def test_catalog_probe_uses_active_interpreter(self):
        from report_agent import material_catalog
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            case_file = out / 'case with spaces.json'
            package = {'id':'synthetic', 'folder':'合成课程', 'family':'合成课程',
                       'course_name':'合成课程', 'status':'ready_full', 'file_count':1,
                       'reason':'test fixture', 'notes':[], 'path':str(out),
                       'family_group_id':'synthetic', 'existing_cases':[]}
            catalog = {'packages':[package], 'root':str(out), 'scanned_at':'synthetic',
                       'package_count':1, 'file_count':1}
            executable = '/example/env with spaces/bin/python'
            with patch.object(material_catalog, 'prepare_case', return_value=case_file), \
                    patch.object(material_catalog.sys, 'executable', executable):
                material_catalog.write_outputs(catalog, out)
            command = shlex.split(json.loads((out / 'batch_queue.json').read_text())[0]['probe_command'])
            self.assertEqual(command[0], executable)
            self.assertEqual(command[command.index('--case') + 1], str(case_file))

    def test_unicode_selftest_keeps_supplementary_chars_without_real_teacher(self):
        from model_service.structured import tokenizer_selftest
        captured = []
        with patch('model_service.structured.replay_json', side_effect=lambda tok, factory, schema, answer: captured.append(answer['value'])):
            tokenizer_selftest(object(), object())
        self.assertIn('𠀀🙂', captured)
        self.assertTrue(any('龘' in item for item in captured))
        self.assertTrue(any('\n' in item and '\\' in item for item in captured))

    def test_runtime_only_new_path_does_not_touch_baseline_files(self):
        configure = load_script('configure_models')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            configs = root / 'configs'
            configs.mkdir()
            (configs / 'runtime.example.json').write_text(json.dumps({'model': {}}))
            for name in ('runtime.json', 'training_continue.json', 'model_identity.json'):
                (configs / name).write_text('baseline ' + name)
            before = {p.name: p.read_bytes() for p in configs.iterdir()}
            base = root / 'base'
            adapter = root / 'adapter'
            base.mkdir()
            adapter.mkdir()
            (base / 'config.json').write_text('{}')
            (base / 'model.safetensors').write_bytes(b'not-a-real-model-test-only')
            (adapter / 'adapter_config.json').write_text('{"peft_type":"LORA"}')
            (adapter / 'adapter_model.safetensors').write_bytes(b'not-real-adapter-test-only')
            target = configs / 'runtime.candidate.local.json'
            args = ['--base', str(base), '--adapter', str(adapter), '--out', str(target), '--runtime-only']
            with patch.object(configure, 'ROOT', root), contextlib.redirect_stdout(io.StringIO()):
                configure.main(args)
                with self.assertRaises(ValueError):
                    configure.main(args)
            actual = json.loads(target.read_text())
            self.assertEqual(actual['model']['expected_adapter_path'], str(adapter))
            self.assertEqual(len(actual['model']['expected_adapter_sha256']), 2)
            for name, data in before.items():
                self.assertEqual((configs / name).read_bytes(), data)

    def test_environment_check_default_does_not_collect_ml_dependencies(self):
        checker = load_script('check_environment')
        real = checker.package_versions
        requested = []

        def versions(names):
            requested.extend(names)
            return real(names)

        with patch.object(checker, 'package_versions', side_effect=versions):
            result = checker.inspect_environment('configs/runtime.example.json')
        self.assertNotIn('torch', requested)
        self.assertNotIn('transformers', requested)
        self.assertNotIn('cuda', [r['check'] for r in result['checks']])

    def test_copy_adapter_and_separate_runtime_bind_verified_copy(self):
        configure = load_script('configure_models')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            configs = root / 'configs'
            configs.mkdir()
            (configs / 'runtime.example.json').write_text(json.dumps({'model': {}}))
            base = root / 'base'
            source = root / 'source_adapter'
            base.mkdir()
            source.mkdir()
            (base / 'config.json').write_text('{}')
            (base / 'model.safetensors').write_bytes(b'synthetic-base-weights-test-only')
            (source / 'adapter_config.json').write_text('{"peft_type":"LORA"}')
            (source / 'adapter_model.safetensors').write_bytes(b'synthetic-adapter-weights-test-only')
            (source / 'tokenizer_config.json').write_text('{"test_fixture":true}')
            before = {file.name: file.read_bytes() for file in source.iterdir()}
            expected_hashes = {name: digest(source / name) for name in ('adapter_config.json', 'adapter_model.safetensors')}
            copied = root / 'models' / 'copied_adapter'
            runtime = configs / 'runtime.copied.local.json'
            args = ['--base', str(base), '--adapter', str(source), '--copy-adapter-to', str(copied),
                    '--out', str(runtime), '--runtime-only']
            with patch.object(configure, 'ROOT', root), contextlib.redirect_stdout(io.StringIO()):
                configure.main(args)
            self.assertTrue(runtime.is_file())
            self.assertTrue(copied.is_dir())
            configured = json.loads(runtime.read_text())['model']
            self.assertEqual(configured['expected_adapter_path'], str(copied))
            self.assertEqual(configured['expected_adapter_sha256'], expected_hashes)
            self.assertEqual(configured['base_url'], 'http://127.0.0.1:18000')
            for name, data in before.items():
                self.assertEqual((copied / name).read_bytes(), data)
                self.assertEqual((source / name).read_bytes(), data)
            self.assertFalse((configs / 'training_continue.json').exists())
            self.assertFalse((configs / 'model_identity.json').exists())


if __name__ == '__main__':
    unittest.main()
