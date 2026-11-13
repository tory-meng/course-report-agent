"""模型仅写启用的内容段；固定公式、统计和排版由程序处理。"""
import json
import re
from .client import task
from .structured import writing_schema
from .common import save

EFFECT_CLAIM=r'显著|明显提升|有效解决|保证|较好.{0,4}效果|起到.{0,12}效果'

def semantic_view(records):
    """保留完整目标/活动文本和来源ID；重复的原文引文仍在已校验事实中留档。"""
    return [{k:v for k,v in r.items() if k!='quotes'} for r in records]

def implementation_view(records):
    """仅将原材料中的活动子句交给写作模型，效果自评保留在原事实和审计中。"""
    selected=[];audit=[]
    for record in records:
        clauses=[s.strip() for s in re.split(r'[，,。；;！？!?\n]+',record.get('text','')) if s.strip()]
        activities=[s for s in clauses if re.search(r'采用|开展|组织|安排|介绍|讲授|布置|讨论|练习|实施',s) and not re.search(EFFECT_CLAIM,s)]
        audit.append({'id':record.get('id'),'source_refs':record.get('source_refs',[]),'selected_activity_clauses':activities,
                      'omitted_clauses':[s for s in clauses if s not in activities]})
        if activities:
            selected.append({k:v for k,v in record.items() if k not in ('text','quotes')}|{'text':'，'.join(activities)+'。','evidence_view':'source_activity_clauses_only'})
    return selected,audit

def factual_numbers(paragraphs,numbered=False):
    """排版用的连续序号不属于课程数值；只排除与实际行号一致的前缀。"""
    bodies=[]
    for i,paragraph in enumerate(paragraphs,1):
        match=re.match(r'^\s*(?:[（(](\d+)[）)]|(\d+)[.．、])\s*',paragraph) if numbered else None
        if match:
            if int(match[1] or match[2])!=i:raise ValueError('段落序号须连续且与条目一致')
            paragraph=paragraph[match.end():]
        bodies.append(paragraph)
    return set(re.findall(r'\d+(?:\.\d+)?',''.join(bodies)))

def method_body(paragraph, label):
    """标题由宿主统一；兼容模型重复输出序号和同一方法标题。"""
    paragraph=re.sub(r'^\s*(?:[（(]\d+[）)]|\d+[.．、])\s*','',paragraph).strip()
    prefix=re.compile(r'^'+re.escape(label)+r'\s*[:：]\s*')
    while prefix.match(paragraph):
        paragraph=prefix.sub('',paragraph,count=1).strip()
    if label=='课堂讨论':
        paragraph=re.sub(r'^课堂表现\s*[:：]\s*','',paragraph).strip()
    return paragraph

def future_plan(paragraph):
    if paragraph.startswith('缺少'):return paragraph
    paragraph=re.sub(r'^\s*(?:需要|应当|应该|应)\s*','',paragraph)
    paragraph=paragraph.replace('以保证','以支持').replace('从而保证','以支持')
    return paragraph if re.match(r'^(?:拟|计划|建议|将)',paragraph) else '拟'+paragraph

