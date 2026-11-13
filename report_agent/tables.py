"""按确认母版的11种表型生成数据与合并关系；同类目标矩阵保持单表。"""
from .common import number
from .report_scope import assessments,method_label,method_support

def show(v,percentage=False):
    if v is None:return '—'
    if isinstance(v,(float,int)):
        return f'{v:.1%}' if percentage else (str(int(v)) if float(v).is_integer() else f'{v:.3f}'.rstrip('0').rstrip('.'))
    return str(v)

def total(values):
    return sum(values) if values and all(isinstance(v,(int,float)) for v in values) else None

def assessment_summaries(facts,calc):
    """5.3.2每个考核方式的同源数值支撑表，独立于母版11类必选表型。"""
    result=[]
    for n,assessment in enumerate(assessments(facts),1):
        rows=[['观测点','有效人数','达成标准','平均达成度','最低达成度','最高达成度','达成率']]
        for point in facts.get('points',[]):
            row=next((r for r in calc['assessment_points'].values() if r['assessment_id']==assessment['id'] and r['point_id']==point['id']),None)
            if row:rows.append([point['id'],row['valid_count'],row['threshold'],row['mean'],row['min'],row['max'],show(row['rate'],True)])
        if len(rows)==1:continue
        result.append({'id':'T5-2A'+str(n),'assessment_id':assessment['id'],'rows':[[show(v) for v in row] for row in rows],
                       'merges':[],'widths_mm':[22,16,18,23,22,22,23],'header_rows':1,
                       'caption':'表5-2A'+str(n)+' '+assessment['name']+'支撑观测点的统计信息',
                       'note':'按候选规则试算，表内比例不代表正式能力达成认定。' if calc.get('evaluation_context') else '人数及达成率按本方式—观测点的有效评分统计。'})
    from .course_presentation import enabled
    if enabled(facts):
        for t in result:t['note']='人数及达成率按本考核方式支撑观测点的有效评分统计。'
    return result

def missing_inputs(f):
    """固定表型存在不代表内容完整；未知矩阵项不能当作零。"""
    missing=[];goals=[g['id'] for g in f.get('goals',[])]
    matrix={(r['assessment_id'],r['goal_id']):r.get('weight') for r in f.get('assessment_goal_weights',[])}
    active=assessments(f,True);ids={a['id'] for a in active}
    for a in active:
        if a.get('total_weight') is None:missing.append(a['id']+'缺少总评构成权重')
        for g in goals:
            if matrix.get((a['id'],g)) is None:missing.append(a['id']+'/'+g+'缺少方式到目标权重；不支持关系也须有依据地记0')
    for a in assessments(f):
        if not any(i['assessment_id']==a['id'] for i in f.get('items',[])):
            missing.append(a['id']+'缺少已确认的考核项、满分及成绩映射')
    for item in (i for i in f.get('items',[]) if i['assessment_id'] in ids and not i.get('summary_only')):
        for key in ['max_score','converted_max','score_basis']:
            if item.get(key) is None:missing.append(item['id']+'缺少表格字段'+key)
        for g in goals:
            if item.get('goal_weights',{}).get(g) is None:missing.append(item['id']+'/'+g+'缺少考核项到目标权重')
    for p in f.get('points',[]):
        if p.get('course_weight') is None:missing.append(p['id']+'缺少课程贡献权重')
    return missing

