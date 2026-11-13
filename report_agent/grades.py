"""显式列映射优先，保守识别学号/总评；不按行对齐、不把空白补零。"""
import re
from .common import number

from .grade_layout import ALIASES
STATUS={'':'normal','正常':'normal','normal':'normal','缺考':'absent','缓考':'deferred','免修':'exempt','缺失':'missing','absent':'absent','deferred':'deferred','exempt':'exempt','missing':'missing'}

def plain(x):return re.sub(r'\s+','',str(x))

def integer_duplicate(candidates,sources):
    """用户默认：同一表相邻同名成绩列中，左侧全为整数、右侧含小数时选左列。"""
    if len(candidates)!=2:return None
    left,right=sorted(candidates,key=lambda c:c['mapping']['total'])
    a,b=left['mapping'],right['mapping']
    if any(a[k]!=b[k] for k in ('source_id','sheet','header_row','student_id')) or b['total']!=a['total']+1:return None
    source=next(s for s in sources if s['id']==a['source_id']);sheet=next(s for s in source['sheets'] if s['name']==a['sheet'])
    header=sheet['rows'][a['header_row']-1]
    if plain(header[a['total']-1])!='成绩' or plain(header[b['total']-1])!='成绩':return None
    pairs=[]
    for row in sheet['rows'][a['header_row']:]:
        if len(row)<a['student_id'] or not re.fullmatch(r'[A-Za-z0-9_-]+',plain(row[a['student_id']-1])):continue
        if len(row)<b['total']:return None
        x,y=row[a['total']-1],row[b['total']-1]
        if isinstance(x,bool) or isinstance(y,bool):return None
        try:pairs.append((number(x),number(y)))
        except (ValueError,TypeError):return None
    if pairs and all(x.is_integer() for x,y in pairs) and any(not y.is_integer() for x,y in pairs):return left
    return None

def auto_mapping(source, items):
    from .grade_layout import mappings
    candidates=mappings(source,items)
    if candidates:
        keys=[(m['sheet'],m['header_row']) for m in candidates]
        if len(keys)!=len(set(keys)):raise ValueError('存在多个total列，请显式填写grade_tables')
        return candidates
    found=[]
    for sheet in source['sheets']:
        for rowno,row in enumerate(sheet['rows'][:30],1):
            headers=[plain(v) for v in row]
            idcols=[i+1 for i,h in enumerate(headers) if h in ALIASES['student_id']]
            if len(idcols)!=1:continue
            mapping={'source_id':source['id'],'sheet':sheet['name'],'header_row':rowno,'student_id':idcols[0],'items':{}}
            for key, aliases in ALIASES.items():
                cols=[i+1 for i,h in enumerate(headers) if h in aliases]
                if len(cols)>1:raise ValueError(f'{sheet["name"]}存在多个{key}列，请显式填写grade_tables')
                if cols:mapping[key]=cols[0]
            if 'total' not in mapping:
                candidates=[i+1 for i,h in enumerate(headers) if '成绩' in h]
                raise ValueError(f'{sheet["name"]}未确认正式总评列（成绩候选列{candidates}）；请显式填写grade_tables，不默认选择最后一列')
            for item in items:
                cols=[i+1 for i,h in enumerate(headers) if h==plain(item['name'])]
                if len(cols)>1:raise ValueError('考核项同名列不唯一，请显式映射')
                if cols:mapping['items'][item['id']]=cols[0]
            found.append(mapping)
    if not found:raise ValueError('无法唯一识别成绩表，请先inspect并填写grade_tables（支持多个表按学号合并）')
    return found

