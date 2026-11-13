"""按表头、课程身份和学号选择成绩/补充分项/空白名册，保留每项裁决。"""
import copy
from pathlib import Path
import re
from .common import signature,number
from .grade_layout import ALIASES,layouts,label,is_student_id

def item_label(value):
    value=label(value)
    return re.sub(r'(?:考核)?(?:成绩|分数|得分)$','',value)

def identities(source):
    result=[]
    for sheet in source.get('sheets',[]):
        for row in sheet['rows'][:5]:
            for n,value in enumerate(row):
                match=re.search(r'课程名称\s*[:：]\s*(.*)',str(value))
                if not match:continue
                text=match[1].strip()
                if not text:text=next((str(v).strip() for v in row[n+1:] if str(v).strip()),'')
                text=re.split(r'[/／]|\s+课程代码',text)[0].strip(' 《》')
                if text:result.append(text)
    return sorted(set(result))

def _numeric(value):
    try:
        if isinstance(value,bool) or not str(value).strip():return None
        return number(value)
    except (ValueError,TypeError):return None

def _students(sheet,layout,pattern):
    result={}
    namecol=next((c['column'] for c in layout['columns'] if c['role']=='name'),None)
    for row in sheet['rows'][layout['header_row']:]:
        sid=str(row[layout['student_id']-1]).strip() if len(row)>=layout['student_id'] else ''
        if not is_student_id(sid,pattern):continue
        if namecol and (len(row)<namecol or not str(row[namecol-1]).strip()):continue
        if sid in result:raise ValueError('候选成绩表含重复学号：'+sid)
        result[sid]=row
    return result

