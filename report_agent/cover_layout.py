"""课程封面的固定信息栏；样式由模板渲染层确定，不交给模型排版。"""
from docx.enum.table import WD_TABLE_ALIGNMENT,WD_CELL_VERTICAL_ALIGNMENT,WD_ROW_HEIGHT_RULE
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm,Pt,RGBColor

FIELDS=(('学生所在班级：','class_name'),('学院：','college'),('专业：','major'),('主讲教师：','teacher'))
WIDTHS_MM=(40,78)

def info_table(doc,metadata):
    """两列等宽信息栏，标签分散对齐、字段居中、统一底线；长值自然换行。"""
    table=doc.add_table(rows=len(FIELDS),cols=2)
    table.autofit=False;table.alignment=WD_TABLE_ALIGNMENT.CENTER
    pr=table._tbl.tblPr
    layout=pr.find(qn('w:tblLayout'))
    if layout is None:layout=OxmlElement('w:tblLayout');pr.append(layout)
    layout.set(qn('w:type'),'fixed')
    table_width=pr.find(qn('w:tblW'))
    table_width.set(qn('w:type'),'dxa');table_width.set(qn('w:w'),str(round(sum(WIDTHS_MM)*1440/25.4)))
    borders=OxmlElement('w:tblBorders')
    for edge in ('top','left','bottom','right','insideH','insideV'):
        e=OxmlElement('w:'+edge);e.set(qn('w:val'),'nil');borders.append(e)
    pr.append(borders)
    for col,width in zip(table.columns,WIDTHS_MM):col.width=Mm(width)
    missing=[]
    for row,(label,key) in zip(table.rows,FIELDS):
        row.height=Mm(8.5);row.height_rule=WD_ROW_HEIGHT_RULE.AT_LEAST
        row._tr.get_or_add_trPr().append(OxmlElement('w:cantSplit'))
        value=metadata.get(key)
        if value is None or not str(value).strip():value='—';missing.append('封面缺少'+key)
        if key=='class_name' and '、' in str(value) and len(str(value))>18:
            value=str(value).replace('、','\n')
        for ci,(cell,width,content) in enumerate(zip(row.cells,WIDTHS_MM,(label,str(value)))):
            cell.width=Mm(width);cell.vertical_alignment=WD_CELL_VERTICAL_ALIGNMENT.CENTER
            cp=cell._tc.get_or_add_tcPr()
            margins=OxmlElement('w:tcMar')
            for edge,size in [('top',0),('bottom',0),('left',0),('right',80 if ci==0 else 0)]:
                e=OxmlElement('w:'+edge);e.set(qn('w:w'),str(size));e.set(qn('w:type'),'dxa');margins.append(e)
            cp.append(margins)
            if ci==1:
                cb=OxmlElement('w:tcBorders');line=OxmlElement('w:bottom')
                for attr,v in [('val','single'),('sz','4'),('color','000000'),('space','0')]:line.set(qn('w:'+attr),v)
                cb.append(line);cp.append(cb)
            p=cell.paragraphs[0]
            p.alignment=WD_ALIGN_PARAGRAPH.DISTRIBUTE if ci==0 else WD_ALIGN_PARAGRAPH.CENTER
            fmt=p.paragraph_format;fmt.space_before=Pt(0);fmt.space_after=Pt(0)
            fmt.line_spacing=1.05;fmt.first_line_indent=Mm(0);fmt.left_indent=Mm(0);fmt.right_indent=Mm(0)
            fmt.keep_with_next=key!=FIELDS[-1][1];fmt.keep_together=True
            run=p.add_run(content);run.font.name='宋体';run.font.size=Pt(15)
            run.font.color.rgb=RGBColor(0,0,0);run.font.bold=False;run.font.underline=False
            run._r.get_or_add_rPr().rFonts.set(qn('w:eastAsia'),'宋体')
    node=table._tbl;node.getparent().remove(node)
    return node,missing
