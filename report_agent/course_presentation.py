"""面向课程报告的展示；计算键、来源和审批状态保持独立。"""
import re
import math

def enabled(facts):
    return facts.get('report_presentation',{}).get('style')=='course_report'

def human(text):
    value=re.sub(r'(?<![A-Za-z0-9_])G(\d+)(?![A-Za-z0-9_])',r'课程目标\1',str(text))
    return re.sub(r'(?:课程|学习|教学)?目标\s*(\d+)',r'课程目标\1',value).replace('课程目标课程目标','课程目标')

def student_records(grades):
    return [{'id':s['id'],'name':s['name'],'key':s['anonymous_id'],'status':s['status'],
             'scores':dict(s['scores']),'total':s.get('total')} for s in grades['students']]

def student_score_columns(facts):
    """附表逐项展示真实字段，包含已登记的中间汇总，不假定所有课只有平时/期末。"""
    return [(i['id'],i['name']) for i in facts.get('items',[])]

def student_total_text(student,value,included):
    labels={'deferred':'缓考','absent':'缺考','exempt':'免修','missing':'缺失评分'}
    state=student.get('status','normal')
    if state in labels:
        return value+'（'+labels[state]+('，不计统计' if not included else '，按政策计入')+'）'
    return value

def student_record_pages(records,max_rows=26):
    """适度平衡附表各页，不为末尾两三名学生单独留整页。"""
    if not records:return []
    page_count=math.ceil(len(records)/max_rows)
    size=math.ceil(len(records)/page_count)
    return [(offset,records[offset:offset+size]) for offset in range(0,len(records),size)]

def chart_analysis(job):
    s=job['series'];kind=job['kind']
    if kind=='pie':
        n=sum(s['counts']);good=s['counts'][0]+s['counts'][1];passed=n-s['counts'][4]
        return f'优秀与良好成绩合计{good}人，占{good/n:.1%}；及格及以上共{passed}人，占{passed/n:.1%}。饼图体现各成绩层次的结构，后续可结合分项表现为不同层次学生安排巩固或拓展任务。'
    if kind=='distribution':
        n=sum(s['counts']);i=max(range(len(s['counts'])),key=lambda k:s['counts'][k])
        return f'有效总评成绩共{n}人，平均分为{s["mean"]:.2f}分；以'+['优秀','良好','中等','及格','不及格'][i]+f'区间人数最多，占{s["counts"][i]/n:.1%}。成绩分布反映本次考核的整体表现，后续教学应结合分项成绩关注学生差异。'
    if kind in ('scatter','score_scatter'):
        unit='分' if kind=='score_scatter' else ''
        text=f'本图包含{s["valid_count"]}名学生，最低值为{s["min"]:.3f}{unit}，平均值为{s["mean"]:.3f}{unit}，最高值为{s["max"]:.3f}{unit}。'
        th=s.get('threshold')
        if th is not None:
            below=sum(v<th for v in s['values'].values())
            margin=s['min']-th
            boundary='最低值恰好等于标准' if abs(margin)<1e-10 else f'最低值高于标准{margin:.3f}{unit}'
            text+=f'以{th:g}{unit}为标准，{below}人低于标准，需结合对应任务开展针对性辅导。' if below else f'按{th:g}{unit}标准，全部有效记录达到标准，{boundary}。'
            lowest=[job.get('student_labels',{}).get(k,k) for k,v in s['values'].items() if abs(v-s['min'])<1e-10]
            if lowest:text+='其中'+'、'.join(lowest[:2])+('等' if len(lowest)>2 else '')+'的数值较低，可结合学习记录安排巩固练习。'
        return text
    valid=[r for r in s if r.get('mean') is not None]
    if not valid:return ''
    low=min(valid,key=lambda r:r['mean']);high=max(valid,key=lambda r:r['mean'])
    if kind=='raw_summary':
        return f'各考核项平均分介于{low["mean"]:.2f}分和{high["mean"]:.2f}分之间，其中{low["id"]}平均分较低。分项保留成绩册原始分值，可据此安排训练重点；各项分值与目标达成度采用不同量纲。'
    equal=max(r['mean'] for r in valid)-min(r['mean'] for r in valid)<1e-10
    text=(f'图中各项平均达成度相同，为{low["mean"]:.3f}；' if equal else f'图中平均达成度介于{low["mean"]:.3f}和{high["mean"]:.3f}之间，{human(low["id"])}相对较低；')
    text+='结果由表中评分项和分配系数计算。'
    if equal:
        text+=('相同或近似的计分构成会产生一致曲线，不能仅据此判断不同能力完全一致。' if kind=='goal_summary' else '相关观测点采用相同的评分构成，所得数值一致，可结合具体学习任务分别记录表现。')
    else:text+='教学安排应结合对应课程内容加强薄弱环节，并通过后续分项任务观察变化。'
    return text