def parse_grades(case,sources,facts):
    mapped=case.get('grade_tables',[])
    if not mapped:
        candidates=[s for s in sources if s['kind']=='grades' and s['role']=='current']
        if not candidates:raise ValueError('没有role=current且kind=grades的成绩来源')
        mapped=[m for s in candidates for m in auto_mapping(s,facts.get('items',[]))]
    students={}; warnings=[]; source_lookup={s['id']:s for s in sources}
    item_lookup={r['id']:r for r in facts.get('items',[])}
    for mapping in mapped:
        cols=[mapping.get(k) for k in ('student_id','name','total','status','class_name') if mapping.get(k) is not None]+list(mapping.get('items',{}).values())
        if not isinstance(mapping.get('header_row'),int) or mapping['header_row']<1 or not mapping.get('student_id'):
            raise ValueError('grade_tables须提供从1开始的整数表头行及student_id列')
        if any(not isinstance(c,int) or isinstance(c,bool) or c<1 for c in cols):
            raise ValueError('grade_tables列号必须是从1开始的整数')
        source=source_lookup[mapping['source_id']]
        if source['role']!='current':raise ValueError('成绩只能来自current来源')
        sheet=next(s for s in source['sheets'] if s['name']==mapping['sheet'])
        for key,meta in mapping.get('item_metadata',{}).items():
            known=item_lookup.get(key,{}).get('score_basis')
            if known in ('raw','converted') and meta.get('score_basis') in ('raw','converted') and known!=meta['score_basis']:
                raise ValueError('分项原始分/折算分口径冲突：'+key+'；请核对满分和映射，不重复乘权重')
        if sheet['formula_cells']:raise ValueError('Excel公式缺缓存：'+str(sheet['formula_cells'])+'；请用Excel/LibreOffice重算并保存后重读')
        header=int(mapping['header_row']); seen=set();active_class='';active_class_ref=''
        for rowno,row in enumerate(sheet['rows'][header:],header+1):
            def raw(col):return row[int(col)-1] if col and int(col)<=len(row) else ''
            if not mapping.get('class_name'):
                for col,value in enumerate(row,1):
                    match=re.fullmatch(r'\s*行政班[：:]\s*(\S[^|]*)',str(value))
                    if match:
                        active_class=match[1].strip();active_class_ref=f'{source["id"]}:{sheet["name"]}:R{rowno}:C{col}'
            sid=plain(raw(mapping['student_id']))
            if not sid or sid in ALIASES['student_id'] or sid.startswith(('合计','平均','总计','说明','备注')):continue
            from .grade_layout import is_student_id
            if not is_student_id(sid,case.get('student_id_pattern',r'[A-Za-z0-9_-]+')):
                warnings.append(f'{source["id"]}:{sheet["name"]}:R{rowno}学号格式不明，跳过并需核对');continue
            if sid in seen:raise ValueError('同表重复学号：'+sid)
            seen.add(sid)
            student=students.setdefault(sid,{'id':sid,'name':'','status':'normal','scores':{},'score_status':{},'refs':{},'total':None})
            name=str(raw(mapping.get('name'))).strip()
            if name:
                if student['name'] and student['name']!=name:raise ValueError('同学号姓名冲突：'+sid)
                student['name']=name
            class_name=str(raw(mapping.get('class_name'))).strip() if mapping.get('class_name') else active_class
            if class_name:
                classes=student.setdefault('class_names',[])
                if class_name not in classes:classes.append(class_name)
                ref=f'{source["id"]}:{sheet["name"]}:R{rowno}:C{mapping["class_name"]}' if mapping.get('class_name') else active_class_ref
                student.setdefault('class_refs',{}).setdefault(class_name,[]).append(ref)
            state=plain(raw(mapping.get('status')))
            if state and state not in STATUS:raise ValueError('未知学生状态：'+state)
            if state:
                normalized=STATUS[state]
                if student['status']!='normal' and student['status']!=normalized:raise ValueError('跨表学生状态冲突：'+sid)
                student['status']=normalized
            targets=dict(mapping.get('items',{}))
            if mapping.get('total'):targets['total']=mapping['total']
            for key,col in targets.items():
                if key!='total' and key not in item_lookup:raise ValueError('列映射指向未知item：'+key)
                value=raw(col); text=plain(value)
                ref=f'{source["id"]}:{sheet["name"]}:R{rowno}:C{col}'
                if text=='待定' and student['status'] in ('deferred','absent','exempt','missing'):
                    # 已有明确行级状态时，保留未定分项为空；正式总评缓存仍原样保存。
                    status=student['status'];score=None
                elif not text or text in STATUS:
                    status=STATUS.get(text,'missing') if text else 'missing'; score=None
                else:
                    try:score=number(text.replace('，',''))
                    except (ValueError,TypeError):raise ValueError(f'{ref}不是可确认成绩：{text}')
                    status='normal'
                previous=student['total'] if key=='total' else student['scores'].get(key)
                if previous is not None and score is not None and previous!=score:raise ValueError(f'{sid}/{key}跨表成绩冲突')
                if key in student['refs'] and student['score_status'].get(key)!=status:
                    raise ValueError(f'{sid}/{key}跨表空白、缺考或评分状态冲突；须先确认正式口径')
                if key=='total':student['total']=score
                else:student['scores'][key]=score
                student['score_status'][key]=status; student['refs'][key]=ref
    if not students:raise ValueError('未提取到学生记录')
    for n,s in enumerate(students.values(),1):s['anonymous_id']=f'S{n:03}'
    classes=sorted({name for s in students.values() for name in s.get('class_names',[])})
    return {'students':list(students.values()),'mapping':mapped,'warnings':warnings,'registered_count':len(students),
            'sample_scope':{'class_names':classes,'multiple_classes':len(classes)>1,'year_inferred_from_student_id':False}}
