"""参考方案筛选与课程评价表复核。候选规则不写回正式事实或审批状态。"""
import math
import re
from report_agent.common import number
from report_agent.reference_db import connect


from report_agent.reference_plans import normalized_title, threshold_evidence, rank_references


def rubric_goal_mean(components):
    """按当前评价表式1，不能将目标权重与总评权重混合或忽略分项满分。"""
    if not components:
        raise ValueError('缺少目标分项')
    numerator = denominator = 0.0
    for row in components:
        average, maximum, weight = (number(row[k]) for k in ('average', 'maximum', 'weight'))
        if maximum <= 0 or not 0 <= average <= maximum or not 0 <= weight <= 1:
            raise ValueError('目标分项均值、满分或系数无效')
        numerator += average * weight
        denominator += maximum * weight
    if denominator <= 0:
        raise ValueError('目标加权满分为0')
    return numerator / denominator


def uniform_goal_score(scores, allocations, weights, maxima):
    """仅供候选方案试算：分项内各目标得分率相同，不能宣称为实际目标评分。"""
    terms = []
    for item, allocation in allocations.items():
        if allocation is None:
            raise ValueError('未知分配不能默认为0')
        allocation = number(allocation)
        if allocation < 0:
            raise ValueError('分配不能为负')
        if allocation == 0:
            continue
        if item not in scores or scores[item] is None:
            raise ValueError('缺少原始分项，不能由总评分解')
        score, maximum, weight = number(scores[item]), number(maxima[item]), number(weights[item])
        if maximum <= 0 or not 0 <= score <= maximum:
            raise ValueError('分项得分超范围')
        terms.append({'average': score / maximum * allocation, 'maximum': allocation, 'weight': weight})
    return rubric_goal_mean(terms)


def audit_course_workbook(filename):
    """course_002本轮评价表的显式坐标复核；不推断为其他课程的通用表结构。"""
    import openpyxl
    ws = openpyxl.load_workbook(filename, data_only=False).active
    cached = openpyxl.load_workbook(filename, data_only=True).active
    groups = [('平时成绩', 12, 13, 14), ('验收报告', 21, 22, 23), ('期末答辩', 24, 25, 26)]
    for label, avg, maximum, weight in groups:
        if re.sub(r'\s+', '', str(ws.cell(avg, 2).value)) != label:
            raise ValueError('评价表布局改变，请重新核对坐标')
    goals = {}
    for col in range(4, 8):
        gid = 'G' + str(col - 3)
        if re.sub(r'\s+', '', str(ws.cell(11, col).value)) != '课程目标' + str(col - 3):
            raise ValueError('评价表目标列改变')
        rows = []
        blanks = []
        for label, avg, maximum, weight in groups:
            cells = [ws.cell(r, col) for r in (avg, maximum, weight)]
            vals = [c.value for c in cells]
            if all(v is None for v in vals):
                blanks.append({'assessment': label, 'cells': [c.coordinate for c in cells],
                               'status': 'no_entry_not_confirmed_zero'})
                continue
            if any(v is None for v in vals):
                raise ValueError('评价表三元组不完整：' + ','.join(c.coordinate for c in cells))
            rows.append({'assessment': label, 'average': vals[0], 'maximum': vals[1], 'weight': vals[2],
                         'cells': {k: c.coordinate for k, c in zip(('average', 'maximum', 'weight'), cells)}})
        value = rubric_goal_mean(rows)
        cache = cached.cell(27, col).value
        if cache is None or not math.isclose(value, number(cache), abs_tol=1e-10):
            raise ValueError('目标公式缓存与独立重算不一致：' + gid)
        goals[gid] = {'components': rows, 'unentered_components': blanks, 'recomputed_mean': value,
                      'cached_mean': cache, 'formula_cell': ws.cell(27, col).coordinate,
                      'formula': ws.cell(27, col).value, 'course_goal_weight': ws.cell(28, col).value,
                      'scope': '按当前表已填项目复核的班级平均值；不能推出逐人目标评分或达成率'}
    overall = sum(g['recomputed_mean'] * number(g['course_goal_weight']) for g in goals.values())
    if not math.isclose(overall, number(cached['D29'].value), abs_tol=1e-10):
        raise ValueError('整体目标公式缓存不符')
    prose = str(ws['C34'].value)
    return {'status': 'formula_and_cache_verified', 'goals': goals, 'overall': overall,
            'overall_cell': 'D29', 'analysis_cell': 'C34', 'analysis_text': prose,
            'analysis_numbers': re.findall(r'0\.\d+', prose),
            'prose_conflicts_with_recalculation': any(not math.isclose(float(x), overall, abs_tol=.001)
                                                     for x in re.findall(r'整体目标达成度[^0-9]*(0\.\d+)', prose)),
            'individual_scores_available': False}
