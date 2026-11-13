"""把已核验模板的章节/表图需求传给抽取任务，不将参考报告事实作为证据。"""
KIND_SECTIONS={
 'metadata':['1','2.6','5.1','5.2'],
 'goals':['2.2','2.3','3.1','3.2','5.1','5.3.3'],
 'points':['2.1','2.3','4','5.1','5.3.4'],
 'methods':['2.4','2.5'],
 'assessments':['2.6','3.1','3.2','5.3.2'],
 'items':['2.6','3.1','4','5.3.2'],
 'goal_rules':['3.1','5.2','5.3.3'],
 'assessment_goal_weights':['3.2','5.3.2','5.3.3'],
 'assessment_point_rules':['4','5.3.2','5.3.4'],
 'narratives':['6.1','6.2','7'],
}
FIELD_MEANINGS={
 'course_name':'课程名称主体；明确的末尾/考试或/考查属于考核形式，不属于名称主体',
 'assessment_form':'考核形式，如考试、考查；与考核项及其权重分开',
 'course_nature':'按原文填写课程性质，如必修、选修；不能用考查/考试替代',
 'course_category':'按原文填写课程类别；不凭考核形式推断',
 'course_code':'原文明示的课程代码；未明示则不填写',
 'offering_code':'原文课号的完整写法；不自动拆解为课程代码',
 'teacher':'完整教师姓名或共同授课名单，不能截短汉字',
 'credits':'有原文依据的数值学分，不能从课时猜测',
}

def build_targets(contract,template_id):
    if contract is None:
        return {'template_id':template_id,'bound':False,'sections':[],'tables':[],'charts':[]}
    if contract.get('template_id')!=template_id:raise ValueError('抽取模板ID与加载的模板不一致')
    return {'template_id':template_id,'bound':True,
      'sections':[{k:s.get(k,[] if k=='table_ids' else '') for k in ('id','title','inputs','instruction','table_ids')} for s in contract.get('sections',[])],
      'tables':[{'id':t['id'],'headers':t.get('rows',[])[:t.get('header_rows',1)]} for t in contract.get('tables',[])],
      'charts':[{k:f.get(k,'') for k in ('id','section','kind','required_data')} for f in contract.get('chart_slots',[])],
      'policy':'模板定义字段与表图需求；事实只来自本轮已标注材料。统计、公式与图表数据由程序计算，不让模型编写统计结果。'}

def targets_for(plan,kinds):
    ids={sid for kind in kinds for sid in KIND_SECTIONS.get(kind,[])}
    sections=[s for s in plan['sections'] if s['id'] in ids]
    table_ids={tid for s in sections for tid in s['table_ids']}
    return {'template_id':plan['template_id'],'bound':plan['bound'],
            'sections':[{k:s[k] for k in ('id','title','inputs','instruction','table_ids')} for s in sections],
            'tables':[t for t in plan['tables'] if t['id'] in table_ids],
            'chart_inputs':[f for f in plan['charts'] if f['section'] in ids],
            'scope':'逐人成绩由程序读取，统计和图表值由程序计算。本批仅抽取有证据的身份、目标、规则、权重和文本事实，不编造数据以填满模板。'}
