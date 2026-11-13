"""读取记分册的真实公式，独立复核缓存；不按拟合猜制度、不执行任意公式。"""
import ast
import copy
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from .common import save,signature


def expression_tree(formula):
    if not isinstance(formula,str) or not formula.startswith('='):raise ValueError('缺少Excel公式')
    return ast.parse(formula[1:].replace('$',''),mode='eval').body


def evaluate(node,cell):
    if isinstance(node,ast.Constant) and type(node.value) in (int,float):return Decimal(str(node.value))
    if isinstance(node,ast.Name) and re.fullmatch(r'[A-Z]+[1-9]\d*',node.id):return Decimal(str(cell(node.id)))
    if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.USub):return -evaluate(node.operand,cell)
    if isinstance(node,ast.BinOp):
        a,b=evaluate(node.left,cell),evaluate(node.right,cell)
        if isinstance(node.op,ast.Add):return a+b
        if isinstance(node.op,ast.Sub):return a-b
        if isinstance(node.op,ast.Mult):return a*b
        if isinstance(node.op,ast.Div):return a/b
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='ROUND' and not node.keywords and len(node.args)==2:
        digits=evaluate(node.args[1],cell)
        if digits!=digits.to_integral_value() or not -6<=digits<=6:raise ValueError('ROUND精度不支持')
        return evaluate(node.args[0],cell).quantize(Decimal(1).scaleb(-int(digits)),rounding=ROUND_HALF_UP)
    raise ValueError('未支持或不安全的公式语法')


def linear_terms(formula,row):
    node=expression_tree(formula);rounding=None
    if isinstance(node,ast.Call) and isinstance(node.func,ast.Name) and node.func.id=='ROUND' and len(node.args)==2:
        rounding=int(evaluate(node.args[1],lambda _:None));node=node.args[0]
    def walk(n):
        if isinstance(n,ast.Name):
            m=re.fullmatch(r'([A-Z]+)(\d+)',n.id)
            if not m or int(m[2])!=row:raise ValueError('只接受同一学生行的直接引用')
            return {m[1]:Decimal(1)},Decimal(0)
        if isinstance(n,ast.Constant) and type(n.value) in (int,float):return {},Decimal(str(n.value))
        if isinstance(n,ast.BinOp):
            left,lc=walk(n.left);right,rc=walk(n.right)
            if isinstance(n.op,(ast.Add,ast.Sub)):
                factor=-1 if isinstance(n.op,ast.Sub) else 1;out=dict(left)
                for k,v in right.items():out[k]=out.get(k,Decimal(0))+factor*v
                return out,lc+factor*rc
            if isinstance(n.op,ast.Mult) and (not left or not right):
                values,offset,factor=(right,rc,lc) if not left else (left,lc,rc)
                return {k:v*factor for k,v in values.items()},offset*factor
            if isinstance(n.op,ast.Div) and not right and rc:return {k:v/rc for k,v in left.items()},lc/rc
        raise ValueError('不是直接评分列的线性组合')
    weights,constant=walk(node)
    if constant or not weights or any(w<=0 for w in weights.values()) or sum(weights.values())!=1:raise ValueError('不是权重和为1的评分公式')
    return {k:float(v) for k,v in weights.items()},rounding


