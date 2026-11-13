#!/usr/bin/env python3
"""离线审计旧抽取记录；不修改原run、不调用模型、不自动批准训练。"""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from report_agent.common import load,save,path
from report_agent.facts import validate_extracted
from report_agent.readers import evidence_index
from report_agent.extraction_quality import formal_goal_records,validate_quality,route
from report_agent.grading_schemes import collect_schemes

def audit(run,out):
    run=path(run);out=path(out);sources=load(run/'source_manifest.json');evidence=evidence_index(sources);rows=[]
    for file in sorted((run/'records').glob('extract_*.json')):
        record=load(file)
        if not record.get('messages'):continue
        try:
            prompt=json.loads(next(m['content'] for m in record['messages'] if m['role']=='user'))
            blocks=[{**b,'_quality':True} for b in prompt['evidence']]
            # 旧批次含多个类别；仍检查其中正式目标表覆盖和每条正文的引用。
            answer=record.get('answer')
            if answer is None:
                raw=record.get('attempts',[])[-1].get('raw',{})
                answer=json.loads(raw.get('text',''))
            validate_extracted(answer,blocks,evidence)
            status='checks_passed_not_approved';error=None
        except (ValueError,KeyError,StopIteration,IndexError) as exc:status='needs_repair';error=str(exc)
        rows.append({'record':file.name,'previous_ok':record.get('ok',False),'status':status,'error':error})
    goals,_=formal_goal_records(list(evidence.values()));schemes=collect_schemes(sources)
    result={'run':str(run),'model_requests':0,'records':rows,'needs_repair':sum(r['status']=='needs_repair' for r in rows),
            'formal_goals_found':sum(r['kind']=='goals' for r in goals),'formal_goal_records':goals,
            'grading_schemes':schemes,'note':'规则检查通过不等于人工批准；旧结果不得仅因ok=true直接导入新流程。新读取器会补充表格坐标。'}
    save(out,result);return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',required=True);p.add_argument('--out',required=True)
    a=p.parse_args();r=audit(a.run,a.out);print(json.dumps({k:r[k] for k in ('model_requests','needs_repair','formal_goals_found')},ensure_ascii=False,indent=2));print('核对结果：',path(a.out))
if __name__=='__main__':main()
