"""模板字段路由、原文摘录核验与覆盖检查。规则不把参考课程数字变成本轮事实。"""
import copy
import re
from .common import signature

REVISION='focused_evidence_v2'
GROUPS={
 'identity':['metadata'], 'objectives':['goals','points'], 'teaching':['methods'],
 'assessment':['assessments','items','assessment_goal_weights','goal_rules','assessment_point_rules'],
 'improvement':['narratives'],
}
QUERY={
 'identity':'课程基本信息 任课教师 课程名称 考核形式',
 'objectives':'课程目标 毕业要求 指标点 支撑关系 权重',
 'teaching':'课堂教学方式 教学方法 学习活动',
 'assessment':'考核方式 总评成绩 权重系数 考核项 课程目标 支撑关系',
 'improvement':'持续改进 本轮实施效果 问题 改进措施',
}
def compact(x):return re.sub(r'\s+','',str(x))
def cells(b):
    if b.get('cells') is not None:return [str(c.get('text','')) for c in b['cells']]
    return [re.sub(r'^C\d+=','',s).strip() for s in b['text'].split(' | ')]
def table_key(b):
    if b.get('table_id'):return b['table_id']
    m=re.match(r'(.+:T\d+)R\d+$',b['id'])
    if m:return m[1]
    m=re.match(r'(.+):R\d+$',b['id'])
    return m[1] if m else None

def formal_goal_records(blocks):
    """正式目标矩阵：合并跨行同目标，拆开一格多指标，不从考核说明制造目标。"""
    tables={}
    for b in blocks:
        if b.get('role') in ('current','rule') and table_key(b):tables.setdefault(table_key(b),[]).append(b)
    goals={};points={};consumed=set()
    def merge_ref(record,refs):record['evidence_ids']=list(dict.fromkeys(record['evidence_ids']+refs))
    def point_parts(value):
        matches=list(re.finditer(r'(?:^|[\n；;])\s*(\d+)\s*[-－—.]\s*(\d+)[：:．.、]?',value))
        return [(m[1],m[2],value[m.start():matches[n+1].start() if n+1<len(matches) else len(value)].strip('\n；; ')) for n,m in enumerate(matches)]
    for rows in tables.values():
        header=None;cols={}
        for b in rows:
            cs=cells(b)
            if any('学习目标' in c or compact(c)=='课程目标' for c in cs) and any('指标点' in c or '观测点' in c for c in cs):
                cols={}
                for i,c in enumerate(cs):
                    if ('学习目标' in c or compact(c)=='课程目标') and '权重' not in c:cols['goal']=i
                    elif ('指标点' in c or '观测点' in c) and '权重' not in c:cols['point']=i
                    elif '毕业要求' in c and '权重' not in c:cols['requirement']=i
                    elif '权重' in c:cols['weight']=i
                header=b;continue
            if not header or not {'goal','point'}<=set(cols) or len(cs)<=max(cols.values()):continue
            goal=cs[cols['goal']].strip();parts=point_parts(cs[cols['point']].strip())
            if len(goal)<12 or not parts:continue
            gm=re.match(r'(?:课程目标|学习目标|目标|G)\s*(\d+)|^(\d+)(?=[.．、\s])',goal)
            gid='G'+(next(g for g in gm.groups() if g) if gm else str(next((int(k[1:]) for k,r in goals.items() if compact(r['value']['text'])==compact(goal)),len(goals)+1)))
            refs=[header['id'],b['id']]
            if gid in goals:
                if compact(goals[gid]['value']['text'])!=compact(goal):raise ValueError('正式矩阵同一课程目标出现不同定义：'+gid)
                merge_ref(goals[gid],refs)
            else:goals[gid]={'kind':'goals','value':{'id':gid,'text':goal},'evidence_ids':refs}
            for first,second,text in parts:
                pid='P'+first+'_'+second;v={'id':pid,'text':text,'goal_weights':{gid:None}}
                if 'requirement' in cols:v['requirement']=cs[cols['requirement']].strip()
                if 'weight' in cols:
                    x=cs[cols['weight']].strip()
                    if re.fullmatch(r'\d+(?:\.\d+)?%?',x):v['course_weight']=float(x.rstrip('%'))/(100 if x.endswith('%') else 1)
                if pid in points:
                    prior=points[pid]['value']
                    if compact(prior['text']).rstrip('。；;')!=compact(text).rstrip('。；;'):raise ValueError('同一指标点出现不同定义：'+pid)
                    if 'course_weight' in v and prior.get('course_weight') not in (None,v['course_weight']):raise ValueError('同一指标点权重冲突：'+pid)
                    prior['goal_weights'][gid]=None;merge_ref(points[pid],refs)
                else:points[pid]={'kind':'points','value':v,'evidence_ids':refs}
            consumed.add(b['id'])
    return sorted(goals.values(),key=lambda r:int(r['value']['id'][1:]))+list(points.values()),consumed