def audit(case,sources,out):
    """仅审计有学号的current成绩表。XLS转换副本只读公式，原缓存分数不替换。"""
    import openpyxl
    from .grade_layout import layouts,label,is_student_id
    results=[]
    for source in sources:
        if source.get('role')!='current' or source.get('kind')!='grades':continue
        candidate_sheets=[]
        for sheet in source.get('sheets',[]):
            for layout in layouts(sheet):
                total_cols=[c['column'] for c in layout['columns'] if c['role']=='total']
                if not total_cols:total_cols=[c['column'] for c in layout['columns'] if c['role']=='generic_score']
                if total_cols:
                    headers=[c['label'] for c in layout['columns']]
                    candidate_sheets.append((sheet,layout['header_row'],headers,layout,total_cols))
        if not candidate_sheets:continue
        path=Path(source['path']);formula_file=path;conversion=None
        if path.suffix.lower()=='.xls':
            folder=Path(out)/'formula_source';folder.mkdir(parents=True,exist_ok=True)
            exe=shutil.which('libreoffice') or shutil.which('soffice')
            if not exe:raise RuntimeError('核验XLS原始公式需要LibreOffice，不以成绩拟合替代')
            with tempfile.TemporaryDirectory(prefix='grade_formula_') as profile:
                result=subprocess.run([exe,'-env:UserInstallation='+Path(profile).as_uri(),'--headless','--convert-to','xlsx','--outdir',str(folder),str(path)],capture_output=True,text=True,timeout=180)
            formula_file=folder/(path.stem+'.xlsx');conversion='libreoffice_formula_copy'
            if result.returncode or not formula_file.exists():raise RuntimeError('XLS公式副本读取失败：'+result.stderr[-500:])
        if formula_file.suffix.lower()!='.xlsx':continue
        wb=openpyxl.load_workbook(formula_file,data_only=False)
        for sheet,header,headers,layout,cols in candidate_sheets:
            ws=wb[sheet['name']];idcol=layout['student_id']-1
            rows=[(n,row) for n,row in enumerate(sheet['rows'][header:],header+1) if len(row)>idcol and is_student_id(row[idcol],case.get('student_id_pattern',r'[A-Za-z0-9_-]+'))]
            def original(coord):
                col,row=openpyxl.utils.cell.coordinate_from_string(coord)
                return sheet['rows'][row-1][openpyxl.utils.column_index_from_string(col)-1]
            eligible=[col for col in cols if rows and all(len(row)>=col and type(row[col-1]) in (int,float) for _,row in rows)]
            integers=[col for col in eligible if all(float(row[col-1]).is_integer() for _,row in rows)]
            fractional=[col for col in eligible if any(not float(row[col-1]).is_integer() for _,row in rows)]
            if len(cols)==1:col=cols[0]
            elif len(integers)==1 and len(fractional)==1:col=integers[0]
            else:
                results.append({'source_id':source['id'],'sheet':sheet['name'],'status':'not_inferred','reason':'多个总评列不能唯一确认'});continue
            patterns=[];verified=[]
            try:
                for n,row in rows:
                    formula=ws.cell(n,col).value;weights,rounding=linear_terms(formula,n)
                    if abs(evaluate(expression_tree(formula),original)-Decimal(str(row[col-1])))>Decimal('0.0000001'):raise ValueError('公式与原始缓存不一致')
                    # 汇总列不可作为独立原始考核项再次相加。
                    if any(ws[f'{letter}{n}'].data_type=='f' for letter in weights):raise ValueError('总评含中间公式，须保留完整层级后再处理')
                    patterns.append((weights,rounding));verified.append({'cell':ws.cell(n,col).coordinate,'formula':formula,'registered':row[col-1]})
                if any(p!=patterns[0] for p in patterns):raise ValueError('同一班级公式不一致')
            except (ValueError,TypeError,IndexError,ArithmeticError) as exc:
                results.append({'source_id':source['id'],'sheet':sheet['name'],'status':'not_inferred','reason':str(exc)});continue
            weights,rounding=patterns[0];components=[]
            for letter,weight in weights.items():
                number=openpyxl.utils.column_index_from_string(letter)
                column=layout['columns'][number-1]
                if column['score_basis']=='converted':
                    components=[];break
                names=[v for v in column['header_path'] if label(v) not in ('成绩构成','原始分','原始成绩','未折算原始分','未折算','百分制','分值','得分')]
                components.append({'name':label(names[-1]) if names else headers[number-1],'weight':weight,'column':number,'letter':letter})
            if not components:
                results.append({'source_id':source['id'],'sheet':sheet['name'],'status':'not_inferred','reason':'分项是折算分，不使用原始分权重算法'});continue
            proof_ids=[]
            for target in range(idcol+2,max(cols)+1):
                first=ws.cell(rows[0][0],target)
                if first.data_type!='f':continue
                checked=[]
                for n,row in rows:
                    f=ws.cell(n,target).value
                    if not isinstance(f,str) or not f.startswith('='):continue
                    try:matched=abs(evaluate(expression_tree(f),original)-Decimal(str(row[target-1])))<Decimal('0.0000001')
                    except (ValueError,TypeError,IndexError,ArithmeticError):matched=False
                    checked.append({'cell':ws.cell(n,target).coordinate,'formula':f,'matched_original_cache':matched})
                ref=f'{source["id"]}:{sheet["name"]}:{first.coordinate}:formula';text=first.coordinate+' '+first.value
                if not any(b['id']==ref for b in source['blocks']):source['blocks'].append({'id':ref,'text':text,'source_id':source['id'],'role':'current','evidence_kind':'formula_recovered_from_original','conversion':conversion})
                proof_ids.append(ref)
                save(Path(out)/f'formula_{source["id"]}_{first.column_letter}.json',{'source_sha256':source['sha256'],'rows':checked})
            average_matches=sum(int((sum(Decimal(str(row[c-1])) for c in range(min(cols)-4,min(cols)))/4).quantize(Decimal(1),rounding=ROUND_HALF_UP))==row[col-1] for _,row in rows) if len(components)==3 and min(cols)>=5 else None
            result={'status':'verified','source_id':source['id'],'source_sha256':source['sha256'],'sheet':sheet['name'],'header_row':header,'student_id_column':idcol+1,'name_column':next((c['column'] for c in layout['columns'] if c['role']=='name'),None),'total_column':col,'decimal_total_column':fractional[0] if fractional and fractional[0]!=col else None,'student_rows':len(rows),'verified_rows':verified,'components':components,'round_digits':rounding,'source_refs':[f'{source["id"]}:{sheet["name"]}:R{header}',*proof_ids],'conversion':conversion,'four_column_average_matches':average_matches}
            results.append(result)
        wb.close()
    save(Path(out)/'grade_formula_audit.json',{'results':results,'policy':'只采信同口径全部学生逐行验证的真实公式；不把中间汇总列当独立考核项'})
    return results


