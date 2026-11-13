"""按本课活动生成统一展示名称；不改原事实文本、ID或正式支撑关系。"""
import re

CANONICAL_METHOD_NAMES={'课堂讲授','课堂讨论','案例教学','课堂练习','自主学习','平时作业','文档答辩',
    '案例分析','小组协作','汇报与答辩','实验与实践','实验教学','专题报告','慕课与作业','测验与考试','小组汇报','调研与实践报告','期末文档','期末文档答辩'}

def title(method):
    if method.get('name') in CANONICAL_METHOD_NAMES:return method['name']
    text=method.get('description') or method.get('name','')
    if any(w in text for w in ('小组','协作','合作')):return '小组协作'
    if any(w in text for w in ('答辩','汇报','展示')):return '汇报与答辩'
    if any(w in text for w in ('自主学习','课后学习')):return '自主学习'
    if any(w in text for w in ('讲授','讲述','讲解','演示')):
        if 'STM32' in text or '控制器' in text:return '讲授与演示（控制器开发）'
        if '标志物' in text or '路灯' in text:return '讲授与演示（标志物通信）'
        return '讲授与演示'
    if any(w in text for w in ('实操','实训','实验')):return '任务实训'
    name=method.get('name','').strip()
    return name if 1<=len(name)<=16 else '教学活动（待分类）'

def prepare(facts):
    result=[]
    for m in facts.get('methods',[]):
        m['presentation_name']=title(m)
        result.append({'method_id':m['id'],'presentation_name':m['presentation_name'],
                       'original_name':m.get('name'),'source_refs':m.get('source_refs',[]),
                       'status':'derived_display_name_not_new_activity'})
    return {'methods':result,'scope':'名称用于跨章节一致展示，不代表新活动或已批准的目标关联'}

def uncovered_goals(facts,links):
    supported={g for m in facts.get('methods',[]) for g in m.get('goal_ids',[])}
    supported.update(g for row in links for g in row['goal_ids'])
    return [g['id'] for g in facts.get('goals',[]) if g['id'] not in supported]

def scope_audit(case,sources,facts):
    wanted=str(facts.get('metadata',{}).get('academic_year','')).replace('—','-')
    rows=[]
    if not re.fullmatch(r'20\d{2}-20\d{2}',wanted):return {'issues':rows}
    for source in sources:
        if source.get('role')!='current':continue
        for b in source.get('blocks',[]):
            compact=re.sub(r'\s+','',b['text'])
            for year in re.findall(r'20\d{2}[—－-]20\d{2}',compact):
                value=re.sub('[—－]','-',year)
                if value!=wanted:rows.append({'source_id':source['id'],'source_ref':b['id'],'written_academic_year':value,
                    'expected_academic_year':wanted,'status':'applicability_unconfirmed'})
    return {'issues':rows,'scope':'来源标记current不等于轮次已核验；不凭签署日期推断适用期，也不自动删除材料'}
