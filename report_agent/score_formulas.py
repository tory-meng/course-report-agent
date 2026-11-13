"""检查全部成绩列的公式来源，包括没有命名总评列的核算表。原始缓存永不重写。"""
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from .common import save,digest
from .grade_layout import layouts,is_student_id

VOLATILE=re.compile(r'\b(?:RAND|RANDBETWEEN|RANDARRAY)\s*\(',re.I)
REF=re.compile(r'\$?([A-Z]{1,3})\$?([1-9]\d*)\b')

def unsafe_cells(formulas):
    """随机生成列及其直接/间接依赖列不作为观测成绩。"""
    unsafe={cell:{'reason':'volatile_random_function','formula':f} for cell,f in formulas.items() if VOLATILE.search(f)}
    changed=True
    while changed:
        changed=False
        for cell,f in formulas.items():
            deps={c+r for c,r in REF.findall(f)}&set(unsafe)
            if cell not in unsafe and deps:
                unsafe[cell]={'reason':'depends_on_random_generated_score','depends_on':sorted(deps),'formula':f};changed=True
    return unsafe

def inventory(case,sources,out):
    import openpyxl
    out=Path(out);folder=out/'formula_source';folder.mkdir(parents=True,exist_ok=True)
    reports=[]
    for source in sources:
        if source.get('role')!='current' or source.get('kind')!='grades':continue
        sheets=[(s,layouts(s)) for s in source.get('sheets',[])];sheets=[(s,ls) for s,ls in sheets if ls]
        if not sheets:continue
        original=Path(source['path']);fp=original
        if original.suffix.lower()=='.xls':
            exe=shutil.which('libreoffice') or shutil.which('soffice')
            if not exe:raise ValueError('缺少现有XLS公式读取工具')
            with tempfile.TemporaryDirectory(prefix='score_formula_profile_') as profile:
                proc=subprocess.run([exe,'-env:UserInstallation='+Path(profile).as_uri(),'--headless','--convert-to','xlsx','--outdir',str(folder),str(original)],capture_output=True,text=True,timeout=180)
            fp=folder/(original.stem+'.xlsx')
            if proc.returncode or not fp.is_file():raise ValueError('成绩公式副本读取失败：'+original.name)
        if fp.suffix.lower()!='.xlsx':continue
        wb=openpyxl.load_workbook(fp,data_only=False)
        try:
            for sheet,ls in sheets:
                ws=wb[sheet['name']];formulas={};score_cells=set()
                for layout in ls:
                    for n,row in enumerate(sheet['rows'],1):
                        if n<=layout['header_row'] or len(row)<layout['student_id'] or not is_student_id(row[layout['student_id']-1],case.get('student_id_pattern',r'[A-Za-z0-9_-]+')):continue
                        for col,c in enumerate(row,1):
                            coord=ws.cell(n,col).coordinate;f=ws.cell(n,col).value
                            if isinstance(f,str) and f.startswith('='):formulas[coord]=f
                            if col==layout['student_id']:continue
                            try:float(c)
                            except (ValueError,TypeError):continue
                            score_cells.add(coord)
                bad={k:v for k,v in unsafe_cells(formulas).items() if k in score_cells}
                reports.append({'source_id':source['id'],'source_sha256':digest(original),'sheet':sheet['name'],
                    'formulas':formulas,'unsafe_score_cells':bad,'status':'contains_generated_components' if bad else 'no_random_score_dependencies',
                    'copy_used_for':'formula_text_only','original_cached_scores_preserved':True})
        finally:wb.close()
    result={'sheets':reports,'status':'reviewed','policy':'仅读取原成绩缓存；转换副本中的重算数值不作为源成绩。随机生成列及其依赖不得映射为评分项。'}
    save(out/'score_formula_inventory.json',result)
    return result

def enforce(mappings,report):
    import openpyxl
    for mapping in mappings:
        cols=set(mapping.get('items',{}).values())
        if mapping.get('total'):cols.add(mapping['total'])
        for row in report['sheets']:
            if row['source_id']!=mapping['source_id'] or row['sheet']!=mapping['sheet']:continue
            for cell in row['unsafe_score_cells']:
                col,_=openpyxl.utils.cell.coordinate_from_string(cell)
                if openpyxl.utils.column_index_from_string(col) in cols:
                    raise ValueError('评分映射使用了随机生成或反推的分项：'+mapping['source_id']+'/'+cell)
