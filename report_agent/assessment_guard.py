"""考核抽取约束：名称、父子层级与数值口径分别核验。"""
import re


def compact(value):return re.sub(r'\s+', '', str(value))


def hierarchy(blocks):
    """只识别明确双占比表中的文字层级；不把候选比例选为本轮规则。"""
    from .extraction_quality import cells,table_key
    tables={}
    for b in blocks:
        key=table_key(b)
        if key:tables.setdefault(key,[]).append(b)
    rows=[]
    for bs in tables.values():
        if not any('考核方式' in b['text'] and b['text'].count('占比')>=2 for b in bs):continue
        for b in bs:
            cs=cells(b)
            # 压缩请求用于离线审计时仍可由原文的单元格分隔符读取。
            if len(cs)<6:continue
            parent,name=cs[0].strip(),cs[1].strip()
            if not parent or not name or name in ('小计','合计','总计'):continue
            if not all(re.fullmatch(r'\d+(?:\.\d+)?[%％]',x.strip()) for x in cs[-2:]):continue
            if len(parent)>80 or len(name)>80:continue
            rows.append({'assessment_name':parent,'item_name':name,'evidence_id':b['id']})
    return rows


def direct_hierarchy_records(blocks):
    """读取明确双占比表的文字列；数值留在独立候选方案中，不作制度裁决。"""
    from .common import signature
    from .extraction_quality import cells,table_key
    eligible=[b for b in blocks if b.get('role') in ('current','rule')]
    rows=hierarchy(eligible)
    records=[];consumed=set();parents={}
    for row in rows:
        name=row['assessment_name'];ref=row['evidence_id']
        aid='A_'+signature(compact(name))[:12]
        if aid not in parents:
            parents[aid]={'kind':'assessments','value':{'id':aid,'name':name},'evidence_ids':[]}
            records.append(parents[aid])
        parents[aid]['evidence_ids'].append(ref)
        child=row['item_name'];iid='I_'+signature([aid,compact(child)])[:12]
        records.append({'kind':'items','value':{'id':iid,'name':child,'assessment_id':aid},'evidence_ids':[ref]})
        consumed.add(ref)
    tables={table_key(b) for b in eligible if b['id'] in consumed}
    for b in eligible:
        if table_key(b) not in tables:continue
        cs=cells(b)
        header='考核方式' in b['text'] and b['text'].count('占比')>=2
        subtotal=(len(cs)==4 and cs[0] in {r['assessment_name'] for r in rows}
                  and cs[1] in ('小计','合计','总计')
                  and all(re.fullmatch(r'\d+(?:\.\d+)?[%％]',x.strip()) for x in cs[-2:]))
        total=(len(cs)<=3 and cs[0].strip() in ('合计','总计')
               and re.fullmatch(r'\d+(?:\.\d+)?[%％]',cs[-1].strip()))
        if header or subtotal or total:consumed.add(b['id'])
    return records,consumed


def evidence_registry(registry,blocks):
    """只向考核任务提供本批原文出现的方式/项目，避免跨方案同义替换。"""
    text=compact('\n'.join(b['text'] for b in blocks))
    parents={row['assessment_id'] for row in registry.get('items',[]) if row.get('assessment_id') and row.get('name') and compact(row['name']) in text}
    return {kind:([row for row in rows if row.get('name') and (compact(row['name']) in text or (kind=='assessments' and row['id'] in parents))]
                  if kind in ('assessments','items') else rows)
            for kind,rows in registry.items() if kind!='methods'}


def restrict_schema(schema,blocks):
    text='\n'.join(b['text'] for b in blocks)
    has_goal=bool(re.search(r'(?:课程目标|学习目标|目标)\s*[一二三四五六七八九十\d]|(?:课程目标|学习目标).{0,10}(?:系数|权重)',text))
    has_point=bool(re.search(r'(?:毕业要求|观测点|指标点)',text) and
                   re.search(r'\d+\.\d+|(?:观测点|指标点)\s*[一二三四五六七八九十\d]',text))
    quantitative=bool(re.search(r'(?<![\d.])(?:0\.\d+|1\.0+)(?![\d.])|\d+(?:\.\d+)?\s*[%％]|(?:系数|权重)[ \t]*(?:[：:=]|为)?[ \t]*[01](?!\d)',text))
    variants=schema['properties']['records']['items']['anyOf']
    kept=[]
    for variant in variants:
        kind=variant['properties']['kind']['enum'][0]
        props=variant['properties']['value']['properties']
        if kind=='items':
            if not has_goal or not quantitative:props.pop('goal_weights',None)
            if not re.search(r'满分|分值',text):props.pop('max_score',None)
            if not re.search(r'折合|折算|折后|换算后',text):props.pop('converted_max',None)
            if not re.search(r'原始分|原分|折合分|折算分',text):props.pop('score_basis',None)
        if kind=='assessments' and (hierarchy(blocks) or not re.search(r'[*×]',text)):props.pop('terms',None)
        # 删除不适用的分支，避免依赖解码器对 not 的支持。
        if (kind in ('assessment_goal_weights','goal_rules') and (not has_goal or not quantitative)) or (kind=='assessment_point_rules' and not has_point):
            continue
        kept.append(variant)
    schema['properties']['records']['items']['anyOf']=kept
    return schema


