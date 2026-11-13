"""可复用的人工选择协议。JSON问题/答案与终端展示分离，供后续Web接入。"""
import copy
import json
from datetime import datetime,timezone
from pathlib import Path
from .common import signature,save,load
from .readers import read_source

VERSION='human_review_v1'

def _option(value, refs=(), quotes=None):
    return {'option_id':'o_'+signature(value)[:20],'value':copy.deepcopy(value),
            'source_refs':list(refs),'quotes':copy.deepcopy(quotes or {})}

def _question(target,title,options,blocking=True,explanation=''):
    # 相同值归并显示来源；不是按来源个数投票。
    unique={}
    for option in options:
        key=option['option_id']
        if key not in unique:unique[key]=copy.deepcopy(option)
        else:
            unique[key]['source_refs']=sorted(set(unique[key]['source_refs']+option['source_refs']))
            unique[key]['quotes'].update(option['quotes'])
    q={'question_id':'q_'+signature(target)[:20],'target':target,'title':title,'options':list(unique.values()),
       'blocking':blocking,'allow_defer':True,'explanation':explanation}
    q['question_sha256']=signature(q)
    return q

def build_questions(case,facts,sources,policy=None):
    qs=[]
    for c in facts.get('conflicts',[]):
        qs.append(_question({'type':'conflict','kind':c['kind'],'id':c.get('id'),'field':c['field'],'conflict_id':c['conflict_id']},
            '确认 '+c['kind']+'.'+str(c.get('id') or '')+'.'+c['field'],
            [_option(v['value'],v['source_refs'],v['quotes']) for v in c['candidates']],
            explanation='候选来自本轮材料。选项都不正确时请暂不确定，补充材料后再处理。'))
    if policy:
        for field,reason in policy.config.get('deferred_fields',{}).items():
            opts=[]
            for action in policy.actions:
                if action['action']=='defer_field' and action['detail']['field']==field:
                    row=action['record'];opts.append(_option(action['detail']['value'],row['source_refs'],row['quotes']))
            if opts:qs.append(_question({'type':'metadata','field':field},'确认 '+{'course_nature':'课程性质'}.get(field,field),opts,
                                       blocking=False,explanation=reason))
    if not case.get('grade_tables'):
        opts=[]
        for source in sources:
            if source.get('role')!='current':continue
            for sheet in source.get('sheets',[]):
                for n,row in enumerate(sheet['rows'][:30],1):
                    headers=[''.join(str(x).split()) for x in row]
                    def columns(names):return [i+1 for i,h in enumerate(headers) if h in names]
                    ids=columns(('学号','学生学号'));totals=columns(('总评','总评成绩','最终成绩'))
                    if len(ids)!=1 or len(totals)!=1:continue
                    mapping={'source_id':source['id'],'sheet':sheet['name'],'header_row':n,'student_id':ids[0],'total':totals[0],'items':{}}
                    names=columns(('姓名','学生姓名'))
                    if len(names)==1:mapping['name']=names[0]
                    statuses=columns(('状态','考试状态','成绩状态'))
                    if len(statuses)==1:mapping['status']=statuses[0]
                    ref=f'{source["id"]}:{sheet["name"]}:R{n}'
                    option=_option([mapping],[ref],{ref:' | '.join(f'C{i}={v}' for i,v in enumerate(row,1))})
                    option['label']=Path(source['path']).name+' / '+sheet['name']+f'：表头第{n}行，学号第{ids[0]}列，总评第{totals[0]}列（仅确认总评，不分配分项分数）'
                    opts.append(option)
        if opts:qs.append(_question({'type':'grade_tables'},'选择本轮正式总评成绩表',opts,
            explanation='仅列出有明确学号和总评列的表。考勤表不作为总评成绩表；此选择不自动把总评分配给大作业分项。'))
    current=case.get('evaluation_policy') or facts.get('metadata',{}).get('evaluation_policy',{})
    labels={'normal':'正常有效评分','missing':'缺失评分','absent':'缺考','deferred':'缓考','exempt':'免修'}
    for state,label in labels.items():
        if current.get(state) in ('include','exclude','zero'):continue
        values=['include'] if state=='normal' else ['exclude','zero']
        qs.append(_question({'type':'evaluation_policy','state':state},'确认'+label+'的计算处理',[{**_option(v),'label':{'include':'按实际得分纳入计算','exclude':'排除此状态的样本','zero':'按零分纳入计算'}[v]} for v in values],
            explanation='这是由你确认的本轮评价规则，不是从人数统计推断的制度。include=纳入实际评分，exclude=排除该状态的样本，zero=按零分纳入。不了解制度请选暂不确定。'))
    # 即使选项不足也不猜：实际缺失条件由阶段4预检给出。
    return {'protocol':VERSION,'case_id':case['id'],'course_name':case['course_name'],
            'questions':qs,'source_paths':{s['id']:s['path'] for s in sources}}