def build(f,c,contract):
    base={t['id']:t for t in contract['tables']}; result={}; gs=f.get('goals',[]);ps=f.get('points',[]);ms=f.get('methods',[]);ass=f.get('assessments',[]);items=f.get('items',[])
    primary=assessments(f);ass=assessments(f,True);ids={a['id'] for a in ass};items=[i for i in items if i['assessment_id'] in ids and not i.get('summary_only')]
    an={a['id']:a['name'] for a in ass};gn={g['id']:g.get('text','') for g in gs}
    matrix={(r['assessment_id'],r['goal_id']):r.get('weight') for r in f.get('assessment_goal_weights',[])}
    def add(id,rows,merges=None,widths=None,header=None,note=''):
        b=base[id];result.setdefault(id,[]).append({'id':id,'rows':[[show(x) for x in row] for row in rows],
          'merges':merges or [],'widths_mm':widths or b['widths_mm'],'header_rows':header or b['header_rows'],'note':note})
    add('T2-1',[base['T2-1']['rows'][0]]+[[p.get('requirement'),p['id'],p.get('text'),p.get('course_weight')] for p in ps])
    rows=[base['T2-2']['rows'][0]];merges=[]
    for p in ps:
        links=list(p.get('goal_weights',{}).items()) or [(None,None)];start=len(rows)
        for gid,w in links:rows.append([p['id'],p.get('text'),gid,w])
        if len(links)>1:merges.extend([[start,col,len(rows)-1,col] for col in (0,1)])
    add('T2-2',rows,merges)
    groups=[gs]
    def point_names(g):return '、'.join(p['id'] for p in ps if g['id'] in p.get('goal_weights',{})) or '—'
    for group in groups:
        n=len(group) or 1; group=group or [{'id':'待补目标'}];note=''
        rows=[['毕业要求观测点']+[point_names(g) for g in group],['教学方式']+[g['id'] for g in group]]
        rows += [[method_label(f,m)]+[method_support(f,m,g['id']) for g in group] for m in ms]
        teaching_note=f.get('teaching_plan',{}).get('note','')
        add('T2-3',rows,widths=[34]+[112/n]*n,note=teaching_note)
        rows=[['考核方式','考核项']+[g['id'] for g in group]+['∑']];merges=[]
        for a in ass:
            start=len(rows)
            for it in items:
                if it['assessment_id']!=a['id']:continue
                ws=it.get('goal_weights',{})
                rows.append([a['name'],it['name']]+[ws.get(g['id']) for g in group]+[total([ws.get(g['id']) for g in group])])
            if len(rows)>start+1:merges.append([start,0,len(rows)-1,0])
            if len(rows)==start and a in primary:rows.append([a['name'],'待核对']+[None for g in group]+[None])
        add('T3-1',rows,merges,[22,22]+[88/n]*n+[14],note=note)
        rows=[['毕业要求观测点','']+[point_names(g) for g in group]+['∑'],['考核方式','课程目标']+[g['id'] for g in group]+['']]
        merges=[[0,0,0,1],[0,n+2,1,n+2]]
        for a in ass:
            idx=len(rows);rows.append([a['name'],'']+[matrix.get((a['id'],g['id'])) for g in group]+[total([matrix.get((a['id'],g['id'])) for g in group])]);merges.append([idx,0,idx,1])
        idx=len(rows);rows.append(['∑','']+[total([matrix.get((a['id'],g['id'])) for a in ass]) for g in group]+[total([matrix.get((a['id'],g['id'])) for a in ass for g in group])]);merges.append([idx,0,idx,1])
        add('T3-2',rows,merges,[22,22]+[88/n]*n+[14],note=note)
        weights=[]
        for g in group:
            links=[p for p in ps if g['id'] in p.get('goal_weights',{})]
            weights.append('；'.join(p['id']+':'+show(p.get('course_weight')) for p in links) or '—')
        rows=[['课程',f['metadata']['course_name']]+['']*(n+1),['毕业要求','毕业要求观测点']+[point_names(g) for g in group]+['∑'],
              ['','课程对观测点的权重系数']+weights+['—'],['教学及考核环节','','教学及考核环节对课程目标&观测点的权重系数']+['']*(n-1)+['∑'],
              ['方式','权重']+[g['id'] for g in group]+['']]
        merges=[[0,1,0,n+2],[1,0,2,0],[3,0,3,1],[3,2,3,n+1],[3,n+2,4,n+2]]
        for m in ms:
            idx=len(rows);rows.append([method_label(f,m),'']+[method_support(f,m,g['id']) for g in group]+['/']);merges.append([idx,0,idx,1])
        for a in ass:rows.append([a['name'],a.get('total_weight')]+[matrix.get((a['id'],g['id'])) for g in group]+[total([matrix.get((a['id'],g['id'])) for g in group])])
        rows.append(['∑',total([a.get('total_weight') for a in primary])]+[total([matrix.get((a['id'],g['id'])) for a in ass]) for g in group]+[total([matrix.get((a['id'],g['id'])) for a in ass for g in group])])
        widths=[22,26]+[86/n]*n+[12]
        add('T4-1',rows,merges,widths,note='；'.join(x for x in (note,teaching_note) if x))
    rows=[base['T2-4']['rows'][0]];merges=[]
    for a in primary:
        start=len(rows)
        for it in items:
            if it['assessment_id']==a['id']:rows.append([len(rows),a['name'],a.get('total_weight'),it['name'],it.get('max_score'),it.get('converted_max')])
        if len(rows)>start+1:merges.extend([[start,k,len(rows)-1,k] for k in (1,2)])
        if len(rows)==start:rows.append([len(rows),a['name'],a.get('total_weight'),'待核对',None,None])
    add('T2-4',rows,merges)
    n=len(primary) or 1;ass_display=primary or [{'id':'missing','name':'待补考核方式'}]
    rows=[['统计维度','人数','各考核方式成绩']+['']*(n-1)+['最终成绩','','',''],['','']+[a['name'] for a in ass_display]+['平均值','中位数','最低分','最高分'],
          ['本轮有效样本',c['total']['valid_count']]+[c['assessments'].get(a['id'],{}).get('mean') for a in ass_display]+[c['total'][k] for k in ['mean','median','min','max']]]
    merges=[[0,0,1,0],[0,1,1,1],[0,2,0,1+n],[0,2+n,0,5+n]]
    original=base['T5-1']['widths_mm'];widths=original if n==2 else [20,13]+[45/n]*n+[17]*4
    note='各分项列为原始登记分均值；总评列为正式总评均值，逐生取整与均值后取整不等价。' if any(a.get('raw_item_id') for a in primary) else ''
    add('T5-1',rows,merges,widths,note=note)
    counts=c['total']['counts'];valid=c['total']['valid_count'];labels=['优秀（90≤x≤100）','良好（80≤x<90）','中等（70≤x<80）','及格（60≤x<70）','不及格（0≤x<60）']
    add('T5-2',[base['T5-2']['rows'][0]]+[[l,v,show(v/valid,True) if valid else '—'] for l,v in zip(labels,counts)])
    rows=[base['T5-3']['rows'][0]];merges=[]
    for g in gs:
        info=c['goals'][g['id']];links=[r for r in f.get('assessment_goal_weights',[]) if r['goal_id']==g['id']] or [{}];start=len(rows)
        aggregate=c.get('aggregate_goals',{}).get(g['id'],{})
        mean=info['mean'] if info['mean'] is not None else aggregate.get('mean')
        count=str(c['registered_count'])+'/'+str(info['valid_count']) if info['valid_count'] and info['valid_count']!=c['registered_count'] else c['registered_count']
        for link in links:rows.append([g['id'],count,an.get(link.get('assessment_id'),'—'),link.get('weight'),g.get('threshold'),mean,info['min'],info['max'],show(info['rate'],True)])
        if len(links)>1:merges.extend([[start,k,len(rows)-1,k] for k in [0,1,4,5,6,7,8]])
    aggregate_only=any(c['goals'][g['id']]['mean'] is None and g['id'] in c.get('aggregate_goals',{}) for g in gs)
    add('T5-3',rows,merges,note='班级平均值来自本轮评价表公式复算；缺少逐人目标评分，个体极值及达成率保留缺项。' if aggregate_only else '')
    if any(v['valid_count'] and v['valid_count']!=c['registered_count'] for v in c['goals'].values()):
        result['T5-3'][0]['note']+='人数以“登记/有效”显示；达成率分母为有效评分人数，未评分记录不补零。'
    rows=[base['T5-4']['rows'][0]];merges=[]
    for p in ps:
        info=c['points'][p['id']];links=list(p.get('goal_weights',{}).items()) or [(None,None)];start=len(rows)
        for gid,w in links:rows.append([p['id'],gid,w,info['mean'],p.get('course_weight'),info['contribution']])
        if len(links)>1:merges.extend([[start,k,len(rows)-1,k] for k in [0,3,4,5]])
    add('T5-4',rows,merges)
    if c.get('evaluation_context'):
        result['T2-1'][0]['note']='课程支撑权重为本次候选参数，非大纲已确认数值；不将支撑等级直接转换为权重。'
        result['T5-3'][0]['note']+='所列达成率仅为候选阈值下的试算比例，不表示对应能力已获正式认定。'
        result['T5-4'][0]['note']+='本表仅列本课支撑值；同源目标映射不构成独立测量，也不代表专业毕业要求整体达成。'
        for table in result['T3-2']:
            table['note']+=('；' if table['note'] else '')+'各目标列按其加权满分贡献归一化；不同于总评构成占比，不再次乘总评权重。'
    for entries in result.values():
        for t in entries:
            if len(t['rows'])==t['header_rows']:t['rows'].append(['—']*len(t['widths_mm']))
            if c.get('evaluation_context') and t['id'] in ('T2-2','T2-4','T3-1','T3-2','T4-1','T5-3','T5-4'):
                t['note']='评价方案试算：候选对应及换算含代理估计，待核验。'+t['note']
    from .course_presentation import enabled,human
    if enabled(f):
        notes={'T2-1':'课程支撑权重见表，按本课观测点分别计算贡献值。',
               'T2-3':'教学方式与课程目标的对应关系。',
               'T3-1':'各考核项在不同课程目标间分配；本表∑为显示列之和。',
               'T3-2':'各目标列按加权满分贡献归一化，不再重复乘总评权重；本表∑为显示列之和。',
               'T4-1':'教学方式与目标评价的对应安排。',
               'T5-3':'人数以登记/有效口径列示；达成率以有效评分人数为分母，未评分不补零。',
               'T5-4':'本表列示本课程对观测点的支撑贡献。'}
        for key,entries in result.items():
            for t in entries:
                t['rows']=[[human(v) if isinstance(v,str) else v for v in row] for row in t['rows']]
                t['note']=notes.get(key,'' if key in ('T2-2','T2-4') else t['note'])
    return result
