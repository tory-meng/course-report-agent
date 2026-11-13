"""由同源计算生成具体核验信息；不从低代理分反推确定的能力缺陷。"""
def below_details(row):
    threshold=row.get('threshold')
    if threshold is None:return []
    return [{'student_id':sid,'value':value,'threshold':threshold,'gap':threshold-value}
            for sid,value in row.get('values',{}).items() if value<threshold]

def build(calc):
    proxy=bool(calc.get('evaluation_context'));goals={}
    for gid,row in calc.get('goals',{}).items():
        source=calc.get('aggregate_goals',{}).get(gid,{})
        goals[gid]={'mean':row.get('mean'),'threshold':row.get('threshold'),'below':below_details(row),
                    'source_class_mean':source.get('mean'),
                    'difference':row['mean']-source['mean'] if row.get('mean') is not None and source.get('mean') is not None else None,
                    'evidence_status':'proxy_estimate' if proxy else 'current_evidence'}
    return {'goals':goals,'contains_estimates':proxy,
            'scope':'低于阈值是数值事实或显式代理试算，不单独证明具体知识薄弱、教学原因或改进效果'}

def readable_issues(calc):
    result=[]
    if any(a.get('text_discrepancies') for a in calc.get('aggregate_audits',[])):
        result.append(['评价表结论','材料文字与按公式重算结果不一致','两种口径须核对','由任课教师核对源表公式与文字结论'])
    if calc.get('evaluation_context'):
        result.append(['评价方案','目标及观测点个体值含代理估计','不能视为真实目标细评分','核对名称对应、目标分配、阈值与估计假设'])
    for gid,row in calc.get('goals',{}).items():
        low=below_details(row)
        if low:result.append([gid,('候选试算：' if calc.get('evaluation_context') else '')+str(len(low))+'条低于阈值'+str(row['threshold'])+'的记录',
                              '先核验评分和数据状态，再判断具体原因','结合原始评分与教学活动复核，不直接归因'])
    return result[:6]