def route(blocks,limit=1400):
    """按字段类别组批，带相邻标题/表头；每批只处理一个类别。"""
    buckets={};ignored=[];headers={};heading='';sid=None
    for b in blocks:
        if b.get('source_id')!=sid:heading='';sid=b.get('source_id')
        t=b['text'];key=table_key(b);cs=cells(b)
        if key and key not in headers:headers[key]=b
        if not key and len(t)<80 and (t.strip().startswith('【') or any(w in t for w in ('教学方法','学习活动','学习目标','考核要求','评分细则','改进','总结','阅读资料'))):heading=t
        context=(headers.get(key,{}).get('text','') if key else heading)
        joined=t+' '+context;groups=[]
        if b.get('extract_kinds')==['metadata']:groups.append('identity')
        if re.search(r'课程名称|课程代码|课号|任课教师|授课教师|教师姓名|主讲教师|授课班级|开课学院|开课院系|学分|学年|学期|课程类别|课程性质|总评满分|总成绩满分|及格分数|考核形式',t):groups.append('identity')
        if re.search(r'学习目标|课程目标|毕业要求|指标点|观测点',joined) and not re.search(r'考核内容|考核方式|达成度|平均分|权重系数',context):groups.append('objectives')
        if re.search(r'教学方法|教学方式|学习活动|小组合作学习|课堂讲授|案例教学|项目驱动教学',joined):groups.append('teaching')
        if re.search(r'考核|权重|系数|满分|总评|评分|成绩组成',joined):groups.append('assessment')
        if re.search(r'改进|以往|上一轮|本轮|本次教学|今后|存在的问题|持续改善',joined) and len(t)>20:groups.append('improvement')
        # 未识别的正文也交给一个小型分类抽取任务，不静默丢弃未知版式。
        if not groups and len(t)>100:groups=['objectives'] if '目标' in t else ['assessment'] if '成绩' in t else ['teaching']
        if not groups:ignored.append({'ref':b['id'],'reason':'标题/空白/无模板事实特征；保留在source_manifest'});continue
        for group in dict.fromkeys(groups):
            kinds=GROUPS[group]
            if b.get('role')=='history':group='improvement';kinds=['narratives']
            if b.get('extract_kinds')==['metadata'] and group!='identity':continue
            if len(t)>limit:
                # 仅在段落/句号切开；每段仍为同一原文连续子串。
                pieces=[];start=0
                while start<len(t):
                    end=min(len(t),start+limit)
                    if end<len(t):
                        cut=max(t.rfind('。',start+limit//2,end),t.rfind('\n',start+limit//2,end))
                        if cut>=0:end=cut+1
                    pieces.append(t[start:end]);start=end
            else:pieces=[t]
            for piece in pieces:
                part={**b,'text':piece,'extract_kinds':kinds,'_quality':True,'focus':group,'context_label':context[:300]}
                if len(pieces)>1:part.pop('cells',None)
                bucket=buckets.setdefault((sid,group),[])
                bucket.append(part)
    tasks=[]
    for (_,group),rows in buckets.items():
        batch=[];size=0
        for row in rows:
            if batch and (size+len(row['text'])>limit or len(batch)>=24 or row['id'] in {x['id'] for x in batch}):
                tasks.append(batch);batch=[];size=0
            key=table_key(row);header=headers.get(key)
            if not batch and header and header['id']!=row['id'] and len(header['text'])<500:
                batch.append({**header,'extract_kinds':GROUPS[group],'_quality':True,'focus':group,'context_only':True});size+=len(header['text'])
            batch.append(row);size+=len(row['text'])
        if batch:tasks.append(batch)
    return tasks,ignored

def registry(facts):
    # 正文绝不回灌；仅关联实体所需的短标签。目标不携带先前整段表述。
    return {k:[{'id':r['id'],**({'name':r['name']} if 'name' in r else {}),**({'assessment_id':r['assessment_id']} if k=='items' else {})} for r in facts.get(k,[])][:64]
            for k in ('goals','points','methods','assessments','items') if facts.get(k)}

def schema_for(blocks,kinds,base):
    schema=copy.deepcopy(base);schema['properties']['records']['maxItems']=12
    for variant in schema['properties']['records']['items']['anyOf']:
        kind=variant['properties']['kind']['enum'][0];v=variant['properties']['value']['properties']
        if kind=='metadata':
            # 总评方案独立收集，不能由身份任务拼凑。
            for key in ('total_rule_text','evaluation_policy'):v.pop(key,None)
            joined=' '.join(b['text'] for b in blocks)
            if not re.search(r'报告日期|报告时间|报告编制日期',joined):v.pop('report_date',None)
            if not re.search(r'任课教师|授课教师|教师姓名|主讲教师|教师[：:]',joined):v.pop('teacher',None)
            if not re.search(r'课程名称|课程名',joined):v.pop('course_name',None)
            compact_text=compact(joined)
            labels={'course_code':r'课程代码|课程编号','offering_code':r'课号',
                    'college':r'学院|院系','major':r'专业|C\d+=',
                    'class_name':r'班级|C\d+=','grade':r'年级|\d{4}级',
                    'term':r'学期','academic_year':r'学年|\d{4}[-—至]\d{4}',
                    'semester':r'学期','course_nature':r'课程性质',
                    'course_category':r'课程类别|课程类型|CourseType',
                    'hours':r'学时','credits':r'学分','total_max':r'总评满分|总成绩满分|百分制',
                    'pass_score':r'及格分数|及格线|合格线','standard_name':r'制度|评价标准|达成标准',
                    'assessment_form':r'考试|考查|考核形式|考核类型'}
            for field,pattern in labels.items():
                if not re.search(pattern,compact_text):v.pop(field,None)
        if kind in ('items','assessments'):v.pop('total_weight',None)
        if kind in ('narratives','goals','points') and 'text' in v:v['text']['maxLength']=700
    if set(kinds)&set(GROUPS['assessment']):
        from .assessment_guard import restrict_schema
        schema=restrict_schema(schema,blocks)
    return schema

def validate_quality(records,blocks,known=None):
    """原文事实摘录、角色和字段覆盖；不宣称通用语义推理已被完全验证。"""
    if blocks[0].get('focus') in (None,'assessment'):
        from .assessment_guard import validate as validate_assessment
        validate_assessment(records,blocks,known)
    content=[b for b in blocks if not b.get('context_only')]
    expected,_=formal_goal_records(blocks)
    expected_goals={r['value']['text'] for r in expected if r['kind']=='goals'}
    actual_goals={r['value']['text'] for r in records if r['kind']=='goals'}
    if expected_goals-actual_goals:raise ValueError('覆盖检查：正式目标表有目标未提取，不能返回空数组或遗漏目标')
    for record in records:
        k=record['kind'];v=record['value'];quotes=v['quotes'];joined='\n'.join(quotes.values())
        if k=='metadata':
            if v.get('teacher'):
                names=[]
                for q in quotes.values():
                    names.extend(re.split(r'\s{2,}|(?:职称|学分|班级|电话|邮箱|联系方式)\s*[：:]',m[1])[0].strip() for m in re.finditer(r'(?:任课教师|授课教师|教师姓名|主讲教师|教师)\s*[：:|]\s*(?:C\d+=)?([^|；;\n]+)',q))
                if not any(compact(v['teacher'])==compact(name).rstrip('。') for name in names):
                    raise ValueError('teacher必须来自任课教师标签；撰写人、审核人不是授课教师')
            if v.get('report_date') and not re.search(r'报告日期|报告时间|报告编制日期',joined):raise ValueError('report_date缺少报告日期标签；教学日历日期不能替代')
        if k in ('goals','points','narratives') or k=='methods':
            text=v.get('description','') if k=='methods' else v.get('text','')
            if not text or not any(compact(text) in compact(q) for q in quotes.values()):
                raise ValueError(k+'正文须为当前证据的连续原文摘录；禁止复制旧段落后改挂引用')
            if k=='goals' and re.search(r'考核内容',joined+' '.join(b.get('context_label','') for b in blocks if b['id'] in quotes)) and not re.search(r'支撑的毕业要求|Learning Outcomes',joined):
                raise ValueError('考核内容/评分细则不能替代正式课程目标')
            if k=='narratives':
                if len(compact(text))<20 or re.match(r'^(课程档案|教师简介|联系方式|先修课程|授课对象|授课语言|办公地点|课程基本信息)',text.strip()):
                    raise ValueError('叙述章节不接收标题、联系方式或基本信息')
                if v['section']=='7' and not re.search(r'将|拟|计划|建议|应当|需要|增加|加强|改进|改为|引入|优化',text):
                    raise ValueError('第7章须为明确的改进安排或建议')
                if v['section']=='6.2' and not re.search(r'本轮|本次|采取|实施|使用|采用|提高|提升|解决|效果|取得',text):
                    raise ValueError('6.2须为本轮实施或效果，不接收课程简介')
        if k=='goals' and not expected_goals:
            contexts=joined+' '+ ' '.join(b.get('context_label','') for b in blocks if b['id'] in quotes)
            if not re.search(r'(?:课程目标|学习目标|目标|G)\s*'+re.escape(v['id'].lstrip('G'))+r'(?!\d)',contexts):
                raise ValueError('正式目标缺少原文编号/目标矩阵；不能把项目学习内容自行编号为课程目标')
        # 数值必须在所选证据出现（百分数可换算）；跨列关系仍保留供审核。
        def numeric(x):
            if isinstance(x,bool):return []
            if isinstance(x,(int,float)):return [float(x)]
            if isinstance(x,dict):return [n for a in x.values() for n in numeric(a)]
            if isinstance(x,list):return [n for a in x for n in numeric(a)]
            return []
        if k!='metadata':
            available=[float(m[1])/(100 if m[2] else 1) for m in re.finditer(r'(?<![A-Za-z0-9.])(\d+(?:\.\d+)?)\s*([%％]?)',joined)]
            for n in numeric({key:value for key,value in v.items() if key not in ('quotes','source_refs')}):
                if not any(abs(n-a)<1e-8 for a in available):raise ValueError('数值未在所选原文出现；不得推导或沿用参考系数')
    joined='\n'.join(b['text'] for b in content);focus=blocks[0].get('focus')
    if focus in (None,'teaching') and re.search(r'通过.{0,30}(讲授|合作学习)|教学方法与学习活动',joined) and not any(r['kind']=='methods' for r in records):
        raise ValueError('覆盖检查：本批有明确教学方法却未提取')
    if focus in (None,'assessment') and re.search(r'平时.{0,30}(占|满分)|考核成绩组成|总评成绩\s*=',joined) and not any(r['kind'] in ('assessments','items') for r in records):
        raise ValueError('覆盖检查：本批有考核组成却未提取考核方式')

def reference_query(blocks):
    focus=blocks[0].get('focus','assessment');context=' '.join(b.get('context_label','') for b in blocks)
    names=[x for x in ('实验','实训','设计报告','验收答辩','平时','期末','项目') if x in context+' '.join(b['text'] for b in blocks)]
    return QUERY[focus]+' '+' '.join(names)

def normalize_entities(answer,facts):
    """只对同名实体稳定编号；关系随同批映射改写，不用模型临时ID混合不同考核方式。"""
    maps={k:{r['id']:r['id'] for r in facts.get(k,[])} for k in ('assessments','items','methods')}
    for k,prefix in [('assessments','A'),('methods','M')]:
        for record in answer['records']:
            if record['kind']!=k:continue
            v=record['value'];old=v['id'];same=next((r for r in facts.get(k,[]) if compact(r['name'])==compact(v['name'])),None)
            if k=='methods':
                same=next((r for r in facts.get(k,[]) if compact(r['name'])==compact(v['name']) and compact(r.get('description',''))==compact(v.get('description',''))),None)
                v['id']=same['id'] if same else prefix+'_'+signature([compact(v['name']),compact(v.get('description',''))])[:12]
            else:v['id']=same['id'] if same else prefix+'_'+signature(compact(v['name']))[:12]
            if k!='methods' and old in maps[k] and maps[k][old] not in (old,v['id']):raise ValueError('同一临时ID指向不同实体，无法确定关系：'+old)
            maps[k][old]=v['id']
    for record in answer['records']:
        v=record['value']
        if 'assessment_id' in v:v['assessment_id']=maps['assessments'].get(v['assessment_id'],v['assessment_id'])
        if record['kind']=='items':
            old=v['id'];same=next((r for r in facts.get('items',[]) if compact(r['name'])==compact(v['name']) and r['assessment_id']==v['assessment_id']),None)
            v['id']=same['id'] if same else 'I_'+signature([v['assessment_id'],compact(v['name'])])[:12];maps['items'][old]=v['id']
    for record in answer['records']:
        k=record['kind'];v=record['value']
        for term in v.get('terms',[]):term['item_id']=maps['items'].get(term['item_id'],term['item_id'])
        if k=='assessment_goal_weights':v['id']=v['assessment_id']+':'+v['goal_id']
        if k=='assessment_point_rules':v['id']=v['assessment_id']+':'+v['point_id']

def direct_matrices(blocks):
    """只读列名和坐标明确的方式—目标矩阵，保存每张表的独立方案。"""
    tables={};records=[];consumed=set()
    for b in blocks:
        if b.get('role') not in ('current','rule'):continue
        key=table_key(b)
        if key:tables.setdefault(key,[]).append(b)
    def emit(name,goal,weight,row,header,name_row=None):
        name=name.strip()
        if not name or not re.fullmatch(r'(?:0(?:\.\d+)?|1(?:\.0+)?)',weight.strip()):return
        aid='A_'+signature(compact(name))[:12];refs=list(dict.fromkeys([header['id'],*([name_row['id']] if name_row else []),row['id']]))
        records.append(record_wire('assessments',{'id':aid,'name':name},refs))
        records.append(record_wire('assessment_goal_weights',{'id':aid+':'+goal,'assessment_id':aid,'goal_id':goal,'weight':float(weight)},refs))
        consumed.add(row['id'])
    for rows in tables.values():
        # 长表：学习目标、考核方式、权重系数列。
        header=None;cols={}
        for b in rows:
            cs=cells(b);hs=[compact(c) for c in cs]
            if '学习目标' in hs and '考核方式' in hs and '权重系数' in hs:
                cols={k:hs.index(k) for k in ('学习目标','考核方式','权重系数')};header=b;continue
            if header and len(cs)>max(cols.values()):
                goal=re.fullmatch(r'(?:目标|课程目标|G)\s*(\d+)',cs[cols['学习目标']].strip())
                if goal:emit(cs[cols['考核方式']],'G'+goal[1],cs[cols['权重系数']],b,header)
        # 横表：1、2、3、4目标列，或课程目标1等；权重列与满分/均分列分离。
        goal_columns={};header=None;current_name='';name_row=None;vertical_mode=False
        for b in rows:
            cs=cells(b);hs=[compact(c) for c in cs]
            explicit={i:'G'+m[1] for i,c in enumerate(hs) if (m:=re.fullmatch(r'(?:课程目标|学习目标|目标|G)(\d+)',c))}
            if explicit:goal_columns=explicit;header=b;continue
            if '学习目标' in hs:
                numeric={i:'G'+c for i,c in enumerate(hs) if re.fullmatch(r'[1-9]\d?',c)}
                if len(numeric)>=2:goal_columns=numeric;header=b;continue
            if not goal_columns or len(cs)<=max(goal_columns):continue
            left=hs[:min(goal_columns)];name_cells=[c.strip() for c in cs[:min(goal_columns)] if c.strip()]
            if not name_cells:continue
            if any(x in left for x in ('平时成绩','验收报告','期末答辩')):
                current_name=next(x for x in left if x in ('平时成绩','验收报告','期末答辩'));name_row=b
            if '平均分' in left:vertical_mode=True
            vertical=vertical_mode and '权重' in left and current_name and not any('课程分目标' in x for x in left)
            horizontal=len(cs)>1 and re.fullmatch(r'0?\.\d+|1(?:\.0+)?',cs[1].strip()) and not any(x in left for x in ('权重','合计','总计'))
            if not vertical and not horizontal:continue
            name=current_name if vertical else cs[0]
            for col,goal in goal_columns.items():emit(name,goal,cs[col],b,header,name_row if vertical else None)
    # 同表同方式会逐目标重复声明；保留一条方式声明和完整系数。
    unique={signature(r):r for r in records}
    return list(unique.values()),consumed

def record_wire(kind,value,refs):return {'kind':kind,'value':value,'evidence_ids':refs}
