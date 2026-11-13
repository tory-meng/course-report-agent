"""固定彩色柱状图、饼图、个体散点图；每张图保存输入与计算来源。"""
from pathlib import Path
from .common import save, digest
from .report_scope import assessments
from .course_presentation import enabled as course_style,human,chart_analysis

COLORS=['#60A561','#4676C1','#F4BD50','#EF8850','#DC5B64']
BINS=['优秀','良好','中等','及格','不及格']

def jobs(facts,calc):
    result=[];total=calc['total'];name=facts['metadata']['course_name']
    def add(id,kind,caption,series,source_refs,lead_in):
        estimate=bool(calc.get('evaluation_context')) and id not in ('distribution','pie','score_scatter','assessment_raw_summary')
        result.append({'id':id,'kind':kind,'caption':f'《{name}》'+caption+('（代理估计）' if estimate else ''),
                       'lead_in':lead_in.rstrip('。')+'。',
                       'series':series,'source_refs':source_refs,'snapshot_sha256':calc['snapshot_sha256'],
                       'evidence_status':'proxy_estimate' if estimate else ('aggregate_source' if kind=='aggregate_summary' else 'current_evidence')})
    refs=total['source_refs'];add('distribution','distribution','最终成绩分布柱状图',total,refs,'下图展示最终成绩各区间的人数与占比')
    add('pie','pie','最终成绩分布饼图',total,refs,'下图展示最终成绩各区间的人数占比')
    add('score_scatter','score_scatter','学生个体最终成绩散点图',total,refs,'下图展示每位学生的正式登记最终成绩，纵轴单位为分')
    raw=[{'id':a['name'],**calc['assessments'][a['id']]} for a in assessments(facts) if a.get('raw_item_id')]
    if raw:add('assessment_raw_summary','raw_summary','原始分项成绩比较图',raw,sorted({r for row in raw for r in row['source_refs']}),'下图比较各原始分项成绩的最低分、平均分和最高分，不表示课程目标达成度')
    for a in assessments(facts):
        rows=[{'id':r['point_id'],**r} for r in calc['assessment_points'].values() if r['assessment_id']==a['id']]
        add('assessment_'+a['id'],'summary',a['name']+'支撑观测点的达成度对比图',rows,a['source_refs'],
            '下图比较'+a['name']+'所支撑观测点的最低、平均和最高达成度')
        for r in rows:add('assessment_'+a['id']+'_'+r['id'],'scatter',a['name']+'支撑观测点'+r['id']+'个体达成度散点图',r,r['source_refs'],
                         '下图展示'+a['name']+'支撑观测点'+r['id']+'的个体达成度')
    aggregate=calc.get('aggregate_goals',{})
    aggregate_only=bool(aggregate) and all(calc['goals'][g['id']]['mean'] is None and g['id'] in aggregate for g in facts.get('goals',[]))
    if aggregate_only:
        add('goals','aggregate_summary','课程目标班级平均达成度图',[{'id':g['id'],'mean':aggregate[g['id']]['mean']} for g in facts['goals']],
            sorted({r for g in aggregate.values() for r in g['source_refs']}),'下图仅展示本轮评价表公式复核的课程目标班级平均达成度')
    else:add('goals','goal_summary','课程目标达成度对比图',[{'id':g['id'],**calc['goals'][g['id']]} for g in facts.get('goals',[])],refs,
             '下图比较各课程目标的达成标准、最低达成度、平均达成度和最高达成度')
    for g in facts.get('goals',[]):add('goal_'+g['id'],'scatter','课程目标'+g['id']+'个体达成度散点图',calc['goals'][g['id']],g['source_refs'],
                                     '下图展示课程目标'+g['id']+'的个体达成度')
    add('points','summary','观测点达成度对比图',[{'id':p['id'],**calc['points'][p['id']]} for p in facts.get('points',[])],refs,
        '下图比较各观测点的最低、平均和最高达成度')
    for p in facts.get('points',[]):add('point_'+p['id'],'scatter','观测点'+p['id']+'个体达成度散点图',calc['points'][p['id']],p['source_refs'],
                                      '下图展示观测点'+p['id']+'的个体达成度')
    if course_style(facts):
        for job in result:
            job['caption']=human(job['caption'].replace('（代理估计）',''))
            job['lead_in']=human(job['lead_in'])
            job['presentation_style']='course_report'
            job['student_labels']=calc.get('student_labels',{})
            job['analysis']=chart_analysis(job)
    return result

def validate_captions(paragraphs,manifest):
    """Word图题与绘图清单共用同一个名称；不以文件存在代替名称一致性检查。"""
    checks=[]
    for job in manifest:
        if job['status']!='filled':continue
        count=paragraphs.count(job['caption'])
        if count!=1:raise ValueError('图题缺失、重复或与清单不一致：'+job['id'])
        checks.append({'id':job['id'],'caption':job['caption'],'occurrences':count})
    return {'status':'passed','checked':len(checks),'charts':checks}

