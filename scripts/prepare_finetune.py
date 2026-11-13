#!/usr/bin/env python3
"""依据当前推理runtime绑定继续微调的基座、Adapter及其哈希；不启动GPU训练。"""
import argparse
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from report_agent.common import path,load,save,model_files,digest

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime',default='configs/runtime.json')
    p.add_argument('--out',default='configs/training_course_round2.json')
    a=p.parse_args();target=path(a.out)
    if target.exists():raise ValueError('配置已存在，请复用已核对配置或指定新路径：'+str(target))
    model=load(path(a.runtime))['model'];base=path(model['expected_model_path']);adapter=path(model['expected_adapter_path'])
    model_files(base);hashes={p.name:digest(p) for p in model_files(adapter,True)}
    if model.get('expected_adapter_sha256') and model['expected_adapter_sha256']!=hashes:raise ValueError('Adapter内容与推理配置绑定不符')
    cfg=load(path('configs/training_continue.example.json'))
    cfg.update(model_path=str(base),adapter_path=str(adapter),expected_adapter_sha256=hashes)
    save(target,cfg);print('已绑定当前模型与Adapter：',target)

if __name__=='__main__':main()