def validate_answers(questions,answers):
    if answers.get('protocol')!=VERSION or answers.get('context_sha256')!=questions['context_sha256']:
        raise ValueError('人工选择文件与本轮材料/配置/事实不一致；不能跨任务套用')
    if not isinstance(answers.get('answers'),list):raise ValueError('answers必须为数组')
    lookup={q['question_id']:q for q in questions['questions']};seen=set()
    for a in answers['answers']:
        q=lookup.get(a.get('question_id'))
        if q is None or q['question_id'] in seen:raise ValueError('未知或重复人工选择问题')
        seen.add(q['question_id'])
        if a.get('question_sha256')!=q['question_sha256']:raise ValueError('候选选项已变化，不能复用旧选择')
        if a.get('status') not in ('selected','deferred'):raise ValueError('选择状态必须selected或deferred')
        if a['status']=='selected':
            if a.get('option_id') not in {o['option_id'] for o in q['options']}:raise ValueError('选择了未知候选')
            if not isinstance(a.get('reviewer'),str) or not a['reviewer'].strip():raise ValueError('缺少确认人')
            if not isinstance(a.get('reason'),str) or not a['reason'].strip():raise ValueError('缺少选择依据')
        elif a.get('option_id') is not None:raise ValueError('暂不确定不得携带选项')
    return answers

def collect_answers(questions,output,previous=None,interactive=False,input_fn=input,print_fn=print):
    answers=copy.deepcopy(previous) if previous is not None else {'protocol':VERSION,'context_sha256':questions['context_sha256'],'answers':[]}
    validate_answers(questions,answers)
    answers['session_status']='completed' if not interactive else 'in_progress'
    save(output,answers)
    if not interactive:return answers
    existing={a['question_id']:a for a in answers['answers']}
    pending=[q for q in questions['questions'] if existing.get(q['question_id'],{}).get('status')!='selected']
    print_fn('人工核对：输入数字选择；0/回车=暂不确定；v=完整来源；q=保存并退出。')
    reviewer=''
    try:
        if pending:reviewer=input_fn('确认人姓名或标识（仅记录本次选择，不代表整份报告审批）：').strip()
        for q in pending:
            print_fn('\n'+q['title']+'\n'+q['explanation'])
            for i,o in enumerate(q['options'],1):
                print_fn(f'{i}. '+o.get('label',json.dumps(o['value'],ensure_ascii=False)))
                for ref,quote in o['quotes'].items():
                    print_fn('   '+questions['source_paths'].get(ref.split(':')[0],ref)+' | '+ref+'\n   '+quote[:450]+('…（输入v查看全文）' if len(quote)>450 else ''))
            while True:
                value=input_fn('请选择：').strip().lower()
                if value=='q':
                    answers['session_status']='paused';save(output,answers);return answers
                if value=='v':
                    for i,o in enumerate(q['options'],1):print_fn(str(i)+'. '+json.dumps(o['quotes'],ensure_ascii=False,indent=2))
                    continue
                if value in ('','0'):
                    entry={'status':'deferred','option_id':None};break
                if not value.isdigit() or not 1<=int(value)<=len(q['options']):print_fn('请输入有效序号，或0暂不确定。');continue
                while not reviewer:reviewer=input_fn('请填写确认人：').strip()
                reason=input_fn('选择依据（如已核对本轮教学大纲；不了解请返回选择0）：').strip()
                if not reason:print_fn('未填写依据，尚未保存此选择。');continue
                entry={'status':'selected','option_id':q['options'][int(value)-1]['option_id'],'reviewer':reviewer,'reason':reason};break
            entry.update(question_id=q['question_id'],question_sha256=q['question_sha256'],confirmed_at=datetime.now(timezone.utc).isoformat())
            answers['answers']=[a for a in answers['answers'] if a['question_id']!=q['question_id']]+[entry]
            validate_answers(questions,answers);save(output,answers)
            print_fn('已保存当前选择。')
    except (EOFError,KeyboardInterrupt):
        print_fn('\n输入中断；已完成的选择已保存。')
        answers['session_status']='paused';save(output,answers);return answers
    answers['session_status']='completed';save(output,answers)
    return answers