def validate(records,blocks,known=None):
    known=known or {}
    assessments={r['id']:r for r in known.get('assessments',[])}
    assessments.update({r['value']['id']:r['value'] for r in records if r['kind']=='assessments'})
    expected=hierarchy(blocks)
    for record in records:
        kind=record['kind'];v=record['value'];joined='\n'.join(v.get('quotes',{}).values())
        if kind in ('assessments','items'):
            if not any(compact(v['name']) in compact(q) for q in v['quotes'].values()):
                raise ValueError(kind+'.name须保留所选原文名称；不能把终结性考核等上层方式改成其答辩子项或其他已知方式')
        if kind=='assessments' and expected and v.get('terms'):
            raise ValueError('双占比表比例保存在候选方案中，未确认前不提交terms计算规则')
        if kind=='items':
            # 同一双占比行的父项必须准确。不能用全局同名/相似实体替换本行父项。
            previous=[r for r in known.get('items',[]) if compact(r['name'])==compact(v['name'])]
            parent=assessments.get(v['assessment_id'])
            if previous and v['assessment_id'] not in {r['assessment_id'] for r in previous}:
                if not parent or compact(parent.get('name','')) not in compact(joined):
                    raise ValueError('已有考核子项'+v['name']+'的父项不可跨批次改写；本段未明示新的父级，请沿用known_entities.items中的assessment_id')
            for row in expected:
                if row['evidence_id'] in v['source_refs'] and compact(row['item_name'])==compact(v['name']):
                    parent=assessments.get(v['assessment_id'])
                    if not parent or compact(parent.get('name',''))!=compact(row['assessment_name']):
                        raise ValueError('items.assessment_id与本行父级考核方式不一致：'+row['assessment_name'])
                    if v.get('max_score') is not None and not re.search(r'满分|分值',v['quotes'].get(row['evidence_id'],'')):
                        raise ValueError('本行只有占比，不能借用其他段落的满分填写当前考核子项')
            if v.get('max_score') is not None and not re.search(r'满分|分值',joined):
                # 明确表头可作为同表引用；只有百分比时不能默认为100分。
                raise ValueError('items.max_score缺少所选原文中的满分/分值标签；百分比不是满分，未知须省略')
            if v.get('converted_max') is not None and not re.search(r'折合|折算|折后|换算后',joined):
                raise ValueError('items.converted_max缺少原文折合满分说明，不能把占比数字当折合分')
            if v.get('goal_weights') and not re.search(r'课程目标|学习目标|目标\s*\d',joined):
                raise ValueError('items.goal_weights缺少引用中的目标分配依据，不能套用已知目标编号补系数')
    if expected:
        actual={(compact(r['value']['name']),r['value']['assessment_id']) for r in records if r['kind']=='items'}
        missing=[]
        for row in expected:
            matched=any(name==compact(row['item_name']) and compact(assessments.get(aid,{}).get('name',''))==compact(row['assessment_name']) for name,aid in actual)
            if not matched:missing.append(row['assessment_name']+'→'+row['item_name'])
        if missing:raise ValueError('覆盖检查：须提取原文明示的父子考核项 '+ '；'.join(dict.fromkeys(missing))+'；仅有名称也应返回，不要求补满分或目标系数')


def prompt_guidance(blocks):
    return {'expected_hierarchy':hierarchy(blocks),
            'rules':['仅抽取当前原文支持的名称与关系；未知数值字段直接省略，不填默认100，不把百分比转写为满分。',
                     '已知实体只有名称与当前原文一致时才复用ID；不同名称不得因为含义相近就合并。',
                     '补充说明已有子项而本段未明确父级时，沿用known_entities.items.assessment_id；不要因同批另有报告评分表就改变这些子项的父项。已知父项无需在没有名称证据的段落重新声明。',
                     '双占比表的第一列是父级方式，第二列是子项；上层方式不能替换成其中一个子项。',
                     '没有目标分配表时不输出goal_weights、assessment_goal_weights、goal_rules；全局有G1至G4不代表本行给了目标系数。',
                     '至少返回有依据的id/name及子项assessment_id；已有实体仍应以本批引用返回。不要因部分数值缺失而返回空records。',
                     '总评候选及其组内比例已独立保存，未确认前不在此任务选择候选方案。']}
