#!/usr/bin/env python3
"""按公开代码白名单构建ZIP；不包含私有资源/运行产物，也不修改Git索引。"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CODE_DIRS = {'report_agent','model_service','training','scripts','tests','prompts','examples','docs','templates','validation','.github'}
ROOT_FILES = {'README.md','RELEASE_NOTES.md','LICENSE_NOTICE.md','.gitignore','.gitattributes',
              'run_report.py','train_v2.py','requirements-docagent.txt','requirements-ml.txt',
              'requirements-structured.txt','requirements-test.txt'}
NOTES = {'models/README.md','data/README.md','inputs/README.md','outputs/README.md',
         'knowledge/README.md','cases/README.md','inputs/current/.gitkeep','inputs/references/.gitkeep'}

def selected(relative):
    name = relative.as_posix()
    if name in ROOT_FILES or name in NOTES:
        return True
    if '__pycache__' in relative.parts or relative.suffix in ('.pyc','.pyo','.log','.tmp'):
        return False
    if relative.parts[0] == 'configs':
        return relative.name.endswith('.example.json') or relative.name in ('template_registry.json','material_intake.json')
    return relative.parts[0] in CODE_DIRS

def build(out):
    out = Path(out).expanduser().resolve()
    if out.exists() or out.with_suffix(out.suffix+'.sha256').exists():
        raise ValueError('目标已存在；使用新的ZIP文件名')
    if out.suffix.lower() != '.zip':
        raise ValueError('--out 必须以.zip结尾')
    out.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='course_agent_release_') as tmp:
        stage = Path(tmp)/ROOT.name
        stage.mkdir()
        for file in sorted(ROOT.rglob('*')):
            relative = file.relative_to(ROOT)
            if not selected(relative):
                continue
            if file.is_symlink():
                raise ValueError('公开代码中不接受符号链接：'+str(relative))
            if file.is_file():
                dest = stage/relative
                dest.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(file,dest)
        check = subprocess.run([sys.executable,str(stage/'scripts/check_release.py'),
                                '--root',str(stage),'--mode','all'],capture_output=True,text=True)
        if check.returncode:
            raise RuntimeError('发布检查失败，未创建ZIP：\n'+check.stdout+check.stderr)
        report = json.loads(check.stdout)
        lines = []
        for file in sorted(stage.rglob('*')):
            if file.is_file():
                lines.append(hashlib.sha256(file.read_bytes()).hexdigest()+'  '+file.relative_to(stage).as_posix())
        (stage/'MANIFEST.sha256').write_text('\n'.join(lines)+'\n',encoding='utf-8')
        with zipfile.ZipFile(out,'w',zipfile.ZIP_DEFLATED) as archive:
            for file in sorted(stage.rglob('*')):
                if file.is_file():
                    archive.write(file,ROOT.name+'/'+file.relative_to(stage).as_posix())
        with zipfile.ZipFile(out) as archive:
            if archive.testzip() is not None:
                raise RuntimeError('ZIP完整性检查失败')
        digest = hashlib.sha256(out.read_bytes()).hexdigest()
        out.with_suffix(out.suffix+'.sha256').write_text(digest+'  '+out.name+'\n',encoding='utf-8')
        result = {'status':'passed','zip':str(out),'files':len(lines)+1,'bytes':out.stat().st_size,
                  'sha256':digest,'release_check_status':report['status']}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        return result

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',default=str(ROOT.parent/(ROOT.name+'.zip')))
    args = p.parse_args()
    build(args.out)

if __name__=='__main__':
    main()
