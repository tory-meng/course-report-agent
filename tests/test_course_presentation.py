import unittest
from unittest.mock import patch
from pathlib import Path
from report_agent.course_presentation import human,student_records,chart_analysis
from report_agent.course_design import fit_two_components

class CoursePresentationTests(unittest.TestCase):
    def test_goal_names_do_not_change_calculation_identifiers(self):
        self.assertEqual(human('G1、课程目标G2；G12'), '课程目标1、课程目标2；课程目标12')
        self.assertEqual(human('goal_G1 P5_2'), 'goal_G1 P5_2')

    def test_named_roster_retains_missing_scores(self):
        rows=student_records({'students':[{'id':'202501','name':'王同学','anonymous_id':'S001','status':'missing','scores':{'I_usual':None},'total':None}]})
        self.assertEqual(rows[0]['name'],'王同学')
        self.assertIsNone(rows[0]['total'])
        self.assertEqual(rows[0]['key'],'S001')

    def test_different_activities_do_not_share_a_generic_display_name(self):
        from report_agent.report_scope import method_label
        methods=[{'id':'m1','name':'实验教学','presentation_name':'任务实训'},
                 {'id':'m2','name':'专题报告','presentation_name':'任务实训'}]
        facts={'methods':methods}
        self.assertEqual([method_label(facts,m) for m in methods],['实验教学','专题报告'])

    def test_formula_fit_checks_every_row_and_excel_rounding(self):
        candidates=fit_two_components([(80,81,81),(100,0,50),(0,100,50)])
        self.assertEqual(candidates,[{'weights':[.5,.5],'round_digits':0}])
        self.assertFalse(fit_two_components([(100,0,50),(0,100,50),(80,80,90)]))

    def test_formula_fit_does_not_claim_uniqueness(self):
        self.assertGreater(len(fit_two_components([(80,80,80),(90,90,90)])),1)
        self.assertEqual(fit_two_components([]),[])

    def test_scatter_analysis_uses_actual_sample_and_threshold(self):
        text=chart_analysis({'kind':'scatter','series':{'valid_count':3,'min':.5,'mean':.7,'max':.9,'threshold':.6,'values':{'S1':.5,'S2':.7,'S3':.9}}})
        self.assertIn('3名学生',text);self.assertIn('1人低于标准',text)
        self.assertNotIn('显著',text)

    def test_equal_curves_do_not_claim_equal_abilities(self):
        text=chart_analysis({'kind':'goal_summary','series':[{'id':'G1','mean':.8},{'id':'G2','mean':.8}]})
        self.assertIn('不能仅据此判断不同能力完全一致',text)

    def test_exact_threshold_is_not_described_as_above(self):
        text=chart_analysis({'kind':'scatter','series':{'valid_count':2,'min':.6,'mean':.7,'max':.8,'threshold':.6,'values':{'S1':.6,'S2':.8}}})
        self.assertIn('最低值恰好等于标准',text)
        self.assertNotIn('高于标准0.000',text)

    def test_source_component_formula_overrides_seed_weights(self):
        from report_agent.course_design import design_goals
        facts={'items':[{'id':'i1','assessment_id':'a'},{'id':'i2','assessment_id':'a'},{'id':'i3','assessment_id':'b'}], 'goals':[{'id':'G1'},{'id':'G2'}]}
        profile={'item_total_weights':{'i1':.1,'i2':.4,'i3':.5}}
        scheme={'parents':[{'id':'a'},{'id':'b'}],'selected':{'weights':[.6,.4]},'component_formula_evidence':[{'assessment_id':'a','within_parent_weights':{'i1':.2,'i2':.8}}]}
        answer={'links':[{'item_id':iid,'goal_ids':(['G1'] if iid=='i1' else ['G2']),'reason':'课程内容'} for iid in ('i1','i2','i3')]}
        def fake_task(client,messages,record,check,*args,**kwargs):check(answer);return answer
        with patch('report_agent.course_design.task',side_effect=fake_task):
            result=design_goals({'id':'test','course_name':'测试'},facts,profile,scheme,None,Path('/tmp/test'))
        self.assertAlmostEqual(result['item_total_weights']['i1'],.12)
        self.assertAlmostEqual(result['item_total_weights']['i2'],.48)
        self.assertEqual(profile['item_total_weights']['i1'],.1)
        self.assertFalse(result['design_record']['approved'])
        self.assertEqual(result['allocations']['i1'],{'G1':1.,'G2':0})

if __name__=='__main__':unittest.main()