def select(case,sources,facts):
    policy=case.get('grade_mapping_policy',{});wanted=label(case['course_name'])
    overrides=policy.get('identity_overrides',{});pattern=case.get('student_id_pattern',r'[A-Za-z0-9_-]+')
    candidates=[];excluded=[]
    for source in sources:
        if source.get('role')!='current' or not source.get('sheets'):continue
        names=identities(source);filename=Path(source['path']).name
        text=' '.join(str(v) for sheet in source.get('sheets',[]) for row in sheet['rows'][:5] for v in row)
        terms=set(re.findall(r'20\d{2}-20\d{2}-[12](?!\d)',text))
        for m in re.finditer(r'(20\d{2})\s*[—－-]\s*(20\d{2})\s*学年\s*第\s*([12一二])\s*学期',text):
            terms.add(m[1]+'-'+m[2]+'-'+{'一':'1','二':'2'}.get(m[3],m[3]))
        expected_term=facts.get('metadata',{}).get('term')
        if expected_term and terms and terms!={expected_term}:
            excluded.append({'source_id':source['id'],'reason':'成绩学期与本轮不符，不因年份较新而合并','written_terms':sorted(terms),'expected_term':expected_term});continue
        if '考勤' in filename or source.get('kind')!='grades':
            excluded.append({'source_id':source['id'],'reason':'非成绩来源，不作为总评或补充分项'});continue
        override=overrides.get(source['id'],{})
        authorized=bool(override.get('confirmation') and override.get('sha256')==source.get('sha256'))
        if names and any(label(n)!=wanted for n in names) and not authorized:
            excluded.append({'source_id':source['id'],'reason':'课程抬头不符且无本文件哈希绑定的用户确认','written_courses':names});continue
        if not names and not authorized:
            excluded.append({'source_id':source['id'],'reason':'缺少可核验课程身份，未自动合并'});continue
        for sheet in source['sheets']:
            for layout in layouts(sheet):
                students=_students(sheet,layout,pattern)
                if not students:continue
                columns=layout['columns'];totals=[c for c in columns if c['role']=='total']
                if not totals and re.search(r'记分册|成绩登记|成绩单',filename):
                    totals=[c for c in columns if c['role']=='generic_score']
                if len(totals)>1:raise ValueError('同表多个总评候选，须核对正式口径：'+source['id'])
                mapping={k:layout[k] for k in ('sheet','header_row','student_id')};mapping.update(source_id=source['id'],items={})
                for key in ('name','status'):
                    cols=[c['column'] for c in columns if c['role']==key]
                    if len(cols)==1:mapping[key]=cols[0]
                classes=[c['column'] for c in columns if label(c['label']) in ('班级','行政班','班级名称')]
                if len(classes)==1:mapping['class_name']=classes[0]
                ambiguous=[]
                for item in facts.get('items',[]):
                    matched=[c for c in columns if c not in totals and any(item_label(v)==item_label(item['name']) for v in c['header_path'])]
                    if len(matched)>1 and item.get('score_basis') in ('raw','converted'):
                        matched=[c for c in matched if c['score_basis']==item['score_basis']]
                    if len(matched)>1:ambiguous.append(item['id'])
                    elif len(matched)==1:
                        c=matched[0];known=item.get('score_basis')
                        if known in ('raw','converted') and c['score_basis'] in ('raw','converted') and known!=c['score_basis']:
                            raise ValueError('原始分与折算分口径冲突：'+source['id']+'/'+item['id'])
                        mapping['items'][item['id']]=c['column']
                if ambiguous:raise ValueError('考核项存在同名多列，不能自动选取：'+source['id']+'/'+','.join(ambiguous))
                totalcol=totals[0]['column'] if totals else None
                count=sum(_numeric(row[totalcol-1]) is not None for row in students.values() if totalcol and len(row)>=totalcol)
                if totalcol:mapping['total']=totalcol
                # 只在同课、有效评分候选中区分正式登记与中间核算；不按分数高低排名。
                rank=2 if re.search(r'成绩单|成绩登记',filename) else 0 if re.search(r'核算|计算',filename) else 1
                candidates.append({'source_id':source['id'],'source_sha256':source.get('sha256'),'written_courses':names,
                    'identity_override_used':authorized,'sheet':sheet['name'],'registered':len(students),'scored':count,
                    'priority':rank,'mapping':mapping,'students':students})
    filled=[c for c in candidates if c['scored']]
    if not filled:raise ValueError('没有可确认课程身份且已评分的总评表；保留名册，不补零，也不使用未确认的其他课程成绩')
    best=max(c['priority'] for c in filled);primary=[c for c in filled if c['priority']==best]
    def scores(c):
        col=c['mapping']['total'];return {sid:_numeric(r[col-1]) if len(r)>=col else None for sid,r in c['students'].items()}
    if len(primary)>1:
        def content(c):
            return {'total':scores(c),'items':{key:{sid:_numeric(row[col-1]) if len(row)>=col else None for sid,row in c['students'].items()} for key,col in c['mapping']['items'].items()}}
        if len({signature(content(c)) for c in primary})!=1:raise ValueError('多份同级正式成绩候选不一致，须核对版本；不按文件名或行数猜测')
        primary=sorted(primary,key=lambda c:(c['source_id'],c['sheet']))[:1]
    main=primary[0];selected=[copy.deepcopy(main['mapping'])];claimed=set(selected[0]['items']);origins={key:main for key in claimed};decisions=[]
    decisions.append({'source_id':main['source_id'],'sheet':main['sheet'],'action':'official_total','reason':'同课身份/用户确认及正式登记用途，保留原总评；不以核算未取整值覆盖'})
    for candidate in candidates:
        if candidate is main:continue
        overlap=set(candidate['students'])&set(main['students'])
        if not overlap:
            decisions.append({'source_id':candidate['source_id'],'action':'excluded','reason':'与所选总评无共同学号，未自动跨班次合并'});continue
        m=copy.deepcopy(candidate['mapping'])
        if not candidate['scored'] and not any(_numeric(row[col-1]) is not None for col in m['items'].values() for row in candidate['students'].values() if len(row)>=col):
            m['items']={}
        extra={k:v for k,v in m['items'].items() if k not in claimed}
        for key in set(m['items'])&claimed:
            previous=origins[key]
            if previous['priority']!=candidate['priority']:continue
            col=m['items'][key];oldcol=previous['mapping']['items'][key]
            for sid in set(previous['students'])&set(candidate['students']):
                a=previous['students'][sid];b=candidate['students'][sid]
                if _numeric(a[oldcol-1])!=_numeric(b[col-1]):raise ValueError('同级补充分项来源不一致：'+key+'；不静默采用副本')
        if extra:
            # 相同补充字段多来源须核验，不静默择一；最终parse_grades还检查跨表值冲突。
            if set(candidate['students'])-set(main['students']):raise ValueError('已评分补充表包含总评名册外学生，须核对课程轮次')
            m.pop('total',None);m['items']=extra;selected.append(m);claimed.update(extra);origins.update({key:candidate for key in extra})
            decisions.append({'source_id':candidate['source_id'],'action':'component_supplement','items':sorted(extra),'reason':'仅补充尚无来源的分项，按学号关联，不重复合并总评或平时汇总'})
        elif not candidate['scored']:
            m.pop('total',None);m['items']={};selected.append(m)
            decisions.append({'source_id':candidate['source_id'],'action':'roster_only','reason':'空白同课名册补充登记学生，空白成绩不转为零'})
        else:
            decisions.append({'source_id':candidate['source_id'],'action':'not_merged','reason':'已有总评和分项来源；重复或核算表不覆盖正式值','total_values_identical':scores(candidate)==scores(main)})
    return selected,{'version':'grade_selection_v1','status':'selected','candidate_tables':[{k:v for k,v in c.items() if k!='students'} for c in candidates],
                     'excluded_sources':excluded,'decisions':decisions,'selected_mappings':selected,
                     'policy':'确定性识别已读取材料；未知身份、多列或来源冲突保留核对，不推断未知评价制度'}
