"""成绩资料优先、重大歧义才提问。所有裁决可回溯，未决值不进入计算。"""
import copy
import json
from pathlib import Path
from .common import ROOT, save, signature
from .direct_extract import labelled_row
from .fact_merge import merge_value, equivalent_key
from .grades import ALIASES, plain, parse_grades

VERSION = 'grade_first_minimal_v1'
IDENTITY = {'course_name','course_code','offering_code','class_name','term','academic_year','semester','grade'}
GRADE_FIELDS = IDENTITY | {'major','college','teacher','assessment_form','total_rule_text','total_max','pass_score'}
OPTIONAL_META = {'college','major','teacher','hours','credits','course_nature','course_category','report_date','standard_name'}

def enabled(case):
    return case.get('decision_policy',{}).get('mode') == VERSION

def policy_prompt():
    return (ROOT/'prompts/course_agent_rules.txt').read_text(encoding='utf-8')

def source_rank(source):
    if source.get('role') != 'current' or not source.get('sheets'): return 0
    name=Path(source['path']).stem
    if any(w in name for w in ('考勤','分析报告','统计')): return 0
    if any(w in name for w in ('成绩单','成绩表')): return 30
    if any(w in name for w in ('记分册','计分册')): return 20
    # 不仅按扩展名：普通名称必须含明确学号/总评表头。
    if any(any(plain(c) in ALIASES['student_id'] for c in row) and
           any(plain(c) in ALIASES['total'] for c in row) for sh in source.get('sheets',[]) for row in sh['rows'][:30]): return 10
    from .grade_layout import mappings
    if mappings(source):return 10
    return 0

def grade_candidates(sources,items=()):
    from .grade_layout import mappings
    result=[]
    for source in sources:
        rank=source_rank(source)
        if not rank: continue
        for mapping in mappings(source,items,generic_total=rank>=20):
            sheet=next(s for s in source['sheets'] if s['name']==mapping['sheet'])
            quotes={f'{source["id"]}:{mapping["sheet"]}:R{n}':' | '.join(f'C{i}={v}' for i,v in enumerate(sheet['rows'][n-1],1) if str(v).strip())
                    for n in range(mapping['header_start'],mapping['header_row']+1)}
            result.append({'mapping':mapping,'rank':rank,'source_refs':list(quotes),
                'quotes':quotes,
                'label':Path(source['path']).name+f' / {mapping["sheet"]}：表头{mapping["header_row"]}行，总评{mapping["total"]}列'})
        if any(r['mapping']['source_id']==source['id'] for r in result):continue
        for sh in source['sheets']:
            for n,row in enumerate(sh['rows'][:30],1):
                headers=[plain(c) for c in row]
                ids=[i+1 for i,c in enumerate(headers) if c in ALIASES['student_id']]
                totals=[i+1 for i,c in enumerate(headers) if c in ALIASES['total']]
                if not totals and rank>=20:
                    totals=[i+1 for i,c in enumerate(headers) if c=='成绩']
                if len(ids)!=1: continue
                for col in totals:
                    mapping={'source_id':source['id'],'sheet':sh['name'],'header_row':n,'student_id':ids[0],'total':col,'items':{}}
                    for key in ('name','status'):
                        cols=[i+1 for i,c in enumerate(headers) if c in ALIASES[key]]
                        if len(cols)==1: mapping[key]=cols[0]
                    for item in items:
                        cols=[i+1 for i,c in enumerate(headers) if c==plain(item['name'])]
                        if len(cols)==1 and cols[0]!=col and item.get('max_score') is not None and item.get('score_basis') in ('raw','converted'):
                            mapping['items'][item['id']]=cols[0]
                    ref=f'{source["id"]}:{sh["name"]}:R{n}'
                    result.append({'mapping':mapping,'rank':rank,'source_refs':[ref],
                        'quotes':{ref:' | '.join(f'C{i}={v}' for i,v in enumerate(row,1) if str(v).strip())},
                        'label':Path(source['path']).name+f' / {sh["name"]}：表头{n}行，总评{col}列'})
    return result

