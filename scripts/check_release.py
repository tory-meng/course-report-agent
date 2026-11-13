#!/usr/bin/env python3
"""检查准备发布的文件，不上传文件，也不修改Git索引。

Git仓库默认检查已跟踪文件；解压后的非Git目录检查全部文件。
检查结果只打印文件路径和规则名，绝不回显疑似密钥的原文。
"""
import argparse
import json
import re
import subprocess
from pathlib import Path
from zipfile import BadZipFile, ZipFile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
WEIGHT_SUFFIXES = {'.safetensors', '.bin', '.pt', '.pth', '.ckpt', '.gguf', '.onnx', '.h5', '.hdf5'}
PRIVATE_BINARY_SUFFIXES = {'.xls', '.xlsx', '.pdf', '.sqlite', '.sqlite3', '.db'}
ARCHIVE_SUFFIXES = {'.zip', '.7z', '.rar', '.tar', '.tgz', '.gz', '.bz2', '.xz'}
LOCAL_PATH = re.compile(r"""/(?:home|Users)/[^/\s'"]+(?:/|(?=$|[\s'"]))|[A-Za-z]:[\\/]Users[\\/][^\\/\s'"]+""")
SECRETS = [
    ('private_key', re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
    ('github_token', re.compile(r'\bgh[pousr]_[A-Za-z0-9]{20,}\b|\bgithub_pat_[A-Za-z0-9_]{30,}\b')),
    ('api_key', re.compile(r'\bsk-[A-Za-z0-9_-]{24,}\b')),
]


def _git_files(root):
    try:
        top = subprocess.run(['git', '-C', str(root), 'rev-parse', '--show-toplevel'],
                             capture_output=True, text=True, check=True).stdout.strip()
        if Path(top).resolve() != root:
            return None
        raw = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached'],
                             capture_output=True, check=True).stdout
    except (OSError, subprocess.CalledProcessError):
        return None
    return [root / name.decode('utf-8') for name in raw.split(b'\0') if name]


def _text_findings(text, path, findings, member=None):
    for n, line in enumerate(text.splitlines(), 1):
        rules = [name for name, pattern in SECRETS if pattern.search(line)]
        if LOCAL_PATH.search(line):
            rules.append('machine_specific_path')
        for rule in rules:
            record = {'rule': rule, 'path': path, 'line': n}
            if member:
                record['archive_member'] = member
            findings.append(record)


def scan_release(root, mode='auto', max_bytes=DEFAULT_MAX_BYTES):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError('发布目录不存在')
    if mode not in ('auto', 'all', 'tracked') or max_bytes <= 0:
        raise ValueError('无效检查参数')
    tracked = _git_files(root) if mode != 'all' else None
    if mode == 'tracked' and tracked is None:
        raise ValueError('tracked模式要求root正好是Git仓库根目录')
    # git init但尚未git add时仍检查文件系统，避免空索引被误判为安全。
    use_tracked = tracked is not None and (bool(tracked) or mode == 'tracked')
    files = tracked if use_tracked else [p for p in root.rglob('*')
                                        if '.git' not in p.relative_to(root).parts and (p.is_file() or p.is_symlink())]
    findings = []
    total_bytes = 0
    sizes = []
    for p in sorted(files):
        relative = p.relative_to(root)
        name = relative.as_posix()
        if p.is_symlink():
            findings.append({'rule': 'symlink_not_allowed_in_release', 'path': name})
            continue
        if not p.is_file():
            findings.append({'rule': 'tracked_file_missing', 'path': name})
            continue
        size = p.stat().st_size
        total_bytes += size
        sizes.append((size, name))
        suffix = p.suffix.lower()
        if size > max_bytes:
            findings.append({'rule': 'file_too_large', 'path': name, 'bytes': size})
            continue
        if '__pycache__' in relative.parts or suffix in ('.pyc', '.pyo'):
            findings.append({'rule': 'python_cache', 'path': name})
        if p.name == '.env' or p.name.startswith('.env.') and p.name not in ('.env.example', '.env.sample'):
            findings.append({'rule': 'local_environment_file', 'path': name})
        if suffix in WEIGHT_SUFFIXES:
            findings.append({'rule': 'model_weight_or_checkpoint', 'path': name})
        if suffix in PRIVATE_BINARY_SUFFIXES:
            findings.append({'rule': 'private_document_or_database', 'path': name})
        if suffix in ARCHIVE_SUFFIXES:
            findings.append({'rule': 'nested_archive', 'path': name})
        if relative.parts[0] in ('data', 'models', 'inputs', 'outputs', 'knowledge', 'reviews', 'cases') and p.name not in ('README.md', '.gitkeep'):
            findings.append({'rule': 'local_artifact_directory', 'path': name})
        if relative.parts[0] == 'configs' and not p.name.endswith('.example.json') and p.name not in ('README.md', '.gitkeep', 'template_registry.json', 'material_intake.json'):
            findings.append({'rule': 'active_local_configuration', 'path': name})
        if suffix in ('.csv', '.tsv', '.jsonl') and name != 'examples/demo_course/grades.csv':
            findings.append({'rule': 'dataset_file', 'path': name})
        if suffix in ('.docx', '.docm'):
            # 仅公开演示母版可作为二进制文档分发；真实报告不应被误打包。
            if name != 'templates/course/buu_course_r2/course_reference_template.docx':
                findings.append({'rule': 'non_demo_word_document', 'path': name})
            try:
                with ZipFile(p) as z:
                    metadata = z.read('docProps/core.xml').decode('utf-8', errors='replace') if 'docProps/core.xml' in z.namelist() else ''
                    if 'Public synthetic course report demonstration template' not in metadata:
                        findings.append({'rule': 'non_synthetic_template_metadata', 'path': name})
                    for member in z.namelist():
                        if member.startswith('word/embeddings/'):
                            findings.append({'rule': 'embedded_document', 'path': name, 'archive_member': member})
                        if member.endswith('.xml'):
                            info = z.getinfo(member)
                            if info.file_size > max_bytes:
                                findings.append({'rule': 'oversized_document_xml', 'path': name, 'archive_member': member})
                            else:
                                _text_findings(z.read(member).decode('utf-8', errors='replace'), name, findings, member)
            except BadZipFile:
                findings.append({'rule': 'invalid_word_archive', 'path': name})
        elif suffix not in WEIGHT_SUFFIXES | PRIVATE_BINARY_SUFFIXES | ARCHIVE_SUFFIXES:
            content = p.read_bytes()
            if b'\x00' not in content:
                _text_findings(content.decode('utf-8', errors='replace'), name, findings)
    if not files:
        findings.append({'rule': 'empty_release', 'path': '.'})
    return {'status': 'passed' if not findings else 'failed',
            'mode': 'tracked' if use_tracked else 'all', 'files_checked': len(files),
            'total_bytes': total_bytes, 'max_file_bytes': max_bytes,
            'largest_files': [{'path': name, 'bytes': size} for size, name in sorted(sizes, reverse=True)[:10]],
            'findings': findings,
            'scope': '文件与文本启发式检查；不证明资料已获发布授权，发布前仍需检查所保留演示文件。'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--mode', choices=['auto', 'all', 'tracked'], default='auto')
    parser.add_argument('--max-file-mib', type=float, default=5)
    args = parser.parse_args()
    try:
        report = scan_release(args.root, args.mode, int(args.max_file_mib * 1024 * 1024))
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
