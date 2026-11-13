"""构建、模型整理和检索本地PDF/Word参考资料库。"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from report_agent.common import path,load
from report_agent.reference_db import build,enrich,search,status_report,DEFAULT_DB

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    q=sub.add_parser('build');q.add_argument('--source',default='inputs/references');q.add_argument('--db',default=DEFAULT_DB)
    q.add_argument('--seed');q.add_argument('--no-ocr',action='store_true');q.add_argument('--ocr',action='store_true',help='默认已启用，保留显式写法')
    q.add_argument('--ocr-lang',default='chi_sim+eng');q.add_argument('--tessdata-dir');q.add_argument('--force',action='store_true')
    q=sub.add_parser('enrich');q.add_argument('--db',default=DEFAULT_DB);q.add_argument('--runtime',default='configs/runtime.json')
    q.add_argument('--records',default='knowledge/reference_model_records');q.add_argument('--limit',type=int);q.add_argument('--type',choices=['course','graduation','all'],default='all')
    q=sub.add_parser('query');q.add_argument('--db',default=DEFAULT_DB);q.add_argument('--query',required=True);q.add_argument('--top-k',type=int,default=3)
    q.add_argument('--type',choices=['course','graduation','all'],default='course')
    q=sub.add_parser('status');q.add_argument('--db',default=DEFAULT_DB)
    a=p.parse_args();db=path(a.db)
    if a.command=='build':
        result=build(path(a.source),db,path(a.seed) if a.seed else None,not a.no_ocr,a.ocr_lang,a.tessdata_dir,a.force,lambda s:print(s,flush=True))
    elif a.command=='enrich':
        if a.limit is not None and a.limit<1:p.error('--limit必须大于0')
        from report_agent.client import Client
        result=enrich(db,Client(load(path(a.runtime))['model']),path(a.records),a.limit,a.type,lambda s:print(s,flush=True))
    elif a.command=='query':result=search(db,a.query,a.type,a.top_k)
    else:result=status_report(db)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 2 if isinstance(result,dict) and result.get('readiness')=='incomplete_text_index' else 0

if __name__=='__main__':raise SystemExit(main())
