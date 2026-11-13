"""读取现有runtime.json启动同一模型/Adapter，不修改任何配置。"""
import argparse
import sys
from pathlib import Path
from urllib.parse import urlsplit
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from report_agent.common import path,load,digest,model_files

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',default='configs/runtime.json')
    parser.add_argument('--max-input-tokens',type=int,default=8192)
    parser.add_argument('--max-output-tokens',type=int,default=8192)
    parser.add_argument('--device-map',choices=['single','auto'],default='single')
    args=parser.parse_args();cfg=load(path(args.runtime))['model'];url=urlsplit(cfg['base_url'])
    if url.scheme!='http' or url.hostname not in ('localhost','127.0.0.1') or url.path not in ('','/'):
        raise ValueError('此入口仅启动runtime指定的本地http服务')
    if not cfg.get('expected_adapter_path'):raise ValueError('runtime缺少expected_adapter_path')
    adapter=path(cfg['expected_adapter_path']);files=model_files(adapter,True)
    actual={p.name:digest(p) for p in files}
    if cfg.get('expected_adapter_sha256') and cfg['expected_adapter_sha256']!=actual:
        raise ValueError('Adapter哈希不符；不自动替换用户选定权重')
    from model_service.serve import start
    start(argparse.Namespace(model_path=str(path(cfg['expected_model_path'])),adapter_path=str(adapter),
        bits=cfg['expected_bits'],port=url.port or 80,device_map=args.device_map,attention='sdpa',
        max_input_tokens=args.max_input_tokens,max_output_tokens=args.max_output_tokens))

if __name__=='__main__':main()
