#!/usr/bin/env python3
"""检查公开实验包依赖；默认不导入PyTorch、不加载模型、不请求服务。"""
import argparse
import importlib.metadata
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from report_agent.common import ROOT, digest, load, model_files, path

DOCUMENT_PACKAGES = ('python-docx', 'openpyxl', 'xlrd', 'PyMuPDF', 'matplotlib', 'jsonschema')
ML_PACKAGES = ('torch', 'transformers', 'peft', 'accelerate', 'bitsandbytes',
               'safetensors', 'fastapi', 'uvicorn', 'lm-format-enforcer')


def package_versions(names):
    result = {}
    for name in names:
        try:
            result[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            result[name] = None
    return result


def inspect_environment(runtime_file, *, ml=False, cuda=False, models=False):
    checks = []
    result = {'python': sys.version.split()[0], 'executable': sys.executable,
              'project_root': str(ROOT), 'checks': checks}

    def record(name, ok, detail):
        checks.append({'check': name, 'passed': bool(ok), 'detail': detail})

    record('python_version', sys.version_info >= (3, 10), '需要 Python 3.10 或更新版本')
    versions = package_versions(DOCUMENT_PACKAGES + (ML_PACKAGES if ml or cuda else ()))
    result['package_versions'] = versions
    for name, version in versions.items():
        record('package:' + name, version is not None, version or '未安装')

    config = None
    try:
        config = load(path(runtime_file))
        record('runtime_config', True, str(path(runtime_file)))
    except (OSError, ValueError, TypeError) as exc:
        record('runtime_config', False, str(exc))

    if config is not None:
        executable = config.get('libreoffice') or shutil.which('libreoffice') or shutil.which('soffice')
        resolved = shutil.which(str(executable)) if executable else None
        record('libreoffice', resolved is not None, resolved or '安装 LibreOffice，或在 runtime 中配置 libreoffice 可执行路径')
        try:
            registry = load(path(config['template_registry']))
            entry = registry['templates']['course']
            contract_file = path(entry['contract'])
            contract = load(contract_file)
            if not entry.get('enabled') or contract['template_id'] != entry['template_id']:
                raise ValueError('课程模板未启用，或注册表和contract身份不符')
            for name, rel in contract['assets'].items():
                asset = contract_file.parent / rel
                if not asset.is_file() or digest(asset) != contract['asset_sha256'][name]:
                    raise ValueError('资源缺失或SHA不符：' + str(asset))
            record('template_assets', True, contract['template_id'])
        except (OSError, ValueError, TypeError, KeyError) as exc:
            record('template_assets', False, str(exc) + '；公开示例先执行 python scripts/build_demo_template.py')
        if models:
            try:
                model = config['model']
                if not model.get('expected_adapter_path'):
                    raise ValueError('尚未选定 Adapter；训练后执行 scripts/configure_models.py')
                base = path(model['expected_model_path'])
                adapter = path(model['expected_adapter_path'])
                model_files(base)
                files = model_files(adapter, True)
                actual = {f.name: digest(f) for f in files}
                expected = model.get('expected_adapter_sha256')
                if expected and actual != expected:
                    raise ValueError('Adapter SHA 与配置不同，不自动接受替换权重')
                record('model_files', True, {'base': str(base), 'adapter': str(adapter), 'adapter_sha256': actual})
            except (OSError, ValueError, TypeError, KeyError) as exc:
                record('model_files', False, str(exc))

    if cuda:
        try:
            import torch
            available = torch.cuda.is_available()
            detail = {'available': available, 'torch_cuda': torch.version.cuda}
            if available:
                detail['devices'] = [{'index': i, 'name': torch.cuda.get_device_properties(i).name,
                                      'total_memory_gib': round(torch.cuda.get_device_properties(i).total_memory / 1024**3, 2)}
                                     for i in range(torch.cuda.device_count())]
            record('cuda', available, detail)
        except (ImportError, RuntimeError, OSError) as exc:
            record('cuda', False, str(exc))
    result['status'] = 'passed' if all(item['passed'] for item in checks) else 'failed'
    result['notes'] = [
        '此检查不加载基座或Adapter、不调用模型服务，不代替真实推理、训练与文档视觉检查。',
        '中文图表和Word/PDF需安装可用中文字体，例如Noto CJK；自行准备的模型、课程数据与运行产物不应提交到公开仓库。',
    ]
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime', default='configs/runtime.example.json')
    p.add_argument('--ml', action='store_true', help='检查模型服务/训练依赖包版本，仍不加载模型')
    p.add_argument('--check-cuda', action='store_true', help='额外导入PyTorch并查询CUDA设备；不加载模型')
    p.add_argument('--check-model-files', action='store_true', help='检查所选基座与Adapter文件，并读取Adapter内容计算SHA')
    args = p.parse_args()
    result = inspect_environment(args.runtime, ml=args.ml, cuda=args.check_cuda, models=args.check_model_files)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result['status'] == 'passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
