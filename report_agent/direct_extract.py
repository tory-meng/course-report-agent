"""只识别表格中明确的标签/值；不推断评分规则、不丢弃未知内容。"""
import re

LABELS={
    '课程名称':'course_name','课程名':'course_name',
    '教师姓名':'teacher','任课教师':'teacher','授课教师':'teacher',
    '学期':'term','开课学期':'term',
    '课程代码':'course_code','课程编号':'course_code',
    '学院':'college','院系':'college','开课学院':'college','专业':'major','班级':'class_name',
    '课号':'offering_code','考核形式':'assessment_form','考核类型':'assessment_form',
    '学年':'academic_year','学期序号':'semester','年级':'grade',
    '总评满分':'total_max','总成绩满分':'total_max','及格分数':'pass_score',
}

def course_label(value):
    """只拆分约定的末尾考核形式；不截断普通斜杠课程名。原文由quotes保留。"""
    match=re.fullmatch(r'(.+?)\s*[/／]\s*(考试|考查)',value.strip())
    if match:return {'course_name':match[1].strip(),'assessment_form':match[2]}
    return {'course_name':value}

def labelled_row(block):
    """返回字段及整行是否完全识别。只接受读取器的C数字=单元格格式。"""
    if block.get('role') not in ('current','rule'):return {},False
    parts=block['text'].split(' | ')
    matches=[re.fullmatch(r'C(\d+)=(.*)',p,flags=re.S) for p in parts]
    if not all(matches):return {},False
    cells=[m[2].strip() for m in matches];used=set();values={}
    for i,cell in enumerate(cells):
        if i in used:continue
        heading=re.fullmatch(r'(20\d{2})\s*[-—－至]\s*(20\d{2})\s*学年\s*第\s*([一二三四12])\s*学期\s*(?:记分册|成绩登记表|成绩表)',cell)
        if heading:
            semester={'一':'1','二':'2','三':'3','四':'4'}.get(heading[3],heading[3])
            values.update(academic_year=heading[1]+'-'+heading[2],semester=semester);used.add(i);continue
        pair=re.fullmatch(r'([^：:]+)[：:](.*)',cell,flags=re.S)
        # 名单表头“专业 | 班级 | 总评”不能误认成标签/值。
        if pair is None:continue
        label=pair[1].strip()
        key=LABELS.get(label)
        if not key:continue
        value=pair[2].strip() if pair else ''
        consumed={i}
        if not value:
            # 下一非空单元格若仍是标签，不跨标签寻找一个貌似合理的值。
            if i+1>=len(cells):continue
            candidate=cells[i+1]
            if candidate.rstrip('：:').strip() in LABELS or re.search(r'[：:]',candidate):continue
            value=candidate;consumed.add(i+1)
        if not value or len(value)>150 or re.search(r'[\n\r；;。：:]',value):continue
        if key in ('total_max','pass_score'):
            numeric=re.fullmatch(r'(\d+(?:\.\d+)?)\s*分?',value)
            if not numeric:continue
            value=float(numeric[1])
        fields=course_label(value) if key=='course_name' else {key:value}
        if any(k in values and values[k]!=v for k,v in fields.items()):return {},False
        values.update(fields);used.update(consumed)
    return values,bool(values) and len(used)==len(cells)


def split_direct(blocks):
    records=[];remaining=[]
    for block in blocks:
        values,covered=labelled_row(block) if 'metadata' in block['extract_kinds'] else ({},False)
        if covered:records.append({'kind':'metadata','value':values,'evidence_ids':[block['id']]})
        if not covered:remaining.append(block)
    return records,remaining
