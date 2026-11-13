"""报告展示采用已选方案；原始候选及其证据继续保留在facts中。"""

def assessments(facts,include_matrix=False):
    rows=facts.get('assessments',[])
    chosen=facts.get('grading_scheme_resolution',{}).get('scheme_id')
    if not chosen:return rows
    scheme=next(c for c in facts['grading_schemes'] if c['id']==chosen)
    names=[c['name'] for c in scheme['components']]
    primary=[]
    for name in names:
        matches=[a for a in rows if a['name']==name]
        if len(matches)!=1:raise ValueError('已选方案的方式实体缺失或重复：'+name)
        primary.append(matches[0])
    if include_matrix:
        ids={r['assessment_id'] for r in facts.get('assessment_goal_weights',[])}
        primary += [a for a in rows if a['id'] in ids and a['id'] not in {p['id'] for p in primary}]
    return primary


def audit(facts):
    primary=assessments(facts);combined=assessments(facts,True)
    pids={a['id'] for a in primary};cids={a['id'] for a in combined}
    return {'primary_assessments':[{'id':a['id'],'name':a['name']} for a in primary],
            'matrix_only_assessments':[{'id':a['id'],'name':a['name']} for a in combined if a['id'] not in pids],
            'inactive_candidates':[{'id':a['id'],'name':a['name']} for a in facts.get('assessments',[]) if a['id'] not in cids],
            'note':'已选总评方案与目标系数表名称不同的方式保留原名；未明确的名称对应、评分映射和零系数不自动补齐。'}


def method_label(facts,method):
    from .teaching_presentation import title,CANONICAL_METHOD_NAMES
    if facts.get('preparation',{}).get('origin')=='explicit_source_bound_course_design':
        name=method.get('name','').strip()
        if 0<len(name)<=16:return name
    if method.get('name') in CANONICAL_METHOD_NAMES:return method['name']
    value=method.get('presentation_name') or title(method)
    # 实验教学和专题报告可能都被归为“任务实训”；保留来源短名以免两行变成同名活动。
    if sum((m.get('presentation_name') or title(m))==value for m in facts.get('methods',[]))>1:
        original=method.get('name','').strip()
        if 0<len(original)<=16:return original
    return value

def method_support(facts,method,goal_id):
    if goal_id in method.get('goal_ids',[]):return '√'
    plan=facts.get('teaching_plan',{})
    if plan.get('status')=='proposed' and any(v['method_id']==method['id'] and goal_id in v['goal_ids'] for v in plan['links']):
        from .course_presentation import enabled
        return '√' if enabled(facts) else '拟√'
    return ''
