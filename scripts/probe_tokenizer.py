"""只加载本地分词器，回放JSON约束；不加载基座/Adapter权重、不启动服务。"""
import argparse
import importlib.metadata
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from report_agent.common import load,path,save
from model_service.loading import tokenizer_directory
from model_service.structured import load_backend,replay_json,tokenizer_selftest
from report_agent.direct_extract import labelled_row

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--runtime',default='configs/runtime.json')
    p.add_argument('--record',help='可选：失败的records/extract_0000.json')
    p.add_argument('--out',default='outputs/tokenizer_probe.json')
    a=p.parse_args();result={'passed':False,'scope':'分词器及约束回放，不代表模型事实抽取准确率'}
    try:
        from transformers import AutoTokenizer
        cfg=load(path(a.runtime))['model']
        directory=tokenizer_directory(path(cfg['expected_model_path']),path(cfg['expected_adapter_path']))
        tokenizer=AutoTokenizer.from_pretrained(directory,local_files_only=True,trust_remote_code=False)
        result.update(tokenizer_path=directory,tokenizer_class=type(tokenizer).__name__,vocab_size=len(tokenizer),
                      versions={k:importlib.metadata.version(k) for k in ('transformers','lm-format-enforcer')})
        factory=load_backend(tokenizer)
        result['basic_checks']=tokenizer_selftest(tokenizer,factory)
        if a.record:
            record=load(path(a.record));messages=record['messages']
            prompt=json.loads(messages[-1]['content']);blocks=prompt['evidence']
            records=[]
            for b in blocks:
                values,covered=labelled_row(b)
                if covered:records.append({'kind':'metadata','value':values,'evidence_ids':[b['id']]})
            if records and 'metadata' in prompt.get('allowed_kinds',[]):
                answer={'records':records}
                result['failed_case_check']=replay_json(tokenizer,factory,record['output_schema'],answer,
                                                       '\n'.join(m['content'] for m in messages))
            else:result['failed_case_check']={'skipped':True,'reason':'没有可直接读取的完整标签行；基础回放仍执行'}
        result['passed']=True
    except Exception as exc:
        result.update(error_type=type(exc).__name__,error=str(exc))
    save(path(a.out),result)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['passed'] else 1

if __name__=='__main__':raise SystemExit(main())