def write_sections(case,facts,calc,template,client,run,offline=False,progress=None):
    selected=[]
    for paragraph in re.split(r'\n\s*\n|\n(?=\d+\.)',template['prompt']):
        match=re.match(r'(\d+)\.',paragraph)
        if paragraph.startswith('你是') or (match and int(match.group(1)) in (1,2,3,14,15,16,17,20,23,24)):
            selected.append(paragraph)
    system='\n\n'.join(selected)+'\n本次仅填写用户消息指定章节；工具、统计、版式及完成状态由宿主处理。严格返回当前任务要求的JSON。'
    result={};issues=[]
    from .course_presentation import enabled as course_style
    planned=course_style(facts)
    if planned and not offline:
        from .course_design import improvement_sections
        result.update(improvement_sections(case,facts,calc,client,run))
    tasks={'2.4':facts.get('methods',[]),'6.1':[r for r in facts.get('narratives',[]) if r.get('section')=='6.1'],
           '6.2':[r for r in facts.get('narratives',[]) if r.get('section')=='6.2'],
           '7':[r for r in facts.get('narratives',[]) if r.get('section')=='7']}
    for i,(sid,records) in enumerate(tasks.items()):
        if progress:progress.units(i,len(tasks),'填写章节 '+sid)
        if planned and sid in result:continue
        if sid=='6.2' and records and not offline:
            records,view_audit=implementation_view(records)
            save(run/'implementation_evidence_view.json',{'scope':'确定性选择原文活动子句；未改原始事实或将自评当作效果证据','records':view_audit})
        if not records:
            result[sid]=['缺少'+{'2.4':'本轮教学方式与实施材料','6.1':'上一轮评价及改进记录','6.2':'本轮实施及可比效果证据','7':'已确认的持续改进安排'}[sid]+'，本节待补充核验。']
            issues.append(sid+'缺少写作依据');continue
        if offline:
            result[sid]=[(r.get('name','')+'：' if sid=='2.4' else '')+r.get('description',r.get('text','')) for r in records]
            continue
        refs=sorted({v for r in records for v in r.get('source_refs',[])})
        data={'task':'按已确认模板写当前小节，返回paragraphs字符串数组及source_refs数组。不得引入新事实或数字。2.4按输入方式逐条对应；其余保持简洁。第7节未来安排使用拟/计划。',
              'section_id':sid,'template_id':template['contract']['template_id'],'facts':semantic_view(records),'course_name':case['course_name'],
              'constraints':{'numbered':sid in ('2.4','7'),'maximum_characters_per_paragraph':180,'suppressed_blocks':template['contract']['editorial_policy']['suppressed_blocks']}}
        styled=sid=='2.4' and bool(facts.get('teaching_reference_context'))
        schema=writing_schema(refs)
        if sid=='2.4':
            schema['properties']['paragraphs'].update(minItems=len(records),maxItems=len(records))
        if styled:
            # 参考原课的整段活动容易被小模型直接复制成本课实施事实。
            # 检索与拟定关系仍保留参考证据；本节只传递通用组织方式。
            data['reference_style']='按具体活动、课程内容、预期作用组织短句。方式名称由程序统一添加。'
            data['reference_adoption_rule']='paragraphs按输入顺序仅写具体活动和作用，不写序号或标题。名称由程序使用presentation_name与表格统一。只能来自本课facts，不能引入参考课程的作业、实验、平台或实施效果。学期适用性未核验，不宣称为已实施效果。'
        if planned and sid=='2.4':
            data['presentation_rule']='本节以教学安排行文，直接写课程活动和内容。不使用拟定、拟采用、代理估计、待核验、待审核等标签，不声称已取得成效。'
        if sid=='6.2':
            data['attribution_rule']='仅概述材料明确记载的教学活动。不得复述显著提高、明显提升、有效解决、保证掌握或教学效果较好等效果判断，材料自评也不能代替独立验证；证据边界由宿主另加一句。'
        evidence_text=' '.join([case['course_name']]+[str(r.get(k,'')) for r in records for k in ('name','description','text')]+
                               [str(q) for r in records for q in r.get('quotes',{}).values()])
        allowed=set(re.findall(r'\d+(?:\.\d+)?',evidence_text))
        def check(answer):
            paragraphs=answer.get('paragraphs')
            if not isinstance(paragraphs,list) or not paragraphs or any(not isinstance(s,str) or not s.strip() or len(s)>220 for s in paragraphs):raise ValueError('paragraphs须为非空短段落数组，每段≤220字')
            if sid=='2.4' and len(paragraphs)!=len(records):raise ValueError('2.4每种教学方式对应一条')
            if not answer.get('source_refs') or not set(answer['source_refs'])<=set(refs):raise ValueError('缺少本节有效来源')
            text=''.join(paragraphs)
            if any(t in text for t in ['<think>','```','【','作为AI']):raise ValueError('回答含占位符、思考或代码')
            if not factual_numbers(paragraphs,sid in ('2.4','7'))<=allowed:raise ValueError('新增了未提供的数字')
            if sid=='6.2' and re.search(EFFECT_CLAIM,text):
                raise ValueError('6.2仅写材料记载活动，不复述未经独立核验的效果判断')
        answer=task(client,[{'role':'system','content':system},{'role':'user','content':json.dumps(data,ensure_ascii=False)}],
                    run/'records'/('section_'+sid.replace('.','_')+'.json'),check,
                    {'task_kind':'write_section','section_id':sid,'case_id':case['id'],'group_id':case.get('group_id',case['id']),
                     'template_id':template['contract']['template_id'],'template_sha256':template['hashes']},retries=1,output_schema=schema)
        if styled:
            from .report_scope import method_label
            result[sid]=[method_label(facts,m)+'：'+method_body(p,method_label(facts,m)) for m,p in zip(records,answer['paragraphs'])]
        elif sid=='6.2':
            result[sid]=['据本课材料记载：'+p for p in answer['paragraphs']]+['材料对教学效果的评价属于自评，尚缺同口径前后数据，不能据此确认能力提升或措施有效性。']
        elif sid=='7':result[sid]=[future_plan(p) for p in answer['paragraphs']]
        else:result[sid]=answer['paragraphs']
    if progress:progress.units(len(tasks),len(tasks),'章节处理完成（缺失依据见核验清单）')
    if planned:
        result['2.4']=[re.sub(r'拟(?:定)?(?=采用|开展|组织|通过|安排|结合|使用|围绕|指导|讲授)','',p).replace('待核验','') for p in result['2.4']]
    return result,issues
