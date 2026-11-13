"""完整评分方案候选及交互。相似课程用于解释划分，不能证明本轮权重。"""
import copy
import json
import re
from .common import signature,save
from .extraction_quality import cells,table_key

LABELS=('平时成绩','平时考核','项目考核成绩','项目考核','验收答辩成绩','验收答辩','设计报告成绩','设计报告','报告成绩','过程性考核','终结性考核','期末成绩')
def norm_name(name):return name.strip() # 不擅自把报告、验收报告、设计报告视为同一实体。
def collect_schemes(sources):
    results=[]
    def add(components,blocks,origin,children=None):
        if len(components)<2 or abs(sum(x['weight'] for x in components)-1)>1e-6:return
        refs=list(dict.fromkeys(b['id'] for b in blocks));quotes={b['id']:b['text'] for b in blocks}
        identity={'components':components,'source_refs':refs}
        results.append({'id':'scheme_'+signature(identity)[:16],'level':'course_total','components':components,
            'source_refs':refs,'quotes':quotes,'origin':origin,'children':children or [],'status':'candidate'})
    for source in sources:
        if source.get('role') not in ('current','rule'):continue
        blocks=source['blocks'];tables={}
        for b in blocks:
            key=table_key(b)
            if key:tables.setdefault(key,[]).append(b)
            text=b['text'];parts=[]
            for name in sorted(LABELS,key=len,reverse=True):
                pattern=re.escape(name)+r'\s*占[^。；;|\d]{0,20}(\d+(?:\.\d+)?)\s*[%％]'
                m=re.search(pattern,text)
                if m and not any(name in x['name'] for x in parts):parts.append({'name':name,'weight':float(m[1])/100})
            if len(parts)>=2:add(parts,[b],'explicit_percentage_sentence')
            if '百分制' in text and ('总评' in text or '成绩组成' in text):
                parts=[]
                for name,n in re.findall(r'([\u4e00-\u9fff]+)(?:（|\()满分\s*(\d+(?:\.\d+)?)\s*分(?:）|\))',text):
                    name=re.sub(r'^.*[=＝]', '',name)
                    parts.append({'name':name,'weight':float(n)/100})
                if len(parts)>=2:add(parts,[b],'explicit_hundred_point_composition')
        for rows in tables.values():
            # 表头必须明确“方式”和单独“权重”列；目标内部系数列不作总评。
            for idx,h in enumerate(rows):
                hc=[re.sub(r'\s+','',x) for x in cells(h)]
                if not hc or hc[0] not in ('方式','考核方式'):continue
                if len(hc)>1 and hc[1]=='权重':
                    parts=[];used=[h]
                    for b in rows[idx+1:]:
                        c=cells(b)
                        if len(c)<2:continue
                        if c[0].strip() in ('权重','合计','总计','教学目标达成度'):break
                        if re.fullmatch(r'0?\.\d+|1(?:\.0+)?',c[1].strip()):
                            parts.append({'name':c[0].strip(),'weight':float(c[1])});used.append(b)
                    add(parts,used,'table_explicit_course_weights');break
            # 两层占比表：末列是全课程占比，倒数第二列是组内占比。
            if not any('占比' in b['text'] for b in rows[:2]):continue
            groups={};internal={};used=[]
            for b in rows[2:]:
                c=cells(b)
                if len(c)<4 or '合计' in c[0]:continue
                tail=[x.strip() for x in c[-2:]]
                if not all(re.fullmatch(r'\d+(?:\.\d+)?[%％]',x) for x in tail):continue
                group=c[0].strip()
                if group not in ('过程性考核','终结性考核','平时考核','项目考核'):continue
                value=float(tail[1][:-1])/100
                if group in groups and groups[group]!=value:continue
                groups[group]=value;used.append(b)
                if '小计' not in c[1]:internal.setdefault(group,[]).append({'name':c[1].strip(),'within_parent_weight':float(tail[0][:-1])/100})
            add([{'name':k,'weight':v} for k,v in groups.items()],rows[:2]+used,'hierarchical_table',
                [{'parent':k,'components':v} for k,v in internal.items()])
    unique={x['id']:x for x in results}
    return list(unique.values())

