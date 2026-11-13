#!/usr/bin/env python3
"""离线重放已完成的抽取记录，输出可定位冲突及续跑case；不请求模型。"""
import argparse
import copy
import json
import shlex
import sys
import zipfile
from datetime import datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from report_agent.common import ROOT, path, load, save, digest, signature, require_empty
from report_agent.readers import evidence_index
from report_agent.facts import LISTS, plan_chunks, validate_extracted, validate_facts
from report_agent.direct_extract import split_direct
from report_agent.structured import extraction_schema, validate_schema
from report_agent.fact_merge import FactMerger


def select_run(value, case):
    if value != 'auto':return path(value)
    matches=[]
    for manifest in (ROOT/'outputs').glob('*/run_manifest.json'):
        try:
            m=load(manifest);folder=manifest.parent
            if m.get('case_id')==case['id'] and (folder/'facts.extracted.json').is_file():
                matches.append(folder)
        except (ValueError,OSError):continue
    if not matches:raise ValueError('outputs下未找到该case已完成全量抽取的目录；用--run指定实际目录')
    return max(matches,key=lambda d:(d/'facts.extracted.json').stat().st_mtime_ns)


def replay_task(folder, task_id, blocks, evidence, case, template_id):
    direct,remaining=split_direct(blocks)
    if direct:
        p=folder/'records'/(task_id+'.rules.json')
        r=load(p)
        if r.get('ok') is not True or r.get('input_hash')!=signature(blocks) or r.get('answer')!={'records':direct}:
            raise ValueError(task_id+'：规则记录与当前片段不一致，不能无依据复用')
        records=validate_extracted(r['answer'],blocks,evidence)
        if remaining:records+=replay_task(folder,task_id+'_remaining',remaining,evidence,case,template_id)
        return records
    schema=extraction_schema(blocks,blocks[0]['extract_kinds'])
    p=folder/'records'/(task_id+'.json')
    r=load(p) if p.is_file() else {}
    if r.get('ok') is True:
        if r.get('task_kind')!='extract' or r.get('case_id')!=case['id'] or r.get('template_id')!=template_id:
            raise ValueError(task_id+'：记录身份不符')
        messages=r['messages'];old_schema=r.get('output_schema')
        if not old_schema or signature({'messages':messages,'output_schema':old_schema})!=r.get('request_hash'):
            raise ValueError(task_id+'：原任务记录校验和不符')
        prompt=json.loads(messages[-1]['content'])
        expected=[{k:v for k,v in b.items() if k!='extract_kinds'} for b in blocks]
        if prompt.get('evidence')!=expected or prompt.get('course_name')!=case['course_name'] or prompt.get('allowed_kinds')!=blocks[0]['extract_kinds']:
            raise ValueError(task_id+'：原任务片段/课程/类别与当前资料不符')
        validate_schema(r['answer'],schema)
        return validate_extracted(r['answer'],blocks,evidence)
    split_path=folder/'records'/(task_id+'.split.json')
    if split_path.is_file():
        split=load(split_path)
        if len(blocks)>1:
            mid=len(blocks)//2;expected=[blocks[:mid],blocks[mid:]]
        else:
            b=blocks[0];mid=len(b['text'])//2
            expected=[[{**b,'text':b['text'][:mid]}],[{**b,'text':b['text'][mid:]}]]
        if split.get('children')!=expected:raise ValueError(task_id+'：拆分范围与原文不一致')
        result=[]
        for j,child in enumerate(expected,1):
            result+=replay_task(folder,task_id+f'_{j}',child,evidence,case,template_id)
        return result
    raise ValueError(task_id+'：缺少通过校验的抽取记录；本工具不会触发模型重跑')


def apply_decisions(facts, report, decisions):
    if decisions.get('report_sha256')!=signature(report):raise ValueError('裁决文件与本次冲突明细不匹配')
    reviewer=decisions.get('reviewer','').strip()
    pending={c['conflict_id']:c for c in report['conflicts']};done=[]
    for d in decisions.get('decisions',[]):
        if d.get('candidate') is None:continue
        if not reviewer or not isinstance(d.get('reason'),str) or not d['reason'].strip():raise ValueError('裁决须填写reviewer和每项reason')
        c=pending.get(d.get('conflict_id'))
        if c is None:raise ValueError('重复或未知冲突ID')
        n=d['candidate']
        if type(n) is not int or not 1<=n<=len(c['candidates']):raise ValueError('candidate必须是明细中的1起始候选编号')
        record=facts['metadata'] if c['kind']=='metadata' else next(x for x in facts[c['kind']] if x['id']==c['id'])
        candidate=c['candidates'][n-1];record[c['field']]=copy.deepcopy(candidate['value'])
        done.append({**d,'reviewer':reviewer,'selected_evidence':candidate})
        pending.pop(c['conflict_id'])
    facts['conflicts']=[c for c in facts['conflicts'] if c['conflict_id'] in pending]
    facts['conflict_resolutions']=done
    return done


