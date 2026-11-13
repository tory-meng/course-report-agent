"""同一留出集调用指定服务，记录JSON/引用/结构检查及原始输出，不把loss当质量分。"""
import json
from report_agent.common import load,path,save,read_rows,signature
from report_agent.client import Client,json_answer
from report_agent.structured import validate_schema

def evaluate(runtime,data,out,task_ids=None):
    cfg=load(path(runtime));client=Client(cfg['model']);rows=read_rows(path(data));results=[]
    if task_ids:
        wanted=set(task_ids)
        if not wanted<={r['id'] for r in rows}:raise ValueError('评测ID不在输入数据中')
        rows=[r for r in rows if r['id'] in wanted]
    for row in rows:
        expected=json.loads(row['messages'][-1]['content'])
        raw=client.request(row['messages'][:-1],output_schema=row['output_schema']) if row.get('output_schema') else client.request(row['messages'][:-1])
        issues=[];actual=None;diagnostics={};schema_pass=False
        try:
            if raw.get('limit_reached') or raw.get('finish_reason') in ('length','repetition','prompt_echo'):raise ValueError('输出截断或重复')
            actual=json_answer(raw['text'])
            if set(actual)!=set(expected):issues.append('输出字段与审核样例不同')
            if 'paragraphs' in expected and (not isinstance(actual.get('paragraphs'),list) or any(not isinstance(p,str) for p in actual.get('paragraphs',[]))):issues.append('段落数组结构错误')
            if 'source_refs' in expected and not set(actual.get('source_refs',[]))<=set(row.get('evidence_refs',expected['source_refs'])):issues.append('引用不在本课任务依据内')
            if row.get('output_schema'):
                validate_schema(actual,row['output_schema']);schema_pass=True
                from .task_metrics import diagnose
                task_issues,diagnostics=diagnose(row,actual);issues+=task_issues
            else:schema_pass=not any(s in issue for issue in issues for s in ('字段','结构'))
        except (ValueError,TypeError) as e:issues.append(str(e))
        results.append({'id':row['id'],'task_kind':row.get('task_kind'),'issues':issues,'json_schema_pass':schema_pass,
                        'task_constraint_pass':not issues,'exact_match':actual==expected,
                        'input_fingerprint':signature(row['messages'][:-1]),'diagnostics':diagnostics,'actual':actual,'expected':expected,'raw':raw})
        save(path(out),{'model':client.identity,'dataset_fingerprint':signature(rows),'rows':results,'status':'running'})
    result={'model':client.identity,'dataset_fingerprint':signature(rows),'rows':results,'status':'complete',
            'summary':{'count':len(rows),'structural_pass':sum(not r['issues'] for r in results),
                       'json_schema_pass':sum(r['json_schema_pass'] for r in results),'task_constraint_pass':sum(r['task_constraint_pass'] for r in results),
                       'exact_match':sum(r['exact_match'] for r in results)},
            'scope':'JSON/schema与任务输入约束分开统计；structural_pass仅保留兼容。代理参考一致不等于语义准确率，不证明事实或完整报告合格。'}
    save(path(out),result);return result['summary']