def apply_scheme(case,facts,value,origin):
    candidate=next((c for c in facts.get('grading_schemes',[]) if c['id']==value['id']),None)
    if candidate is None or candidate!=value:raise ValueError('评分方案不在当前证据候选中')
    case['grading_scheme_id']=candidate['id']
    facts['grading_scheme_resolution']={'scheme_id':candidate['id'],'origin':origin,'status':'selected',
                                          'scope':'仅总评构成；不确认目标系数、分项映射或缺考制度'}
    meta=facts['metadata'];meta['total_rule_text']='；'.join(x['name']+'占'+format(x['weight']*100,'g')+'%' for x in candidate['components'])
    meta['source_refs']=sorted(set(meta.get('source_refs',[])+candidate['source_refs']))
    meta.setdefault('quotes',{}).update(candidate['quotes'])
    # 只向完全同名考核方式绑定总评比例，未匹配项不创造映射。
    weights={x['name']:x['weight'] for x in candidate['components']}
    rows=facts.setdefault('assessments',[])
    for name in weights:
        if not any(a['name']==name for a in rows):
            rows.append({'id':'A_'+signature(re.sub(r'\s+','',name))[:12],'name':name,
                         'source_refs':list(candidate['source_refs']),'quotes':copy.deepcopy(candidate['quotes'])})
    for a in rows:
        a.pop('total_weight',None)
        if a['name'] in weights:a['total_weight']=weights[a['name']]
    for item in facts.get('items',[]):item.pop('total_weight',None)

def prepare_scheme_question(case,facts,sources,client,run):
    from .human_review import _question,_option
    candidates=facts.get('grading_schemes',[])
    if not candidates:return None,{'action':'no_explicit_total_scheme','policy':'保留缺项，不从参考课程补比例'}
    chosen=case.get('grading_scheme_id')
    if chosen:
        candidate=next((x for x in candidates if x['id']==chosen),None)
        if candidate is None:raise ValueError('已选择的评分方案与本轮材料不一致')
        apply_scheme(case,facts,candidate,'user_or_case_selection');return None,{'action':'selected','scheme_id':chosen}
    keys={signature(sorted(c['components'],key=lambda x:x['name'])) for c in candidates}
    if len(keys)==1:
        apply_scheme(case,facts,candidates[0],'single_equivalent_current_scheme')
        return None,{'action':'unique_current_scheme','scheme_id':candidates[0]['id']}
    # 当记分册明确写出完整方案且无同级分歧，可按证据优先自动选择。
    from .decision_policy import source_rank
    ranks={s['id']:source_rank(s) for s in sources}
    ranked=[max((ranks.get(r.split(':')[0],0) for r in c['source_refs']),default=0) for c in candidates]
    high=max(ranked);best=[c for c,r in zip(candidates,ranked) if r==high]
    if high>0 and len({signature(sorted(c['components'],key=lambda x:x['name'])) for c in best})==1:
        apply_scheme(case,facts,best[0],'explicit_grade_source_priority')
        return None,{'action':'explicit_grade_source_priority','scheme_id':best[0]['id']}
    proposal=None;lookup=None
    resolver=getattr(client,'reference_resolver',None) if client else None
    if resolver:lookup=resolver('实践课程 考核方式 平时 项目 报告 答辩 总评比例 分层权重',[],purpose='scheme_decision',task_id='grading_scheme')
    if client and case.get('decision_policy',{}).get('model_review',True):
        from .client import task,TaskValidationError
        from .structured import obj,choice,text,array
        refs=sorted({r for c in candidates for r in c['source_refs']})
        reference_ids=[h['reference_id'] for h in (lookup or {}).get('hits',[])]
        schema=obj({'candidate':choice(['unresolved']+[c['id'] for c in candidates]),'reason':text(500),
                    'evidence_ids':array(choice(refs),0,len(refs)),
                    'reference_id':choice(['none']+reference_ids)},['candidate','reason','evidence_ids','reference_id'])
        prompt={'task':'选择最适合解释本轮考核划分的参考课程，并推荐一个本轮完整方案或unresolved。解释层级、适用依据和未决问题。参考相似不能证明比例正确，未获本轮依据时须用户确认。',
                'course_name':case['course_name'],'candidates':candidates,'reference_examples':lookup}
        def check(a):
            if a['candidate']!='unresolved':
                selected=next(c for c in candidates if c['id']==a['candidate'])
                if not a['evidence_ids'] or not set(a['evidence_ids'])<=set(selected['source_refs']):raise ValueError('方案推荐必须引用该方案的本轮来源')
        try:
            proposal=task(client,[{'role':'system','content':'你是课程评分方案审核助手。只使用提供的候选和证据；返回JSON。'},
                                 {'role':'user','content':json.dumps(prompt,ensure_ascii=False)}],
                run/'records'/'grading_scheme_decision.json',check,{'task_kind':'scheme_review','case_id':case['id'],
                    'group_id':case.get('group_id',case['id'])},retries=0,output_schema=schema)
        except TaskValidationError as e:proposal={'candidate':'unresolved','reason':str(e),'reference_id':'none'}
    options=[]
    for c in candidates:
        o=_option(c,c['source_refs'],c['quotes']);o['label']=' + '.join(x['name']+' '+format(x['weight']*100,'g')+'%' for x in c['components'])
        if proposal and c['id']==proposal['candidate']:o['label']+='（模型推荐，待确认）'
        options.append(o)
    explanation='材料给出不同完整评分方案。先确定本轮适用方案，不跨方案混用；0=暂不确定。参考课程只解释划分，不直接提供本轮比例。'
    if proposal:explanation+='\n模型理由：'+proposal['reason']
    if lookup:
        for h in lookup['hits']:
            if proposal and h['reference_id']==proposal.get('reference_id'):explanation+='\n模型所选参考：'+h['file']+' | '+h['locator']
    q=_question({'type':'grading_scheme'},'确认本轮完整评分方案',options,explanation=explanation)
    return q,{'action':'ask_scheme','model_review':proposal,'reference_lookup':lookup,'candidates':candidates}

