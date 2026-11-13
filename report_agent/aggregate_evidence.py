"""显式坐标读取班级目标汇总：独立复算公式/缓存，不推导逐人分数。"""
import math
import re
from .common import path, digest, number


def weighted_mean(components):
    if not components:raise ValueError('缺少目标分项')
    numerator=denominator=0.0
    for c in components:
        average,maximum,weight=(number(c[k]) for k in ('average','maximum','weight'))
        if maximum<=0 or not 0<=average<=maximum or not 0<=weight<=1:
            raise ValueError('目标分项均值、满分或系数无效')
        numerator+=average*weight;denominator+=maximum*weight
    if denominator<=0:raise ValueError('目标加权满分为0')
    return numerator/denominator


def compact(s):return re.sub(r'\s+','',str(s)).replace('$','').upper()


def audit_workbook(source, config, course_name, expected_count, goal_ids):
    import openpyxl
    file=path(source['path'])
    if source.get('role')!='current':raise ValueError('班级目标汇总必须来自本轮current材料')
    if source.get('sha256') and digest(file)!=source['sha256']:raise ValueError('汇总源文件在读取后发生变化')
    if file.suffix.lower()!='.xlsx':raise ValueError('目标汇总读取器需要含公式及缓存的xlsx')
    book=openpyxl.load_workbook(file,data_only=False);values=openpyxl.load_workbook(file,data_only=True)
    try:
        ws=book[config['sheet']];cached=values[config['sheet']]
        if compact(course_name) not in compact(ws[config['course_name_cell']].value):raise ValueError('目标汇总表课程身份不符')
        count=number(cached[config['sample_count_cell']].value)
        if count!=expected_count:raise ValueError('目标汇总表样本数与本轮登记人数不一致')
        columns=config['goal_columns']
        if set(columns)!=set(goal_ids):raise ValueError('目标汇总列与本课正式目标集合不一致')
        groups=config['components'];goals={}
        for gid,col in columns.items():
            if compact(ws[f"{col}{config['header_row']}"].value)!=compact(config.get('goal_labels',{}).get(gid,'课程目标'+gid.lstrip('G'))):
                raise ValueError('目标汇总表目标列标题不符：'+gid)
            components=[];unentered=[]
            for group in groups:
                if compact(ws[f"{config['assessment_label_column']}{group['average_row']}"].value)!=compact(group['name']):
                    raise ValueError('目标汇总表考核项目/坐标发生变化：'+group['name'])
                coords={k:f'{col}{group[k+"_row"]}' for k in ('average','maximum','weight')}
                vals={k:ws[cell].value for k,cell in coords.items()}
                if all(v is None for v in vals.values()):
                    unentered.append({'assessment':group['name'],'cells':list(coords.values()),'status':'no_entry_not_confirmed_zero'});continue
                if any(v is None for v in vals.values()):raise ValueError('目标汇总分项三元组不完整：'+gid+'/'+group['name'])
                components.append({'assessment':group['name'],**vals,'cells':coords})
            mean=weighted_mean(components)
            average_cells=[f"{col}{g['average_row']}" for g in groups]
            num='+'.join(f"{col}{g['average_row']}*{col}{g['weight_row']}" for g in groups)
            den='+'.join(f"{col}{g['maximum_row']}*{col}{g['weight_row']}" for g in groups)
            expected=f'=IF(AND({",".join("ISBLANK("+c+")" for c in average_cells)}),"",({num})/({den}))'
            cell=f"{col}{config['mean_row']}";expression=ws[cell].value
            if compact(expression) not in {compact(expected),compact('=('+num+')/('+den+')')}:
                raise ValueError('目标汇总公式与声明的加权满分算法不一致：'+cell)
            cache=cached[cell].value
            if cache is None or not math.isclose(number(cache),mean,abs_tol=1e-10):raise ValueError('目标汇总缓存与独立复算不符：'+cell)
            weight_cell=f"{col}{config['goal_weight_row']}";weight=number(ws[weight_cell].value)
            if not 0<=weight<=1:raise ValueError('整体目标权重无效：'+weight_cell)
            goals[gid]={'components':components,'unentered_components':unentered,'mean':mean,'recomputed_mean':mean,
                'cached_mean':cache,'formula':expression,'formula_cell':cell,'course_goal_weight':weight,
                'result_kind':'aggregate_only','sample_count':int(count),'source_refs':[f"{source['id']}:{config['sheet']}:{cell}"],
                'scope':'按表内已填项目复核班级均值；不能推出逐人目标评分、极值或达成率'}
        if not math.isclose(sum(g['course_goal_weight'] for g in goals.values()),1,abs_tol=1e-10):raise ValueError('整体目标权重和不为1，不自动归一化')
        overall=sum(g['mean']*g['course_goal_weight'] for g in goals.values())
        num='+'.join(f"{col}{config['mean_row']}*{col}{config['goal_weight_row']}" for col in columns.values())
        weight_cells=[f"{col}{config['goal_weight_row']}" for col in columns.values()]
        expected=f'=IF(AND({",".join("ISBLANK("+c+")" for c in weight_cells)}),"",{num})'
        overall_cell=config['overall_cell']
        if compact(ws[overall_cell].value) not in {compact(expected),compact('='+num)}:raise ValueError('整体目标公式与声明的算法不一致')
        if cached[overall_cell].value is None or not math.isclose(number(cached[overall_cell].value),overall,abs_tol=1e-10):raise ValueError('整体目标缓存与独立复算不符')
        prose=str(ws[config['analysis_cell']].value or '') if config.get('analysis_cell') else ''
        discrepancies=[]
        overall_text=re.search(r'整体目标达成度[^0-9]*(0\.\d+)',prose)
        if overall_text and not math.isclose(float(overall_text[1]),overall,abs_tol=.001):
            discrepancies.append({'metric':'overall','written':float(overall_text[1]),'recomputed':overall})
        for match in re.finditer(r'目标(\d+)[^：:。\d]*[：:]\s*(0\.\d+)',prose):
            gid='G'+match[1]
            if gid in goals and not math.isclose(float(match[2]),goals[gid]['mean'],abs_tol=.001):
                discrepancies.append({'metric':gid,'written':float(match[2]),'recomputed':goals[gid]['mean']})
        return {'status':'formula_and_cache_verified','source_id':source['id'],'source_sha256':digest(file),
            'sheet':config['sheet'],'config_id':config['id'],'goals':goals,'overall':overall,'overall_cell':overall_cell,
            'analysis_cell':config.get('analysis_cell'),'analysis_text':prose,'text_discrepancies':discrepancies,
            'prose_conflicts_with_recalculation':bool(discrepancies),'individual_scores_available':False}
    finally:book.close();values.close()


def audit_sources(case,sources,facts,grades):
    audits=[];combined={}
    for config in case.get('evaluation_workbooks',[]):
        source=next((s for s in sources if s['id']==config['source_id']),None)
        if source is None:raise ValueError('目标汇总配置指向未知来源')
        result=audit_workbook(source,config,case['course_name'],grades['registered_count'],[g['id'] for g in facts['goals']])
        if set(combined)&set(result['goals']):raise ValueError('多份汇总表覆盖同一目标，需明确执行版本')
        combined.update(result['goals']);audits.append(result)
    return {'goals':combined,'audits':audits,'scope':'班级汇总与逐人实际评分独立保存'}
