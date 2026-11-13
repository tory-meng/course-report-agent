"""在已确认DOCX母版内替换内容；固定公式图和表型来自原母版。"""
import copy
import re
from pathlib import Path
from docx import Document
from docx.text.paragraph import Paragraph
from docx.table import Table
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm,Pt, RGBColor
from .tables import show
from .report_scope import assessments,audit
from .course_presentation import enabled as course_style,human,student_score_columns

TABLE_AT={26:'T2-1',36:'T2-2',47:'T2-3',53:'T2-4',58:'T3-1',61:'T3-2',64:'T4-1',78:'T5-1',82:'T5-2',122:'T5-3',144:'T5-4'}
CAPTION_AT={85:'distribution',87:'pie',90:'score_scatter',125:'goals',147:'points'}

def set_text(p,text):
    props=copy.deepcopy(p._p.pPr)
    style=copy.deepcopy(p.runs[0]._r.rPr) if p.runs else None
    for n in list(p._p):p._p.remove(n)
    if props is not None:p._p.append(props)
    run=p.add_run(str(text))
    if style is not None:run._r.insert(0,style)
    run.font.color.rgb=RGBColor(0,0,0);run.font.strike=False

def goal_display(goal):
    ordinal=goal['id'].removeprefix('G')
    text=re.sub(r'^\s*'+re.escape(ordinal)+r'(?:[.．、]\s*|\s+)','',goal['text'])
    return re.sub(r'。{2,}','。',text)

def matrix_node(doc,prototype,data):
    node=copy.deepcopy(prototype);table=Table(node,doc);sample=copy.deepcopy(table.cell(0,0)._tc)
    table.autofit=False
    properties=node.find(qn('w:tblPr'))
    indent=properties.find(qn('w:tblInd'))
    if indent is None:indent=OxmlElement('w:tblInd');properties.append(indent)
    indent.set(qn('w:w'),'0');indent.set(qn('w:type'),'dxa')
    table_width=properties.find(qn('w:tblW'))
    if table_width is None:table_width=OxmlElement('w:tblW');properties.append(table_width)
    table_width.set(qn('w:w'),str(round(sum(data['widths_mm'])/25.4*1440)));table_width.set(qn('w:type'),'dxa')
    for n in list(node):
        if n.tag in (qn('w:tr'),qn('w:tblGrid')):node.remove(n)
    grid=OxmlElement('w:tblGrid')
    for width in data['widths_mm']:
        col=OxmlElement('w:gridCol');col.set(qn('w:w'),str(round(width/25.4*1440)));grid.append(col)
    node.insert(1,grid)
    for ri,row in enumerate(data['rows']):
        if len(row)!=len(data['widths_mm']):raise ValueError(data['id']+'表格列数不一致')
        tr=OxmlElement('w:tr');trpr=OxmlElement('w:trPr');tr.append(trpr)
        no_split=OxmlElement('w:cantSplit');trpr.append(no_split)
        if ri<data['header_rows']:trpr.append(OxmlElement('w:tblHeader'))
        for ci,value in enumerate(row):
            tc=copy.deepcopy(sample);pr=tc.find(qn('w:tcPr'))
            if pr is None:pr=OxmlElement('w:tcPr');tc.insert(0,pr)
            for tag in ('gridSpan','vMerge','shd','tcW','noWrap','tcFitText'):
                for e in list(pr.findall(qn('w:'+tag))):pr.remove(e)
            # 文字允许自然换行，短数值和学号保持整串，不压缩字体或拆数字。
            if re.fullmatch(r'-?\d+(?:\.\d+)?%?',str(value)):
                pr.append(OxmlElement('w:noWrap'))
            width=OxmlElement('w:tcW');width.set(qn('w:w'),str(round(data['widths_mm'][ci]/25.4*1440)));width.set(qn('w:type'),'dxa');pr.append(width)
            if ri<data['header_rows']:
                shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'F2F2F2');pr.append(shade)
            paras=tc.findall(qn('w:p'))
            for p in paras[1:]:tc.remove(p)
            if not paras:tc.append(OxmlElement('w:p'))
            tr.append(tc)
        node.append(tr)
    table=Table(node,doc)
    for r,row in enumerate(data['rows']):
        for c,value in enumerate(row):set_text(table.cell(r,c).paragraphs[0],value)
    for r1,c1,r2,c2 in data['merges']:
        if r1==r2 and c1==c2:continue
        text=data['rows'][r1][c1];cell=table.cell(r1,c1).merge(table.cell(r2,c2))
        set_text(cell.paragraphs[0],text)
        for p in cell.paragraphs[1:]:p._p.getparent().remove(p._p)
    # 母版首格带keepNext；不能把它复制给所有正文行并把相邻表格串成不可分页的大块。
    # 小表整体保持，长表允许按完整行分页，末行始终解除与下一个板块的绑定。
    for ri,row in enumerate(table.rows):
        keep_matrix=data['id']!='student_scores' and len(data['rows'])<=20
        merged_group=any(r1<=ri<r2 for r1,c1,r2,c2 in data['merges'])
        keep=ri<len(data['rows'])-1 and (ri<data['header_rows'] or len(data['rows'])<=6 or keep_matrix or merged_group)
        for cell in row.cells:
            for p in cell.paragraphs:p.paragraph_format.keep_with_next=keep
    return node

