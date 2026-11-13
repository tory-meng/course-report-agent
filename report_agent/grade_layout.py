"""成绩表布局诊断。只提出有坐标的列候选，不由分数拟合或猜测评分制度。"""
import re

ALIASES={
 'student_id':['学号','学生学号','学籍号','学籍编号','学生编号'],
 'name':['姓名','学生姓名'],
 'total':['总评','总评成绩','最终成绩','总成绩','综合成绩','课程总评成绩','总评得分'],
 'status':['状态','考试状态','成绩状态']}

def plain(value):return re.sub(r'\s+','',str(value))

def label(value):
    value=plain(value).replace('（','(').replace('）',')')
    return re.sub(r'\([^)]*\)','',value)

def is_student_id(value,pattern=r'[A-Za-z0-9_-]+'):
    value=plain(value)
    if value.upper() in ('TOTAL','SUM','AVERAGE','COUNT') or value in ALIASES['student_id']:return False
    return bool(value and re.fullmatch(pattern,value))

def basis(text):
    if '未折算' in text:return 'raw'
    if any(w in text for w in ('折合','折算','贡献分','计入总评')):return 'converted'
    if any(w in text for w in ('原始','百分制','未折算')):return 'raw'
    return 'unknown'

def _header_grid(sheet,end):
    rows=[list(row) for row in sheet['rows'][:end]]
    for merged in sheet.get('merged_cells',[]):
        if isinstance(merged,str):
            from openpyxl.utils.cell import range_boundaries
            c1,r1,c2,r2=range_boundaries(merged);r1-=1;c1-=1
        else:r1,r2,c1,c2=merged
        if r1>=end or r2>end:continue
        value=rows[r1][c1] if c1<len(rows[r1]) else ''
        for r in range(r1,r2):
            while len(rows[r])<c2:rows[r].append('')
            for c in range(c1,c2):
                if not str(rows[r][c]).strip():rows[r][c]=value
    return rows

def layouts(sheet):
    """学号表头起点到首个学生行之间合成表头路径，最多6行；不向成绩区填充合并值。"""
    rows=sheet['rows'];result=[]
    for start,row in enumerate(rows[:30]):
        idcols=[i for i,v in enumerate(row) if label(v) in ALIASES['student_id']]
        if len(idcols)!=1:continue
        idcol=idcols[0];first=None
        for n in range(start+1,min(len(rows),start+7)):
            sid=plain(rows[n][idcol]) if idcol<len(rows[n]) else ''
            if is_student_id(sid):
                first=n;break
        if first is None:continue
        grid=_header_grid(sheet,first);width=max(len(r) for r in grid[start:first]);columns=[]
        for col in range(width):
            path=[]
            for head in grid[start:first]:
                value=str(head[col]).strip() if col<len(head) else ''
                if value and value not in path:path.append(value)
            leaf=path[-1] if path else '';text=' / '.join(path)
            roles=[k for k,aliases in ALIASES.items() if any(label(v) in aliases for v in path)]
            # 分组“成绩”不等同于正式总评，且不得遮盖最下层分项名。
            if not roles and label(leaf)=='成绩':roles=['generic_score']
            weights=re.findall(r'(\d+(?:\.\d+)?)\s*[%％]',text)
            columns.append({'column':col+1,'label':leaf,'header_path':path,'role':roles[0] if len(roles)==1 else 'unknown',
                            'score_basis':basis(text),'declared_weight':float(weights[-1])/100 if len(set(weights))==1 else None})
        if any(c['role']=='student_id' and c['column']==idcol+1 for c in columns):
            result.append({'sheet':sheet['name'],'header_start':start+1,'header_row':first,'student_id':idcol+1,'columns':columns})
    return result

def mappings(source,items=(),generic_total=False):
    result=[]
    for sheet in source.get('sheets',[]):
        for layout in layouts(sheet):
            columns=layout['columns'];totals=[c for c in columns if c['role']=='total']
            if not totals and generic_total:totals=[c for c in columns if c['role']=='generic_score']
            for total in totals:
                mapping={k:layout[k] for k in ('sheet','header_row','student_id','header_start')}
                mapping.update(source_id=source['id'],total=total['column'],items={},item_metadata={},header_paths={str(c['column']):c['header_path'] for c in columns})
                for key in ('name','status'):
                    cols=[c for c in columns if c['role']==key]
                    if len(cols)==1:mapping[key]=cols[0]['column']
                for item in items:
                    cols=[c for c in columns if c['column']!=total['column'] and any(label(v)==label(item['name']) for v in c['header_path'])]
                    if len(cols)>1 and item.get('score_basis') in ('raw','converted'):
                        cols=[c for c in cols if c['score_basis']==item['score_basis']]
                    if len(cols)==1:
                        c=cols[0];mapping['items'][item['id']]=c['column'];mapping['item_metadata'][item['id']]=c
                result.append(mapping)
    return result

def diagnostic(sources,items=()):
    rows=[]
    for source in sources:
        if source.get('role')!='current':continue
        for sheet in source.get('sheets',[]):
            for layout in layouts(sheet):
                candidates=mappings({**source,'sheets':[sheet]},items,True)
                issues=[]
                if len(candidates)!=1:issues.append('正式总评列需核对；不默认取最后一列')
                unknown=[c['column'] for c in layout['columns'] if c['role'] in ('unknown','generic_score') and c['header_path']]
                rows.append({'source_id':source['id'],'source_sha256':source.get('sha256'),**layout,
                             'candidate_mappings':candidates,'unresolved_columns':unknown,'issues':issues})
    return {'version':'grade_layout_v1','tables':rows,'policy':'列语义建议不改变原始分数；原始分与折算分、重复总评及身份歧义须核对'}