def render(job,out,font_path=None):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    if font_path:
        font_manager.fontManager.addfont(str(font_path))
        name=font_manager.FontProperties(fname=str(font_path)).get_name()
    else:
        names={f.name for f in font_manager.fontManager.ttflist}
        name=next((n for n in ['Noto Sans CJK SC','Noto Serif CJK SC','Microsoft YaHei','SimHei','WenQuanYi Zen Hei'] if n in names),None)
        if not name:raise RuntimeError('缺少中文绘图字体，安装fonts-noto-cjk或在runtime指定chart_font_path')
    plt.rcParams.update({'font.family':name,'axes.unicode_minus':False,'font.size':10})
    kind=job['kind'];s=job['series']
    if kind in ('summary','goal_summary','aggregate_summary','raw_summary'):
        if not s or any(r.get('mean') is None for r in s):return {'status':'missing_data','reason':'缺少部分实体的有效统计，未绘制不完整汇总图'}
    elif not s.get('valid_count'):return {'status':'missing_data','reason':'缺少可计算的个体评分或正式计算规则'}
    height_mm=35 if kind in ('distribution','pie') else (38 if kind=='goal_summary' else 39)
    clean=job.get('presentation_style')=='course_report'
    if job.get('evidence_status')=='proxy_estimate' and not clean:height_mm+=6
    fig,ax=plt.subplots(figsize=(8,8*height_mm/146),layout='constrained')
    try:
        if job.get('evidence_status')=='proxy_estimate' and not clean:fig.suptitle('方案试算：代理估计，待核验',fontsize=9,color='#895719')
        if kind=='distribution':
            counts=s['counts'];n=sum(counts)
            bars=ax.bar(BINS,[100*x/n for x in counts],color=COLORS)
            ax.bar_label(bars,labels=[f'{x}人\n{x/n:.1%}' for x in counts],padding=3)
            ax.set_ylim(0, max(25,max(counts)/n*125));ax.set_ylabel('占比（%）')
        elif kind=='pie':
            valid=[(l,v,c) for l,v,c in zip(BINS,s['counts'],COLORS) if v]
            ax.pie([r[1] for r in valid],labels=[r[0] for r in valid],colors=[r[2] for r in valid],autopct='%1.1f%%')
        elif kind in ('scatter','score_scatter'):
            data=s['values'];xs=list(range(1,len(data)+1));ys=list(data.values());th=s.get('threshold')
            if th is None:ax.scatter(xs,ys,color=COLORS[1],s=24)
            else:
                for passed,color,label in [(True,COLORS[1],'达到标准'),(False,COLORS[4],'未达标准')]:
                    chosen=[(x,y) for x,y in zip(xs,ys) if (y>=th)==passed]
                    if chosen:ax.scatter([v[0] for v in chosen],[v[1] for v in chosen],color=color,s=24,label=label)
                ax.axhline(th,color=COLORS[0],linestyle='--',label=(f'分数标准 {th:g}' if kind=='score_scatter' else f'达成标准 {th:g}'));ax.legend(fontsize=8)
            upper=s.get('max_score',1) if kind=='score_scatter' else 1
            ax.set_ylim(-upper*.03,upper*1.06);ax.set_ylabel('最终成绩（分）' if kind=='score_scatter' else '达成度')
            every=max(1,len(xs)//8);ix=list(range(0,len(xs),every));ax.set_xticks([xs[i] for i in ix],[job.get('student_labels',{}).get(list(data)[i],list(data)[i]) for i in ix]);ax.set_xlabel('学生姓名' if clean else '匿名学生序号')
        else:
            xs=list(range(len(s)));fields=[('min','最低达成度',COLORS[4]),('mean','平均达成度',COLORS[0]),('max','最高达成度',COLORS[1])]
            if kind=='raw_summary':fields=[('min','最低分',COLORS[4]),('mean','平均分',COLORS[0]),('max','最高分',COLORS[1])]
            if kind=='goal_summary':fields=[('threshold','达成标准',COLORS[2])]+fields
            if kind=='aggregate_summary':fields=[('mean','班级平均',COLORS[0])]
            width=.8/len(fields)
            for j,(key,label,color) in enumerate(fields):
                good=[(i,r[key]) for i,r in enumerate(s) if r.get(key) is not None]
                ax.bar([i-.4+width*(j+.5) for i,_ in good],[v for _,v in good],width=width,color=color,label=label)
            ax.set_xticks(xs,[human(r['id']) if clean else r['id'] for r in s]);ax.set_ylim(0,1.15);ax.set_ylabel('达成度');ax.legend(ncols=len(fields),fontsize=8)
            if kind=='raw_summary':
                ax.set_ylim(min(0,min(r['min'] for r in s))*1.05,max(1,max(r['max'] for r in s))*1.15);ax.set_ylabel('原始登记分值')
        if kind!='pie':
            ax.spines[['top','right']].set_visible(False);ax.grid(axis='y',alpha=.18);ax.set_axisbelow(True)
        out=Path(out);out.parent.mkdir(parents=True,exist_ok=True);fig.savefig(out,dpi=220,facecolor='white')
        return {'status':'filled','path':str(out),'sha256':digest(out)}
    finally:plt.close(fig)

def generate(facts,calc,run,font_path=None,progress=None):
    manifest=[];planned=jobs(facts,calc)
    for i,job in enumerate(planned):
        if progress:progress.units(i,len(planned),'绘图 '+job['id'])
        save(run/'chart_data'/(job['id']+'.json'),job)
        result=render(job,run/'figures'/(job['id']+'.png'),font_path)
        manifest.append({**job,**result})
        if progress:progress.units(i+1,len(planned),'图表处理完成：'+result['status'])
    save(run/'chart_manifest.json',manifest);return manifest
