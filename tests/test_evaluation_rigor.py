import copy
import unittest
from report_agent.evaluation_rigor import review
from report_agent.material_scope import inspect
from report_agent.common import ROOT,load
from report_agent.readers import ingest
from report_agent.grades import parse_grades
from report_agent.compute import calculate
from report_agent.tables import build,assessment_summaries

class RigorTests(unittest.TestCase):
    def test_identical_rules_are_reported_as_same_source_not_independent_measurements(self):
        facts={'items':[{'id':'x','max_score':100}], 'goal_rules':[{'id':'G1','terms':[{'item_id':'x','coefficient':1}]},{'id':'G2','terms':[{'item_id':'x','coefficient':.5}]}]}
        calc={'evaluation_context':{'mode':'scenario_estimate'},'goals':{'G1':{'values':{'S1':.65,'S2':.9},'threshold':.6}}}
        before=copy.deepcopy(calc);result=review(facts,calc,{'threshold':.6})
        self.assertEqual(result['equivalent_goal_groups'],[['G1','G2']]);self.assertFalse(result['formal_measurement_verified'])
        self.assertEqual([r['passed'] for r in result['threshold_sensitivity']['G1']],[2,1,1]);self.assertEqual(calc,before)
    def test_latest_is_scoped_to_same_course_and_material_purpose(self):
        def s(sid,name,text):return {'id':sid,'path':'/fixtures/'+name,'role':'current','blocks':[{'id':sid+':1','text':text}]}
        sources=[s('old','教案.docx','课程名称 | 课程甲\n2021-2022学年'),s('latest','新教案.docx','课程名称 | 课程甲\n2025-2026学年'),
                 s('wrong','教案2026.docx','课程名称：课程乙\n2026-2027学年'),s('cal','教学日历.docx','课程名称：课程甲  学分：2\n2020-2021学年')]
        case={'course_name':'课程甲','autonomous_policy':{'latest_applicable_materials':True}}
        result=inspect(case,sources);self.assertEqual(result['selections_by_purpose']['teaching_plan']['selected_source_ids'],['latest'])
        self.assertEqual(result['selections_by_purpose']['calendar']['selected_source_ids'],['cal'])
        self.assertEqual(next(r for r in result['sources'] if r['source_id']=='wrong')['decision'],'excluded_from_latest_selection')
    def test_known_alias_is_explicit_and_filename_year_is_not_proof_of_execution(self):
        source={'id':'s','path':'/fixtures/2026考核方案.docx','role':'current','blocks':[{'id':'s:1','text':'课程名称 | 本课简称\n2025级'}]}
        case={'course_name':'完整课程名','course_aliases':['本课简称'],'autonomous_policy':{'latest_applicable_materials':True}}
        result=inspect(case,[source]);row=result['sources'][0]
        self.assertEqual(row['version_basis'],'filename_version_label_not_proven_applicability');self.assertEqual(row['written_cohorts'],['2025'])
    def test_group_sums_match_visible_goals_and_assessment_tables_match_calculation(self):
        case=load(ROOT/'examples/demo_course/case.json');facts=load(ROOT/'examples/demo_course/facts.json');sources=ingest(case);grades=parse_grades(case,sources,facts)
        for n in range(4,7):
            gid='G'+str(n);facts['goals'].append({**facts['goals'][0],'id':gid});facts['goal_rules'].append({**facts['goal_rules'][0],'id':gid})
            facts['assessment_goal_weights'] += [{**r,'id':r['assessment_id']+':'+gid,'goal_id':gid} for r in facts['assessment_goal_weights'][:] if r['goal_id']=='G1']
        calc=calculate(case,facts,grades);data=build(facts,calc,load(ROOT/'templates/course/buu_course_r2/template_contract.json'))
        for table in data['T3-2']:
            for row in table['rows'][2:]:
                self.assertAlmostEqual(float(row[-1]),sum(map(float,row[2:-1])),delta=.002)
        for table in assessment_summaries(facts,calc):
            self.assertAlmostEqual(sum(table['widths_mm']),146)
            for row in table['rows'][1:]:
                info=next(r for r in calc['assessment_points'].values() if r['point_id']==row[0] and r['assessment_id']==table['assessment_id'])
                self.assertEqual(row[1],str(info['valid_count']));self.assertAlmostEqual(float(row[3]),info['mean'],delta=.00051)

if __name__=='__main__':unittest.main()