def choose_grades(case,sources,facts):
    if case.get('grade_tables'): return {'action':'explicit_mapping','candidates':[]}
    candidates=grade_candidates(sources,facts.get('items',[]))
    if not candidates: return {'action':'missing_mapping','candidates':[]}
    best=[c for c in candidates if c['rank']==max(x['rank'] for x in candidates)]
    # 同级多个表/列保留选择；不悄悄舍弃另一个班级或分项。
    if len(best)==1:
        case['grade_tables']=[best[0]['mapping']]
        return {'action':'selected_grade_source','selected':best[0],'candidates':candidates}
    if case.get('decision_policy',{}).get('duplicate_total_preference','integer')=='integer':
        from .grades import integer_duplicate
        preferred=integer_duplicate(best,sources)
        if preferred:
            case['grade_tables']=[preferred['mapping']]
            return {'action':'selected_integer_duplicate_total','selected':preferred,'candidates':best,
                    'rule':'用户默认采用相邻同名成绩中的整数列；不改变原始总评、不把中间汇总列重复相加'}
    return {'action':'ambiguous_mapping','candidates':best}

def is_critical(conflict):
    return not ((conflict['kind']=='metadata' and conflict['field'] in OPTIONAL_META) or
                (conflict['kind']=='methods' and conflict['field']=='description'))

def candidate_rank(conflict,candidate,sources):
    if conflict['kind']!='metadata' or conflict['field'] not in GRADE_FIELDS: return 0
    lookup={s['id']:s for s in sources}; ranks=[]
    for ref in candidate['source_refs']:
        source=lookup.get(ref.split(':')[0],{})
        if source.get('role')!='current': continue
        # 只有标签明确给出的字段享有来源优先；模型从旁边句子猜出的字段不加权。
        labels,_=labelled_row({'role':'current','text':candidate['quotes'].get(ref,'')})
        field=conflict['field']
        if field in labels and merge_value(field,labels[field],candidate['value'])[0]: ranks.append(source_rank(source))
    return max(ranks,default=0)

def _set(facts,c,value):
    row=facts['metadata'] if c['kind']=='metadata' else next(r for r in facts[c['kind']] if r['id']==c['id'])
    if value is None: row.pop(c['field'],None)
    else: row[c['field']]=copy.deepcopy(value)

def resolve_conflicts(case,facts,sources,client,run):
    decisions=[];remaining=[]
    for c in facts.get('conflicts',[]):
        ranks=[candidate_rank(c,x,sources) for x in c['candidates']]
        best=[i for i,r in enumerate(ranks) if r==max(ranks)]
        merged=None;ok=max(ranks)>0
        for i in best:
            equal,merged=merge_value(c['field'],merged,c['candidates'][i]['value']);ok=ok and equal
        if ok:
            _set(facts,c,merged);decisions.append({'conflict':c,'action':'grade_source_priority','selected':merged,'ranks':ranks});continue
        if not is_critical(c):
            _set(facts,c,None)
            facts.setdefault('data_quality_issues',[]).append('非关键字段待核对：'+c['field']+'；候选见decision_audit.json')
            decisions.append({'conflict':c,'action':'defer_noncritical'});continue
        # 只对真正未决的重大冲突请求一次模型复核；异常仍保留原始冲突。
        proposal=None
        if client is not None and case.get('decision_policy',{}).get('model_review',True):
            from .client import task,TaskValidationError
            from .structured import obj,text,choice,array
            refs=sorted({r for x in c['candidates'] for r in x['source_refs']})
            ids=[str(i+1) for i in range(len(c['candidates']))]
            schema=obj({'candidate':choice(['unresolved']+ids),'reason':text(300),
                        'evidence_ids':array(choice(refs),0,len(refs))},['candidate','reason','evidence_ids'])
            prompt={'task':'复核本轮字段矛盾，只选择已有候选编号或unresolved。列出明确支持所选候选的原文ID及理由；不能根据相似课程、出现次数、置信度决定数值。',
                    'course_name':case['course_name'],'conflict':c,'candidates_numbered':[{'number':str(i+1),**x} for i,x in enumerate(c['candidates'])]}
            def check(a):
                if a['candidate']!='unresolved':
                    allowed=set(c['candidates'][int(a['candidate'])-1]['source_refs'])
                    if not a['evidence_ids'] or not set(a['evidence_ids'])<=allowed: raise ValueError('裁决须引用选定候选的本轮证据')
            try:
                proposal=task(client,[{'role':'system','content':policy_prompt()},{'role':'user','content':json.dumps(prompt,ensure_ascii=False)}],
                              run/'records'/('decision_'+c['conflict_id']+'.json'),check,
                              {'task_kind':'conflict_review','case_id':case['id'],'group_id':case.get('group_id',case['id'])},retries=0,output_schema=schema)
            except TaskValidationError as exc: proposal={'candidate':'unresolved','reason':str(exc)}
        # 模型可以找出等价值；程序复核后才自动合并。真正不同的同级关键值仍需人选择。
        merged=None;equivalent=True
        for x in c['candidates']:
            same,merged=merge_value(c['field'],merged,x['value']);equivalent=equivalent and same
        if equivalent:
            _set(facts,c,merged);decisions.append({'conflict':c,'action':'verified_equivalent','selected':merged,'model_review':proposal})
        else:
            remaining.append(c);decisions.append({'conflict':c,'action':'ask_critical','model_review':proposal})
    facts['conflicts']=remaining
    return decisions

