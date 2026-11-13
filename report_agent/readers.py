"""确定性读取原始资料；保留每行/单元格定位，不执行文件中的代码或公式。"""
import csv
import io
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from .common import path, digest

def clean(v):
    if v is None: return ''
    if isinstance(v, float) and v.is_integer(): return int(v)
    return v if isinstance(v, (str, int, float, bool)) else str(v)

def excel_sheets(p):
    if p.suffix.lower() in ('.csv', '.tsv'):
        raw = p.read_bytes()
        try: text = raw.decode('utf-8-sig')
        except UnicodeDecodeError: text = raw.decode('gb18030')
        return [{'name': p.stem, 'rows': list(csv.reader(io.StringIO(text), delimiter='\t' if p.suffix.lower() == '.tsv' else ',')), 'formula_cells': []}]
    if p.suffix.lower() == '.xls':
        import xlrd
        book = xlrd.open_workbook(p, formatting_info=True)
        return [{'name': s.name, 'rows': [[clean(s.cell_value(r,c)) for c in range(s.ncols)] for r in range(s.nrows)],
                 'formula_cells': [], 'merged_cells': [list(v) for v in s.merged_cells]} for s in book.sheets()]
    import openpyxl
    book = openpyxl.load_workbook(p, data_only=True)
    formulas = openpyxl.load_workbook(p, data_only=False)
    result = []
    for sheet in book:
        rows = [[clean(c.value) for c in row] for row in sheet]
        bad = [c.coordinate for row in formulas[sheet.title] for c in row if c.data_type == 'f' and sheet[c.coordinate].value is None]
        # Numeric IDs with an explicit 000... format retain their displayed leading zeros.
        for row in sheet:
            for c in row:
                if isinstance(c.value, (int, float)) and re.fullmatch(r'0+', c.number_format or ''):
                    rows[c.row-1][c.column-1] = str(int(c.value)).zfill(len(c.number_format))
        result.append({'name': sheet.title, 'rows': rows, 'formula_cells': bad,
                       'merged_cells': [str(r) for r in sheet.merged_cells.ranges],
                       'formulas': {c.coordinate:{'expression':c.value,'cached_value':clean(sheet[c.coordinate].value)}
                                    for row in formulas[sheet.title] for c in row if c.data_type=='f'}})
    book.close(); formulas.close(); return result

def ocr_image(data, lang):
    if not shutil.which('tesseract'): raise RuntimeError('扫描资料需要tesseract及中文语言包')
    with tempfile.TemporaryDirectory(prefix='report_ocr_') as tmp:
        f = Path(tmp) / 'page.png'; f.write_bytes(data)
        r = subprocess.run(['tesseract', str(f), 'stdout', '-l', lang], capture_output=True, text=True, timeout=180)
        if r.returncode: raise RuntimeError(r.stderr[-800:])
        return r.stdout.strip()