def recover(case_path, run_value, out=None, decisions_path=None, policy_path=None, interactive=False, answers_path=None):
    case=load(path(case_path));folder=select_run(run_value,case)
    print('复用抽取目录：',folder,flush=True)
    manifest=load(folder/'run_manifest.json');sources=load(folder/'source_manifest.json')
    if manifest.get('case_id')!=case['id'] or manifest.get('offline'):raise ValueError('不是当前case的在线抽取任务')
    if not (folder/'facts.extracted.json').is_file():raise ValueError('尚未完成全量抽取，不接受探测/局部结果')
    lookup={s['id']:s for s in sources}
    if len(lookup)!=len(sources) or set(lookup)!={s['id'] for s in case['sources']}:raise ValueError('当前来源清单已改变')
    for s in case['sources']:
        old=lookup[s['id']]
        if s.get('role','current')!=old['role'] or s.get('kind','document')!=old['kind']:
            raise ValueError('来源用途改变：'+s['id'])
        sha=digest(path(s['path']))
        if sha!=old['sha256'] or sha!=manifest['inputs'].get(s['id']):raise ValueError('原始材料已改变：'+s['id'])
    # 此处仅核对已保存的模板身份，不改变用户模板/原case/原runtime。
    runtime=copy.deepcopy(manifest['runtime_config'])
    registry=load(path(runtime['template_registry']))
    entry=registry['templates'][case.get('report_type','course')]
    cp=path(entry['contract']);contract=load(cp)
    hashes={'contract':digest(cp),**{k:digest(cp.parent/v) for k,v in contract['assets'].items()}}
    if hashes!=manifest['template_hashes'] or contract['template_id']!=manifest['template_id']:
        raise ValueError('模板已改变，不能把旧抽取当作当前模板已完成的抽取')
    _,chunks=plan_chunks(case,sources);plan=load(folder/'extraction_plan.json');audit=load(folder/'extraction_audit.json')
    expected_ids=[f'extract_{i:04}' for i in range(len(chunks))]
    if plan.get('root_batches')!=len(chunks) or [x['id'] for x in audit['batches']]!=expected_ids:
        raise ValueError('全量批次未完成或分批配置改变')
    target=path(out) if out else ROOT/'outputs'/('facts_recovery_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f'))
    require_empty(target)
    facts={k:[] for k in LISTS};facts.update(metadata={},conflicts=[],review={'approved':False,'reviewer':''})
    evidence=evidence_index(sources);merger=FactMerger(facts,evidence)
    policy=None
    if policy_path:
        from report_agent.fact_policy import FactPolicy
        policy=FactPolicy(load(path(policy_path)),case,evidence)
    for i,blocks in enumerate(chunks):
        rid=expected_ids[i];records=replay_task(folder,rid,blocks,evidence,case,manifest['template_id'])
        for record in records:
            record=policy.apply(record) if policy else record
            if record:merger.add(record,rid)
        print(f'[{i+1}/{len(chunks)}] 离线复核 {rid}：{len(records)}条；模型请求0次',flush=True)
    active_sources=policy.finish(facts,sources,target) if policy else sources
    report=merger.write_review(target,sources)
    save(target/'decisions.example.json',{'report_sha256':signature(report),'reviewer':'',
        'decisions':[{'conflict_id':c['conflict_id'],'candidate':None,'reason':''} for c in report['conflicts']]})
    if decisions_path:apply_decisions(facts,report,load(path(decisions_path)))
    # 问题JSON总是导出；只有显式--interactive才读终端输入。
    from report_agent.human_review import build_questions,collect_answers,apply_answers
    questions=build_questions(case,facts,active_sources,policy)
    questions['context_sha256']=signature({'protocol':questions['protocol'],'case':case,
        'sources':manifest['inputs'],'template':manifest['template_hashes'],
        'policy':policy.config if policy else None,'facts':facts,'questions':questions})
    save(target/'user_questions.json',questions)
    previous=load(path(answers_path)) if answers_path else None
    print('人工问题：',target/'user_questions.json',flush=True)
    print('选择自动保存：',target/'user_answers.json',flush=True)
    answers=collect_answers(questions,target/'user_answers.json',previous,interactive)
    apply_answers(questions,answers,case,facts,active_sources,target)
    sources=sources+[ss for ss in active_sources if ss['id'] not in {x['id'] for x in sources}]
    paused=answers.get('session_status')=='paused'
    save(target/'facts.recovered.json',facts)
    result={'source_run':str(folder),'case_id':case['id'],'batches_reused':len(chunks),'model_requests':0,
        'auto_merged_fields':report['auto_merged_count'],'remaining_conflicts':len(facts['conflicts']),
        'remaining_issues':facts.get('data_quality_issues',[]),
        'status':'awaiting_user' if paused else ('needs_review' if facts['conflicts'] else 'pending_validation')}
    if not facts['conflicts'] and not paused:
        try:validate_facts(facts,sources,case['course_name'])
        except Exception as exc:result.update(status='invalid_facts',error=str(exc))
        else:
            result['status']='ready_for_generation'
            newcase=copy.deepcopy(case);newcase['facts_path']=str(target/'facts.recovered.json')
            if policy:
                retained={s['id'] for s in active_sources}
                if any(m['source_id'] not in retained for m in newcase.get('grade_tables',[])):
                    raise ValueError('排除来源仍被grade_tables引用，必须先核对成绩映射')
                newcase['sources']=[s for s in newcase['sources'] if s['id'] in retained]
            save(target/'case.recovered.json',newcase)
            runtime['reference_library']={'enabled':False}
            save(target/'runtime.recovered.json',runtime)
            command='python run_report.py run --case '+shlex.quote(str(target/'case.recovered.json'))+' --runtime '+shlex.quote(str(target/'runtime.recovered.json'))+' --out '+shlex.quote(str(target/'report'))
            (target/'next_command.txt').write_text(command+'\n',encoding='utf-8')
            result['next_command']=command
            if policy or interactive or answers_path:
                # 在再花模型时间之前，先本地验证后续成绩读取/计算入口。
                from report_agent.grades import parse_grades
                from report_agent.compute import calculate
                preflight={'status':'passed','model_requests':0,'grade_tables':newcase.get('grade_tables',[]),
                    'evaluation_policy':newcase.get('evaluation_policy') or facts['metadata'].get('evaluation_policy',{})}
                try:
                    effective=copy.deepcopy(newcase)
                    effective['evaluation_policy']=preflight['evaluation_policy']
                    grades=parse_grades(effective,active_sources,facts)
                    calc=calculate(effective,facts,grades)
                    preflight.update(students=grades['registered_count'],calculation_issues=calc['issues'])
                except (ValueError,KeyError,TypeError,StopIteration) as exc:
                    preflight.update(status='blocked',error=str(exc))
                    result['status']='facts_ready_generation_blocked'
                    result['next_stage_error']=str(exc)
                    result.pop('next_command',None)
                    (target/'next_command.txt').unlink()
                    preflight['sheet_headers']=[{'source_id':ss['id'],'kind':ss['kind'],'sheet':sheet['name'],
                        'row':i,'cells':row} for ss in active_sources for sheet in ss.get('sheets',[])
                        for i,row in enumerate(sheet['rows'][:30],1) if any(str(x).strip() in ('学号','学生学号') for x in row)]
                save(target/'next_stage_check.json',preflight)
    save(target/'recovery_result.json',result)
    with zipfile.ZipFile(target/'conflict_review.zip','w',zipfile.ZIP_DEFLATED) as z:
        for name in ('fact_conflicts.json','fact_conflicts.txt','facts.recovered.json','recovery_result.json','decisions.example.json'):
            z.write(target/name,name)
        for extra in ('fact_policy_audit.json','next_stage_check.json','user_questions.json','user_answers.json','human_decisions.json'):
            if (target/extra).is_file():z.write(target/extra,extra)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
    print('核对目录：',target,flush=True)
    if result['status']!='ready_for_generation':
        print('查看user_questions.json、fact_conflicts.txt或next_stage_check.json；可带--interactive选择，或用--answers已有user_answers.json恢复选择。无需重新抽取。',flush=True)
    return 0 if result['status']=='ready_for_generation' else 2


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--case',default='cases/course_001.json')
    p.add_argument('--run',required=True,help='原抽取输出目录，或auto选择outputs下该case最近完成抽取的目录')
    p.add_argument('--out',help='新的核对目录；省略则自动生成')
    p.add_argument('--decisions',help='据原文填写的裁决JSON；不填写则只做保守自动合并')
    p.add_argument('--policy',help='明确指定的当前案例修复策略JSON，不自动应用到其他课程')
    p.add_argument('--interactive',action='store_true',help='终端展示待核对问题并逐项选择，0暂不确定、q保存退出')
    p.add_argument('--answers',help='复用与本轮证据绑定的user_answers.json；可同时加--interactive继续未选项')
    a=p.parse_args()
    try:return recover(a.case,a.run,a.out,a.decisions,a.policy,a.interactive,a.answers)
    except (ValueError,KeyError,OSError) as exc:
        print('离线恢复停止：'+str(exc),file=sys.stderr);return 2

if __name__=='__main__':raise SystemExit(main())
