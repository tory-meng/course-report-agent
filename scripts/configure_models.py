#!/usr/bin/env python3
"""校验选定基座和Adapter，生成运行/训练配置；可完整复制Adapter并校验哈希。"""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
import shutil
from report_agent.common import ROOT,path,load,save,digest,model_files

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--base',required=True);p.add_argument('--adapter',required=True)
    p.add_argument('--bits',type=int,choices=[4,8],default=8);p.add_argument('--base-url',default='http://127.0.0.1:18000')
    p.add_argument('--copy-adapter-to')
    p.add_argument('--out',default='configs/runtime.json',help='运行配置的新路径；拒绝覆盖现有文件')
    p.add_argument('--runtime-only',action='store_true',help='仅另存运行配置，用于候选对照；不写训练/身份配置')
    a=p.parse_args(argv);base=path(a.base);adapter=path(a.adapter)
    target=path(a.out)
    targets=[target] if a.runtime_only else [target,ROOT/'configs/training_continue.json',ROOT/'configs/model_identity.json']
    if len(set(targets))!=len(targets):raise ValueError('输出路径不能互相重合')
    for existing in targets:
        if existing.exists():raise ValueError(str(existing)+'已存在；请人工核对或选择新的--out，不自动覆盖')
    model_files(base);files=model_files(adapter,True);before={f.name:digest(f) for f in files}
    if a.copy_adapter_to:
        copy_target=path(a.copy_adapter_to)
        if copy_target.exists():raise ValueError('复制目标已存在，拒绝覆盖')
        if copy_target==adapter or adapter in copy_target.parents:raise ValueError('目标不能位于源目录内')
        shutil.copytree(adapter,copy_target);after={f.name:digest(f) for f in model_files(copy_target,True)}
        if before!=after:raise RuntimeError('Adapter复制校验失败')
        adapter=copy_target
    cfg=load(ROOT/'configs/runtime.example.json');cfg['model'].update(expected_model_path=str(base),expected_adapter_path=str(adapter),expected_bits=a.bits,base_url=a.base_url,expected_adapter_sha256=before)
    if target.exists():raise ValueError(str(target)+'已存在；请人工核对或另存备份后再配置')
    save(target,cfg)
    if not a.runtime_only:
        train=load(ROOT/'configs/training_continue.example.json');train.update(model_path=str(base),adapter_path=str(adapter),expected_adapter_sha256=before)
        save(ROOT/'configs/training_continue.json',train)
        save(ROOT/'configs/model_identity.json',{'base':str(base),'adapter':str(adapter),'adapter_sha256':before})
    print('已生成'+str(target)+('（仅运行配置）' if a.runtime_only else '及training_continue.json、model_identity.json')+'；服务仍须显式加载同一Adapter。')

if __name__=='__main__':main()
