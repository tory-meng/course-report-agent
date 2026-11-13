#!/usr/bin/env python3
"""从公开占位布局重建演示Word母版；不读取任何真实课程或原学校模板。"""
import argparse
import hashlib
import json
import re
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt

ROOT = Path(__file__).resolve().parents[1]
TABLE_AT = {26:'T2-1',36:'T2-2',47:'T2-3',53:'T2-4',58:'T3-1',61:'T3-2',64:'T4-1',78:'T5-1',82:'T5-2',122:'T5-3',144:'T5-4'}

def math_run(parent, text):
    run = OxmlElement('m:r')
    node = OxmlElement('m:t')
    node.text = text
    run.append(node)
    parent.append(run)

def equation(paragraph, left, numerator, denominator, label):
    """可编辑的原生OMML分式，语义与计算工具使用的公式相同。"""
    math = OxmlElement('m:oMath')
    math_run(math, left + ' = ')
    fraction = OxmlElement('m:f')
    num = OxmlElement('m:num')
    den = OxmlElement('m:den')
    math_run(num, numerator)
    math_run(den, denominator)
    fraction.extend([num, den])
    math.append(fraction)
    math_run(math, '    ' + label)
    paragraph._p.append(math)
    paragraph.alignment = WD_ALIGN_PARAGRAPH.CENTER
    paragraph.paragraph_format.space_after = Pt(10)

def build(directory):
    contract_file = directory / 'template_contract.json'
    contract = json.loads(contract_file.read_text(encoding='utf-8'))
    layout = json.loads((directory / 'template_layout.json').read_text(encoding='utf-8'))
    doc = Document()
    sec = doc.sections[0]
    sec.page_width, sec.page_height = Mm(210), Mm(297)
    sec.top_margin = sec.bottom_margin = Mm(23)
    sec.left_margin, sec.right_margin = Mm(32.5), Mm(31.5)
    normal = doc.styles['Normal']
    normal.font.name = 'Noto Serif CJK SC'
    normal._element.rPr.rFonts.set(qn('w:eastAsia'), 'Noto Serif CJK SC')
    normal.font.size = Pt(11)
    normal.paragraph_format.line_spacing = 1.25
    normal.paragraph_format.space_after = Pt(4)
    tables = {t['id']:t for t in contract['tables']}
    for expected, item in enumerate(layout['body_nodes']):
        if expected != item['index']:
            raise ValueError('布局节点索引不连续')
        if item['kind'] == 'table':
            spec = tables[TABLE_AT[expected]]
            table = doc.add_table(rows=len(spec['rows']), cols=len(spec['widths_mm']))
            table.style = 'Table Grid'
            table.autofit = False
            for ri, row in enumerate(spec['rows']):
                for ci, value in enumerate(row):
                    cell = table.cell(ri,ci)
                    cell.width = Mm(spec['widths_mm'][ci])
                    cell.text = str(value)
                    for p in cell.paragraphs:
                        p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                        p.paragraph_format.space_after = Pt(2)
                        p.paragraph_format.line_spacing = 1.05
                        for run in p.runs:
                            run.font.size = Pt(9.5)
            for r1,c1,r2,c2 in spec.get('merges',[]):
                table.cell(r1,c1).merge(table.cell(r2,c2))
        else:
            text = item['text']
            p = doc.add_paragraph(text)
            if expected in (1,2,3,9):
                p.alignment = WD_ALIGN_PARAGRAPH.CENTER
                p.paragraph_format.space_after = Pt(12)
                if expected in (2,3):
                    for run in p.runs:
                        run.bold = True
                        run.font.size = Pt(19 if expected==3 else 17)
            if expected==11:
                p.paragraph_format.page_break_before = True
            if expected==180:
                # 第7节由此原型复制；与最终签署栏保持相邻，避免只有签名的孤页。
                p.paragraph_format.keep_with_next = True
            if re.match(r'^\d+(?:\.\d+)*\s',text):
                p.paragraph_format.keep_with_next = True
                for run in p.runs:
                    run.bold = True
                    run.font.size = Pt(12)
            if expected==71:
                equation(p,'A(s,i)','Σm x(s,i,m)·b(i,m)','Σm M(i,m)·b(i,m)','（式-1）')
            if expected==72:
                equation(p,'R(i)','count{s∈valid(i): A(s,i)≥θ(i)}','|valid(i)|','（式-2）')
            if expected in (91,139,148):
                for run in p.runs:
                    run.font.strike = True
    if len(list(doc.element.body)) != 186:
        raise ValueError('母版节点结构与渲染绑定不一致')
    doc.core_properties.author = 'Course Report Agent Contributors'
    doc.core_properties.title = 'Public synthetic course report demonstration template'
    doc.core_properties.subject = 'Generated template; no institutional branding or real course materials'
    output = directory / contract['assets']['word_template']
    doc.save(output)
    contract['asset_sha256'] = {key:hashlib.sha256((directory/relative).read_bytes()).hexdigest()
                              for key,relative in contract['assets'].items()}
    contract_file.write_text(json.dumps(contract,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('演示模板与资源SHA绑定已生成：',output.relative_to(ROOT))
    return output

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',default='templates/course/buu_course_r2')
    args = parser.parse_args()
    directory = Path(args.directory)
    build(directory if directory.is_absolute() else ROOT/directory)

if __name__=='__main__':
    main()