def read_source(source, ocr=False, ocr_lang='chi_sim+eng'):
    p = path(source['path'])
    if not p.is_file(): raise FileNotFoundError(p)
    sid = source['id']; role = source.get('role', 'current')
    if role not in ('current', 'rule', 'history', 'style_reference'): raise ValueError('未知来源role：' + role)
    result = {'id': sid, 'path': str(p), 'sha256': digest(p), 'role': role, 'kind': source.get('kind', 'document'), 'blocks': [], 'sheets': []}
    def block(loc, text, **layout):
        if text.strip(): result['blocks'].append({'id': f'{sid}:{loc}', 'text': text, 'source_id': sid, 'role': role, **layout})
    ext = p.suffix.lower()
    if ext in ('.xls', '.xlsx', '.csv', '.tsv'):
        result['sheets'] = excel_sheets(p)
        for sheet in result['sheets']:
            for i, row in enumerate(sheet['rows'], 1):
                block(f'{sheet["name"]}:R{i}', ' | '.join(f'C{j}={v}' for j, v in enumerate(row,1) if str(v).strip()),
                      table_id=f'{sid}:{sheet["name"]}',row=i,
                      cells=[{'column':j,'text':str(v)} for j,v in enumerate(row,1)],
                      merged_cells=sheet.get('merged_cells',[]))
    elif ext in ('.docx','.doc'):
        if ext == '.doc':
            executable=shutil.which('libreoffice') or shutil.which('soffice')
            if not executable:raise RuntimeError('读取旧.doc需要LibreOffice，请安装后重试；不会跳过此材料')
            with tempfile.TemporaryDirectory(prefix='report_doc_') as tmp:
                folder=Path(tmp)
                command=[executable,'-env:UserInstallation='+(folder/'profile').as_uri(),'--headless',
                         '--convert-to','docx','--outdir',str(folder),str(p)]
                converted=subprocess.run(command,capture_output=True,text=True,timeout=180)
                target=folder/(p.stem+'.docx')
                if converted.returncode or not target.is_file():raise RuntimeError('旧.doc转换失败：'+p.name+' '+converted.stderr[-500:])
                parsed=read_source({**source,'path':str(target)},ocr,ocr_lang)
                result.update(blocks=parsed['blocks'],sheets=parsed['sheets'],conversion='libreoffice_doc_to_docx')
                return result
        from docx import Document
        from docx.text.paragraph import Paragraph
        from docx.table import Table
        from docx.oxml.ns import qn
        doc = Document(p)
        for i, node in enumerate(doc.element.body,1):
            if node.tag == qn('w:p'): block(f'P{i}', Paragraph(node,doc).text)
            elif node.tag == qn('w:tbl'):
                for j, row in enumerate(Table(node,doc).rows,1):
                    unique=[];seen={};positions=[]
                    for col,c in enumerate(row.cells,1):
                        if c._tc not in seen:
                            seen[c._tc]=col;unique.append(c.text)
                        positions.append({'column':col,'text':c.text,'anchor_column':seen[c._tc],
                                          'grid_span':c._tc.grid_span,'vertical_merge':str(c._tc.vMerge or '')})
                    block(f'T{i}R{j}', ' | '.join(unique),table_id=f'{sid}:T{i}',row=j,cells=positions)
        if ocr:
            for rid, rel in doc.part.rels.items():
                if 'image' in rel.reltype:
                    try: text = ocr_image(rel.target_part.blob, ocr_lang)
                    except RuntimeError as e: raise RuntimeError(f'{sid}内嵌图{rid} OCR失败：{e}') from e
                    block(f'image-{rid}',text)
    elif ext == '.pdf':
        import pymupdf as fitz
        with fitz.open(p) as doc:
            for i, page in enumerate(doc,1):
                text = page.get_text()
                if len(text.strip()) < 20:
                    if not ocr: raise ValueError(f'{p.name}第{i}页需OCR；case中设置ocr=true')
                    text = ocr_image(page.get_pixmap(matrix=fitz.Matrix(2,2)).tobytes('png'), ocr_lang)
                block(f'page{i}', text)
    elif ext in ('.txt', '.md', '.json'):
        for i, text in enumerate(p.read_text(encoding='utf-8-sig').splitlines(),1): block(f'L{i}',text)
    else: raise ValueError(f'暂不支持{ext}；旧.doc请先用LibreOffice转换为.docx')
    if not result['blocks']: raise ValueError(f'未读取到有效资料：{p}')
    return result

def ingest(case, progress=None):
    sources = case.get('sources', [])
    ids = [s['id'] for s in sources]
    if not sources or len(ids) != len(set(ids)): raise ValueError('来源清单为空或source id重复')
    result=[]
    for i,s in enumerate(sources):
        if progress:progress.units(i,len(sources),'读取 '+str(s['path']))
        result.append(read_source(s,case.get('ocr',False),case.get('ocr_lang','chi_sim+eng')))
        if progress:progress.units(i+1,len(sources),'资料读取完成')
    return result

def evidence_index(sources):
    return {b['id']: b for s in sources for b in s['blocks'] if s['role'] != 'style_reference'}