def generate(template,facts,calc,tables,charts,writing,out,draft=True):
    doc=Document(template['word_path']);original=list(doc.element.body)
    if len(original)<185:raise ValueError('母版节点与已确认版本不一致；重新建立绑定，不能猜索引')
    images={r['id']:r for r in charts};body=doc.element.body;result=[];meta=facts['metadata'];missing=[]
    clean=course_style(facts)
    def para(i,text=None):
        n=copy.deepcopy(original[i]);p=Paragraph(n,doc)
        if text is not None:set_text(p,text)
        if text and text.startswith(('下图','按候选代理模型','其中未达标人员')):p.paragraph_format.keep_with_next=True
        if clean and text and text.startswith('低于达成标准的学生'):p.paragraph_format.keep_with_next=True
        return n
    def caption(id,proto):
        node=para(proto,images[id]['caption'])
        Paragraph(node,doc).paragraph_format.keep_with_next=False
        return node
    def lead(id,proto):return para(proto,images[id]['lead_in'])
    def picture(id,proto=128,height=39):
        job=images.get(id)
        if not job or job['status']!='filled':
            missing.append('图位 '+id+' 缺少数据');return [para(127,'图表待补充：'+(job or {}).get('reason','缺少对应计算输入'))]
        n=para(proto);p=Paragraph(n,doc)
        for child in list(n):
            if child.tag!=qn('w:pPr'):n.remove(child)
        p.add_run().add_picture(job['path'],width=Mm(146))
        return [n]
    def table(index):
        nodes=[]
        for data in tables[TABLE_AT[index]]:
            if data['note']:
                note=para(25,data['note'].rstrip('。；')+('；∑仅汇总本表显示的目标，各项四舍五入可能有尾差。' if data['id'] in ('T3-1','T3-2','T4-1') else '。'))
                p=Paragraph(note,doc);p.paragraph_format.space_before=Pt(0);p.paragraph_format.space_after=Pt(2)
                p.paragraph_format.line_spacing=Pt(12);p.paragraph_format.keep_with_next=True
                for run in p.runs:run.font.size=Pt(9)
                nodes.append(note)
            nodes.append(matrix_node(doc,original[index],data))
        return nodes
    def below(row):
        if row.get('threshold') is None:return '达成标准尚未确认，暂不判定未达标人员。'
        ids=[calc.get('student_labels',{}).get(k,k)+'='+show(v)+'（差'+show(row['threshold']-v)+'）' for k,v in row.get('values',{}).items() if v<row['threshold']]
        if not row.get('values'):return '缺少有效个体数据，暂不能判定。'
        if clean:return '低于达成标准的学生：'+('、'.join(ids) if ids else '无')+'。'
        prefix='按候选代理模型试算低于阈值的记录：' if calc.get('evaluation_context') else '其中未达标人员：'
        return prefix+('、'.join(ids[:8])+('等，共'+str(len(ids))+'人' if len(ids)>8 else '')+'；阈值'+show(row['threshold']) if ids else ('本次试算无低于阈值记录' if calc.get('evaluation_context') else '经核验无未达标人员'))+'。'
    def paragraphs(index,strings,numbered=False):
        nodes=[para(index,('（'+str(i)+'）' if numbered else '')+(re.sub(r'^\s*(?:(?:[（(]\d+[）)]|\d+[.．、])\s*)+','',text) if numbered else text)) for i,text in enumerate(strings,1)]
        if index==39:
            for node in nodes:
                Paragraph(node,doc).paragraph_format.space_after=Pt(3)
                Paragraph(node,doc).paragraph_format.keep_together=True
        return nodes
    i=0
    while i<len(original):
        if i==5:
            fields=('班级名称','学院名称','专业名称','教师姓名')
            if any('【'+field+'】' not in Paragraph(original[5+j],doc).text for j,field in enumerate(fields)):
                raise ValueError('封面信息栏位置与母版不一致，不能猜测替换')
            from .cover_layout import info_table
            cover,cover_missing=info_table(doc,meta)
            result.append(cover);missing.extend(cover_missing);i=9;continue
        if i in TABLE_AT:result+=table(i);i+=1;continue
        if i in CAPTION_AT:result.append(caption(CAPTION_AT[i],i));i+=1;continue
        if i==30:
            result+=paragraphs(30,[g['id']+'：'+goal_display(g) for g in facts.get('goals',[])] or ['课程目标资料待补充。']);i=33;continue
        if i==39:
            if not clean and any(m.get('status')=='proposed_teaching_design' for m in facts.get('methods',[])):
                result.append(para(39,'以下为按最新适用教案与课程大纲拟定的教学安排，不作为原成绩年度逐项实施的证明。'))
            result+=paragraphs(39,writing['2.4'],True);i=44;continue
        if i==49 and calc.get('evaluation_context'):
            node=para(49);Paragraph(node,doc).paragraph_format.keep_with_next=True
            result.append(node);i+=1;continue
        if i==50:
            text=meta.get('grade_formula_text') or meta.get('total_rule_text') or '成绩构成及原始分、折合分规则尚待核对，见本轮缺项清单。'
            if not meta.get('total_rule_text'):missing.append('2.6缺少正式总评计算规则说明')
            node=para(50,text)
            if calc.get('evaluation_context'):Paragraph(node,doc).paragraph_format.keep_with_next=True
            result.append(node)
            if meta.get('total_rule_text') and meta.get('total_rule_text')!=text:
                result.append(para(50,meta['total_rule_text']))
            if calc.get('evaluation_context') and not clean:
                result.append(para(50,'教学与考核方案优先最新同课适用版本；采用新版安排不追认原成绩年度已逐项实施，原成绩学期保持不变。'))
            i=52;continue
        if i==54 and calc.get('evaluation_context'):i+=1;continue
        if i==68:
            if clean:
                thresholds=sorted({g['threshold'] for g in facts['goals'] if g.get('threshold') is not None})
                threshold_text='、'.join(format(t,'.2f') for t in thresholds)
                result.append(para(68,'课程目标达成标准设为'+threshold_text+'，参考课程库同类课程的评价方式。目标达成度按实际考核项得分率和表3-1、表3-2所列分配系数计算；观测点采用对应目标结果计算本课程的支撑贡献。教学安排采用最新适用课程材料，第6章为本课程后续教学改进方案。'))
                if facts.get('preparation',{}).get('origin')=='automatic_legacy_material_adapter':
                    result.append(para(68,'考核项按本课大纲的勾选关系分配到相应目标；同一指标点关联多个目标时，按表2-2等权汇总。课程支撑权重优先保留本课已有数值，其余采用筛选后的参考报告参数；各项系数均列入相应表格。'))
                for group in calc.get('evaluation_rigor',{}).get('equivalent_goal_groups',[]):
                    result.append(para(68,human('、'.join(group))+'使用相同的归一化评分构成，因此计算曲线一致；这种一致性来自计分规则，不单独表示不同能力的实际表现完全相同。'))
                i+=1;continue
            standards='；'.join(g['id']+'：'+show(g.get('threshold')) for g in facts.get('goals',[]))
            text='依据'+str(meta.get('standard_name') or '待补充的评价制度')+'，本轮课程目标达成标准为：'+standards+'。'
            if calc.get('evaluation_context'):text='本报告为评价方案试算，候选目标阈值为：'+standards+'；阈值及换算模型待核验，不宣称为已执行制度。'
            result.append(para(68,text))
            rigor=calc.get('evaluation_rigor',{})
            if rigor.get('mode')=='proxy':
                notes=rigor.get('notes',[])
                for note in notes:
                    if note.startswith('目标达成度') or '曲线相同' in note:result.append(para(68,note))
                reference=rigor.get('threshold_reference',{}).get('file')
                if reference:result.append(para(68,'候选阈值参考来源：'+Path(reference).name+'；尚无本课正式阈值依据。'))
            i+=1;continue
        if i==76:
            text='最终成绩采用正式登记总评。各考核方式仅在评分、满分与换算规则齐备时统计；缺项以“—”标示，各列按其有效样本计算。'
            if any(a.get('raw_item_id') for a in assessments(facts)):
                text='各分项保留记分册原始登记口径，总评采用正式登记列；中间汇总列不作为独立考核项重复计入。分项原始分均值不等同于折算贡献分或课程目标达成度。'
            result.append(para(76,text));i+=1;continue
        if i==83:i+=1;continue
        if i==81:
            result.append(para(83,'表5-2《'+meta['course_name']+'》最终成绩分布统计表'));i+=1;continue
        if i in (84,86,89):
            if i==84:result.append(lead('distribution',81))
            result+=picture({84:'distribution',86:'pie',89:'score_scatter'}[i],i,35 if i!=89 else 39);i+=1;continue
        if i==88:result.append(lead('score_scatter',88));i+=1;continue
        if i==91:
            if images.get('assessment_raw_summary',{}).get('status')=='filled':
                result.append(lead('assessment_raw_summary',127))
                result+=picture('assessment_raw_summary',128,39)
                result.append(caption('assessment_raw_summary',129))
            i+=1;continue
        if i==92 and calc.get('evaluation_context'):
            # 试算新增统计表后，母版此处的手动换页会留下只有一图的大半页空白。
            # 让5.3.2随内容自然分页，仍保留目标汇总表之前的完整分组分页。
            i+=1;continue
        if i in (139,148):i+=1;continue
        if i==94:
            if calc.get('evaluation_context') and not clean:result.append(para(95,calc['evaluation_context']['warning']))
            if audit(facts)['matrix_only_assessments']:result.append(para(95,audit(facts)['note']))
            for k,a in enumerate(assessments(facts),1):
                result.append(para(94,f'（{2*k-1}）《{meta["course_name"]}》{a["name"]}支撑观测点的达成情况'))
                from .tables import assessment_summaries
                summary=next((t for t in assessment_summaries(facts,calc) if t['assessment_id']==a['id']),None)
                if summary:
                    result.append(para(97,summary['caption']))
                    note=para(95,summary['note']);Paragraph(note,doc).paragraph_format.keep_with_next=True;result.append(note)
                    result.append(matrix_node(doc,original[122],summary))
                result.append(lead('assessment_'+a['id'],95))
                result+=picture('assessment_'+a['id'],96,39);result.append(caption('assessment_'+a['id'],97))
                result.append(para(98,f'（{2*k}）《{meta["course_name"]}》{a["name"]}学生个体达成度'))
                links=[r for r in calc['assessment_points'].values() if r['assessment_id']==a['id']]
                if not links:
                    missing.append(a['id']+'缺少方式到观测点的评分规则');result.append(para(99,'缺少本方式到观测点的明确评分规则，个体图暂不能计算。'))
                for r in links:
                    chart_id='assessment_'+a['id']+'_'+r['point_id']
                    result.append(lead(chart_id,99));result.append(para(100,below(r)))
                    result+=picture(chart_id,101,40);result.append(caption(chart_id,102))
            from .report_analysis import readable_issues
            problems=readable_issues(calc)
            if len(problems)>=2 and not clean:
                optional={'id':'T5-2P','rows':[['考核项','统计或材料证据','问题及评价边界','后续核验或改进']]+problems,
                          'merges':[],'header_rows':1,'widths_mm':[22,50,38,36]}
                result.append(para(97,'表5-2P 问题与核验事项'));result.append(matrix_node(doc,original[26],optional))
            i=119;continue
        if i==119:
            node=para(119)
            # 此小节有跨行合并的目标汇总；LO不保证跨表格行的keepNext。
            # 从新页开始，避免把同一目标的合并单元格拆到两页。
            Paragraph(node,doc).paragraph_format.page_break_before=True
            result.append(node);i+=1;continue
        if i==123:result.append(lead('goals',123));i+=1;continue
        if i==124:
            if images.get('goals',{}).get('kind')=='aggregate_summary':
                result.append(para(127,'该班级汇总不代表已取得逐人目标评分、极值或达成率。'))
                for audit_row in calc.get('aggregate_audits',[]):
                    result.append(para(127,'按表内目标权重汇总，课程整体平均达成度为'+show(audit_row['overall'])+'。'))
                    if audit_row['text_discrepancies']:result.append(para(127,'原评价表分析文字与单元格公式结果不一致，本图列示公式复算值，差异待核验。'))
            result+=picture('goals',124,38);i+=1;continue
        if i in (126,150):
            node=para(i);p=Paragraph(node,doc)
            set_text(p,p.text.replace('学生个体达成情况','学生个体达成度'))
            result.append(node);i+=1;continue
        if i==127:
            for g in facts.get('goals',[]):
                chart_id='goal_'+g['id']
                result.append(lead(chart_id,127));result.append(para(134,below(calc['goals'][g['id']])))
                result+=picture(chart_id,128,39);result.append(caption(chart_id,129))
            i=141;continue
        if i==141 and any(len(p.get('goal_weights',{}))>1 for p in facts.get('points',[])):
            # 多目标观测点含跨行合并，从新页排汇总，避免续页出现无标识的空白合并格。
            node=para(141);Paragraph(node,doc).paragraph_format.page_break_before=True
            result.append(node);i+=1;continue
        if i==145:result.append(lead('points',145));i+=1;continue
        if i==146:result+=picture('points',146,40);i+=1;continue
        if i==149:i+=1;continue
        if i==151:
            for p in facts.get('points',[]):
                chart_id='point_'+p['id']
                result.append(lead(chart_id,151));result.append(para(152,below(calc['points'][p['id']])))
                result+=picture(chart_id,153,39);result.append(caption(chart_id,154))
            i=164;continue
        if clean and i in (164,165,175):
            result.append(para(i,{164:'6 教学改进与实施方案',165:'6.1 改进重点与教学安排',175:'6.2 教学实施安排与效果评价'}[i]));i+=1;continue
        if i==166:result+=paragraphs(166,writing['6.1']);i=175;continue
        if i==176:result+=paragraphs(176,writing['6.2']);i=179;continue
        if i==180:
            nodes=paragraphs(180,writing['7'],True)
            if clean:
                # 短小的改进安排和签署栏一起分页，避免末段单独占据一页。
                for node in nodes:
                    Paragraph(node,doc).paragraph_format.keep_with_next=True
                    Paragraph(node,doc).paragraph_format.keep_together=True
            result+=nodes;i=184;continue
        result.append(copy.deepcopy(original[i]));i+=1
    if clean:
        # 在图题后写分析，顺序固定为图、图题、分析，避免只有泛泛的图前介绍。
        expanded=[]
        by_caption={job['caption']:job for job in charts if job['status']=='filled'}
        for node in result:
            expanded.append(node)
            if node.tag==qn('w:p'):
                p=Paragraph(node,doc);job=by_caption.get(p.text)
                if job and job.get('analysis'):
                    p.paragraph_format.keep_with_next=True
                    analysis=para(127,job['analysis']);ap=Paragraph(analysis,doc)
                    ap.paragraph_format.keep_with_next=False;ap.paragraph_format.space_after=Pt(5)
                    expanded.append(analysis)
        result=expanded
        # 完整实名原始成绩表包含缺评分的登记人员，不将缺失值变成零。
        records=calc.get('student_records',[])
        section=result.pop() if result and result[-1].tag==qn('w:sectPr') else None
        source_notes=facts.get('metadata',{}).get('source_record_notes',[])
        if source_notes:
            heading=para(179,'原始成绩记录说明')
            Paragraph(heading,doc).paragraph_format.keep_with_next=True
            result.append(heading)
            result+=paragraphs(127,source_notes)
        columns=student_score_columns(facts)
        if records:
            heading=para(179,'附表 学生原始成绩明细')
            Paragraph(heading,doc).paragraph_format.page_break_before=True
            Paragraph(heading,doc).paragraph_format.keep_with_next=True
            result.append(heading)
            rows=[['序号','学号','姓名']+[human(name) for _,name in columns]+['总评成绩']]
            for ix,s in enumerate(records,1):
                from .course_presentation import student_total_text
                total=show(s.get('total')) if s.get('total') is not None else '未评分'
                total=student_total_text(s,total,s['key'] in calc['total']['values'])
                rows.append([str(ix),s['id'],s['name']]+[show(s['scores'].get(key)) if s['scores'].get(key) is not None else '未评分' for key,_ in columns]+[total])
            widths=[10,32,22]+[62/len(columns)]*len(columns)+[20] if columns else [10,32,22,82]
            result.append(matrix_node(doc,original[78],{'id':'student_scores','rows':rows,'header_rows':1,'merges':[],'widths_mm':widths}))
        if section is not None:result.append(section)
    for n in list(body):body.remove(n)
    for n in result:body.append(n)
    date=str(meta.get('report_date','')).split('-');date=(date+['','',''])[:3]
    variables={'学院名称':meta.get('college'),'课程名称':meta['course_name'],'班级名称':meta.get('class_name'),'专业名称':meta.get('major'),
       '教师姓名':meta.get('teacher'),'报告年':date[0],'月':date[1],'日':date[2],'课程代码':meta.get('course_code'),'课程性质':meta.get('course_nature'),
       '课程类别':meta.get('course_category'),'总学时':meta.get('hours'),'学分':meta.get('credits'),'年级':meta.get('grade'),'学期':meta.get('term'),
       '专业':meta.get('major'),'学年':meta.get('academic_year'),'学期序号':meta.get('semester'),'班级范围':meta.get('class_name'),
       '登记人数':calc['registered_count'],'签名留空，由本人签署':'________________','评价日期':meta.get('report_date')}
    def substitute(match):
        key=match.group(1);v=variables.get(key)
        if v is None or v=='':missing.append('元数据缺少'+key);return '待补充'
        return str(v)
    for p in doc.paragraphs:
        if clean and not p._p.xpath('.//w:drawing'):
            changed=human(p.text)
            if changed!=p.text:set_text(p,changed)
        if '【' in p.text:set_text(p,re.sub(r'【([^】]+)】',substitute,p.text))
        if p.text.strip()=='《'+meta['course_name']+'》':
            # 长课程名按可用行宽适配字号，避免只剩“计》”一类尾字单独换行。
            import unicodedata,math
            units=sum(1 if unicodedata.east_asian_width(c) in ('W','F') else .55 for c in p.text)
            section=doc.sections[0];available=(section.page_width-section.left_margin-section.right_margin)/Pt(1)
            fitted=math.floor(available/(units+1))
            for run in p.runs:
                original_size=(run.font.size or p.style.font.size or Pt(26)).pt
                if fitted<original_size:run.font.size=Pt(max(18,fitted))
        if calc.get('sample_scope',{}).get('multiple_classes'):
            if p.text.startswith('评价样本：'):
                classes=calc['sample_scope']['class_names'];names='、'.join(classes[:4])+('等' if len(classes)>4 else '')
                set_text(p,f'评价样本：本次采用{meta.get("academic_year","")}学年第{meta.get("semester","")}学期《{meta["course_name"]}》实际登记名单，共{calc["registered_count"]}人，来自{names}。名单包含多个行政班，不统一归为单一年级。')
            elif p.text.startswith('开课年级：'):set_text(p,'开课年级：按各行政班名册，不合并标注为单一年级')
            elif p.text.startswith('下表给出了') and meta.get('grade'):set_text(p,p.text.replace(str(meta['grade'])+'级','本次登记名单中'))
        if p.text.startswith('评价样本：') and calc['registered_count']!=calc['total']['valid_count']:
            count=calc['total']['valid_count'];excluded=calc['registered_count']-count
            set_text(p,p.text+f'其中有效总评{count}人，其余{excluded}人不计入总评统计；空白成绩不补零，原始记录保留在附表。')
        if re.match(r'^表\d|^（\d+）《',p.text) or '如下表所示' in p.text or '如下表' in p.text or p.text.startswith('下表给出'):
            p.paragraph_format.keep_with_next=True
        if p._p.xpath('.//w:drawing'):
            p.paragraph_format.keep_with_next=True
    if draft:
        for section in doc.sections:
            label='' if clean else ('方案试算草稿　含代理估计，待核验' if calc.get('evaluation_context') else '审核草稿　'+('离线流程演示' if template.get('offline') else '待人工核验'))
            for header in (section.header,section.first_page_header,section.even_page_header):set_text(header.paragraphs[0],label)
    out=Path(out);out.parent.mkdir(parents=True,exist_ok=True);doc.save(out)
    return {'path':str(out),'missing_fields':sorted(set(missing)),'table_count':len(doc.tables),'paragraph_count':len(doc.paragraphs)}