def used_states(grades):
    states=set()
    for student in grades['students']:
        if student['status']!='normal': states.add(student['status']);continue
        states.update(student['score_status'].values())
    return states

def prepare_review(case,facts,sources,client,run):
    """原case只在内存中调整，持久化effective快照供复核；不改用户原配置。"""
    from .human_review import build_questions,_question,_option
    decisions=resolve_conflicts(case,facts,sources,client,run)
    from .grading_schemes import prepare_scheme_question
    scheme_question,scheme_audit=prepare_scheme_question(case,facts,sources,client,run)
    decisions.append({'grading_scheme':scheme_audit})
    selection=choose_grades(case,sources,facts)
    if not case.get('evaluation_policy') and facts['metadata'].get('evaluation_policy'):
        case['evaluation_policy']=copy.deepcopy(facts['metadata']['evaluation_policy'])
    policy=case.setdefault('evaluation_policy',{})
    # include是正常数值的定义，不声称是从人数统计推断的学校制度。
    if policy.get('normal') is None:
        policy['normal']='include';policy['normal_origin']='engine_numeric_score_definition'
    question_case=copy.deepcopy(case)
    # 使用已有问答协议，只保留真实重大矛盾；状态问题后续按实际评分记录过滤。
    questions=build_questions(question_case,facts,sources)
    qs=[q for q in questions['questions'] if q['target']['type']=='conflict']
    if scheme_question:qs.append(scheme_question)
    from .grading_schemes import prepare_matrix_question
    matrix_question=prepare_matrix_question(case,facts)
    if matrix_question:qs.append(matrix_question)
    if selection['action']=='ambiguous_mapping':
        options=[{**_option([c['mapping']],c['source_refs'],c['quotes']),'label':c['label']} for c in selection['candidates']]
        qs.append(_question({'type':'grade_tables'},'多个同级正式成绩来源：请选择本轮统计依据',options,
                            explanation='自动规则无法唯一确认总评来源。这里只选择正式总评依据，不将总评分配为目标分项。'))
    grades=None;grade_error=None
    if case.get('grade_tables'):
        try: grades=parse_grades(case,sources,facts)
        except (ValueError,KeyError,StopIteration) as exc: grade_error=str(exc)
    elif selection['action']=='missing_mapping': grade_error='未识别明确学号与总评列；请核对source_manifest.json并在case填写grade_tables。'
    if grades:
        actual=used_states(grades)
        qs += [q for q in questions['questions'] if q['target']['type']=='evaluation_policy' and q['target']['state'] in actual]
    questions['questions']=qs
    questions['context_sha256']=signature({'case':case,'facts':facts,'sources':[(s['id'],s['sha256']) for s in sources],'questions':qs})
    save(run/'decision_audit.json',{'policy':VERSION,'decisions':decisions,'grade_selection':selection,'grade_error':grade_error})
    save(run/'user_questions.json',questions)
    return questions,grade_error