def apply_answers(questions,answers,case,facts,sources,run):
    """仅应用服务端已构造的候选值；响应不能提交任意字段值或代码。"""
    validate_answers(questions,answers)
    lookup={q['question_id']:q for q in questions['questions']};confirmed=[];policy_changed=False
    prior_policy=case.get('evaluation_policy') or facts.get('metadata',{}).get('evaluation_policy',{})
    prior_policy_has_rules=any(prior_policy.get(k) in ('include','exclude','zero') for k in ('normal','missing','absent','deferred','exempt'))
    for a in answers['answers']:
        if a['status']!='selected':continue
        q=lookup[a['question_id']];o=next(o for o in q['options'] if o['option_id']==a['option_id']);t=q['target']
        value=copy.deepcopy(o['value'])
        if t['type']=='metadata':
            facts['metadata'][t['field']]=value
            facts['metadata']['source_refs']=sorted(set(facts['metadata'].get('source_refs',[])+o['source_refs']))
            facts['metadata'].setdefault('quotes',{}).update(o['quotes'])
            if t['field']=='course_nature':
                facts['data_quality_issues']=[s for s in facts.get('data_quality_issues',[]) if not s.startswith('课程性质待核对：')]
        elif t['type']=='conflict':
            row=facts['metadata'] if t['kind']=='metadata' else next(x for x in facts[t['kind']] if x['id']==t['id'])
            row[t['field']]=value
            facts['conflicts']=[c for c in facts['conflicts'] if c['conflict_id']!=t['conflict_id']]
            if t['kind']=='metadata' and t['field']=='evaluation_policy':
                case['evaluation_policy']={**copy.deepcopy(value),'source_refs':list(o['source_refs'])}
        elif t['type']=='grading_matrix':
            from .grading_schemes import apply_matrix
            apply_matrix(case,facts,value,'user_confirmation')
        elif t['type']=='grading_scheme':
            from .grading_schemes import apply_scheme
            apply_scheme(case,facts,value,'user_confirmation')
        elif t['type']=='grade_tables':case['grade_tables']=value
        elif t['type']=='evaluation_policy':
            if not case.get('evaluation_policy'):case['evaluation_policy']=copy.deepcopy(facts.get('metadata',{}).get('evaluation_policy',{}))
            case['evaluation_policy'][t['state']]=value;policy_changed=True
        else:raise ValueError('未知人工选择目标')
        confirmed.append({**a,'target':t,'selected_value':value,'selected_evidence':o})
    if policy_changed:
        # 用户确认本身作为独立规则来源，不能谎称是成绩单人数统计给出的规则。
        sid='human_policy_'+questions['context_sha256'][:12]
        if any(s['id']==sid for s in sources):raise ValueError('人工制度来源ID已存在')
        file=run/'user_evaluation_policy.txt'
        provenance={'说明':'以下为用户确认的本轮评价制度，仅对已选择状态生效；未确认状态不作默认处理。',
                    'case_id':case['id'],'evaluation_policy':{k:v for k,v in case['evaluation_policy'].items() if k!='source_refs'},
                    'confirmations':[a for a in confirmed if a['target']['type']=='evaluation_policy']}
        file.write_text(json.dumps(provenance,ensure_ascii=False)+'\n',encoding='utf-8')
        cfg={'id':sid,'path':str(file),'role':'rule','kind':'document'}
        source=read_source(cfg);sources.append(source);case['sources'].append(cfg)
        refs=list(prior_policy.get('source_refs',[])) if prior_policy_has_rules else []
        case['evaluation_policy']['source_refs']=sorted(set(refs+[sid+':L1']))
        facts['metadata']['evaluation_policy']=copy.deepcopy(case['evaluation_policy'])
        facts['metadata']['source_refs']=sorted(set(facts['metadata'].get('source_refs',[])+[sid+':L1']))
        facts['metadata'].setdefault('quotes',{})[sid+':L1']=source['blocks'][0]['text']
    facts['human_decisions']=confirmed
    facts.setdefault('review',{})['approved']=False
    facts['review']['note']='人工选择仅确认对应字段/规则；整份事实和报告仍待审核。'
    save(run/'human_decisions.json',{'protocol':VERSION,'context_sha256':questions['context_sha256'],
                                  'decisions':confirmed,'unanswered':[q['question_id'] for q in questions['questions'] if not any(a['question_id']==q['question_id'] for a in confirmed)]})
    return confirmed
