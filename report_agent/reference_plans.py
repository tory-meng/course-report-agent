"""按课程类型、考核结构及目标语义筛选参考，合并重复年份。"""
import re
from .reference_db import connect


def normalized_title(filename):
    match = re.search(r'《([^》]+)》', filename)
    return re.sub(r'[\s\-—_]', '', match[1] if match else filename)


def threshold_evidence(rows):
    result = []
    for row in rows:
        section = re.search(r'5[.．]\s*1\s*课程目标达成标准', row['text'])
        if not section:
            continue
        excerpt = row['text'][section.start():section.start() + 400]
        match = re.search(r'设定为\s*(0[.．]\d+)', excerpt)
        if match:
            result.append({'value': float(match[1].replace('．', '.')), 'reference_id': row['id'],
                           'locator': row['locator'], 'quote': excerpt[:match.end()], 'ocr': bool(row['ocr'])})
    return result


def rank_references(db, assessment_names, goals):
    """按考核结构、实践属性、目标语义、章节覆盖和可读性评分，按课程去重。"""
    synonyms = {'平时考核': ['平时', '过程性'], '设计报告': ['设计报告', '项目报告', '报告'],
                '验收答辩': ['验收答辩', '答辩', '项目汇报']}
    concepts = [('编程', '程序', '设计', '开发'), ('团队', '协作', '合作'),
                ('社会', '安全', '环境'), ('沟通', '报告', '展示')]
    target = ''.join(goals)
    active_concepts = [words for words in concepts if any(word in target for word in words)]
    ranked = []
    with connect(db, True) as conn:
        for doc in conn.execute('SELECT id,relpath,sha256 FROM documents WHERE active=1 AND report_type="course" AND status="ready"'):
            rows = [dict(r) for r in conn.execute('SELECT id,locator,text,ocr FROM chunks WHERE document_id=?', (doc['id'],))]
            all_text = '\n'.join(r['text'] for r in rows)
            matches = {name: next((word for word in synonyms.get(name, [name]) if word in all_text), None)
                       for name in assessment_names}
            assessment = 40 * sum(v is not None for v in matches.values()) / max(1, len(matches))
            practice = 20 if any(word in doc['relpath'] for word in ('实践', '实训', '综合设计')) else (
                8 if '项目' in all_text else 0)
            semantic = 20 * sum(any(w in all_text for w in words) for words in active_concepts) / max(1, len(active_concepts))
            coverage = 10 * sum(bool(re.search(pat, all_text)) for pat in
                                (r'2[.．]\s*4', r'3[.．]\s*2', r'5[.．]\s*1', r'5[.．]\s*3')) / 4
            evidence = threshold_evidence(rows)
            quality = 5 if evidence and all(e['ocr'] for e in evidence) else (10 if evidence else 0)
            teaching = next((r for r in rows if re.search(r'2[.．]\s*4\s*课程教学方式', r['text'])), None)
            teaching_text=''
            if teaching:
                start=re.search(r'2[.．]\s*4\s*课程教学方式',teaching['text']).start()
                teaching_text=re.split(r'2[.．]\s*5\s*课程教学方式',teaching['text'][start:])[0][:1500]
            ranked.append({'reference_id': doc['id'], 'file': doc['relpath'], 'sha256': doc['sha256'],
                           'course_key': normalized_title(doc['relpath']),
                           'score': round(assessment + practice + semantic + coverage + quality, 2),
                           'breakdown': {'assessment_structure': assessment, 'practice_type': practice,
                                         'goal_semantics': semantic, 'section_coverage': coverage, 'readability': quality},
                           'assessment_matches': matches, 'threshold_candidates': evidence,
                           'teaching_excerpt': teaching_text,
                           'teaching_evidence': {'reference_id':teaching['id'],'locator':teaching['locator'],'ocr':bool(teaching['ocr'])} if teaching else None,
                           'limits': ['只推荐组织和候选制度；不导入参考课程学生成绩、目标文本或已实施效果',
                                      '词项评分用于解释排序，不是经验证的语义相似度概率']})
    ranked.sort(key=lambda r: (r['score'], r['file']), reverse=True)
    unique = {}
    for row in ranked:
        if row['course_key'] not in unique:
            row['other_editions'] = []
            unique[row['course_key']] = row
        else:
            unique[row['course_key']]['other_editions'].append(row['file'])
    return list(unique.values())