def collect_matrix_record(facts,record):
    """方式→目标系数按原表成组，防止逐字段裁决拼成不存在的矩阵。"""
    v=record['value'];tables={re.sub(r'R\d+$','',r) for r in v['source_refs'] if re.search(r'(?::T\d+|:[^:]+:)R\d+$',r)}
    if len(tables)!=1:raise ValueError('目标系数必须定位到唯一原表；不能引用多个表拼接矩阵')
    table=next(iter(tables));key='matrix_'+signature(table)[:16]
    matrices=facts.setdefault('grading_matrix_candidates',[])
    current=next((m for m in matrices if m['id']==key),None)
    if current is None:
        current={'id':key,'scope':'assessment_to_goal','table':table,'records':[],'source_refs':[],'quotes':{}};matrices.append(current)
    prev=next((x for x in current['records'] if x['assessment_id']==v['assessment_id'] and x['goal_id']==v['goal_id']),None)
    if prev and prev['weight']!=v['weight']:raise ValueError('同一表格同一方式/目标提取出不同系数，须修复抽取')
    if not prev:current['records'].append(copy.deepcopy(v))
    current['source_refs']=sorted(set(current['source_refs']+v['source_refs']));current['quotes'].update(v['quotes'])

def apply_matrix(case,facts,value,origin):
    candidate=next((c for c in facts.get('grading_matrix_candidates',[]) if c['id']==value['id']),None)
    if candidate is None or candidate!=value:raise ValueError('目标矩阵不在当前证据候选中')
    case['grading_matrix_id']=candidate['id'];facts['assessment_goal_weights']=copy.deepcopy(candidate['records'])
    facts['grading_matrix_resolution']={'matrix_id':candidate['id'],'origin':origin,
        'scope':'仅确认方式到目标系数；不自动推出逐学生目标成绩或指标点聚合规则'}

def prepare_matrix_question(case,facts):
    from .human_review import _question,_option
    candidates=facts.get('grading_matrix_candidates',[])
    if not candidates:return None
    chosen=case.get('grading_matrix_id')
    if chosen:
        value=next((c for c in candidates if c['id']==chosen),None)
        if value is None:raise ValueError('已选择的目标系数矩阵不在本轮候选中')
        apply_matrix(case,facts,value,'user_or_case_selection');return None
    def key(c):return signature(sorted([(v['assessment_id'],v['goal_id'],v['weight']) for v in c['records']]))
    if len({key(c) for c in candidates})==1:
        apply_matrix(case,facts,candidates[0],'single_equivalent_current_matrix');return None
    names={a['id']:a['name'] for a in facts.get('assessments',[])};options=[]
    for c in candidates:
        o=_option(c,c['source_refs'],c['quotes']);o['label']=c['table']+'：'+'；'.join(names.get(v['assessment_id'],v['assessment_id'])+'/'+v['goal_id']+'='+str(v['weight']) for v in c['records']);options.append(o)
    return _question({'type':'grading_matrix'},'确认本轮完整的考核方式—课程目标系数表',options,
        explanation='原表给出的目标系数不同。请选择本轮适用的整张表，不逐个挑选系数；这与总评占比是独立层级。无法确认请选0。')