def apply_verified(case,facts,sources,results):
    evidence={b['id']:b for s in sources for b in s['blocks']}
    for result in results:
        if result['status']!='verified':continue
        refs=result['source_refs'];quotes={ref:evidence[ref]['text'] for ref in refs}
        components=[{'name':c['name'],'weight':c['weight']} for c in result['components']]
        scheme={'id':'scheme_formula_'+signature(components)[:16],'level':'course_total','components':components,'source_refs':refs,'quotes':quotes,'origin':'verified_grade_register_formulas','children':[],'status':'candidate'}
        facts.setdefault('grading_schemes',[]).append(scheme)
        from .grading_schemes import apply_scheme
        apply_scheme(case,facts,scheme,'verified_grade_register_formulas')
        parents={a['name']:a for a in facts['assessments']};mapping={}
        for c in result['components']:
            parent=parents[c['name']];iid='I_grade_'+signature([result['source_id'],result['sheet'],c['column']])[:12]
            item={'id':iid,'name':c['name'],'assessment_id':parent['id'],'score_basis':'raw','source_refs':list(refs),'quotes':copy.deepcopy(quotes)}
            if not any(i['id']==iid for i in facts.setdefault('items',[])):facts['items'].append(item)
            parent['raw_item_id']=iid;mapping[iid]=c['column']
        case['grade_tables']=[{'source_id':result['source_id'],'sheet':result['sheet'],'header_row':result['header_row'],'student_id':result['student_id_column'],'name':result['name_column'],'total':result['total_column'],'items':mapping}]
        terms='＋'.join(c['name']+'×'+format(c['weight']*100,'g')+'%' for c in result['components'])
        rule='总评＝'+('ROUND（'+terms+'，'+str(result['round_digits'])+'）' if result['round_digits'] is not None else terms)+'。'
        facts['metadata']['grade_formula_text']=rule+'采用已逐行核验的正式登记总评列；中间汇总列不作为独立考核项重复计入。'
        facts['grade_formula_resolution']=copy.deepcopy(result)
