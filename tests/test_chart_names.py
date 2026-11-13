import copy
import unittest
from report_agent.charts import jobs,validate_captions

class ChartNameTests(unittest.TestCase):
    def fixture(self,estimate=False):
        row={'values':{'S001':.8},'valid_count':1,'min':.8,'mean':.8,'max':.8,'threshold':.7,'source_refs':['s:R1']}
        facts={'metadata':{'course_name':'课程甲'},'assessments':[{'id':'A','name':'平时考核','raw_item_id':'I','source_refs':['s:R1']}],
               'goals':[{'id':'G1','source_refs':['s:R2']}],'points':[{'id':'P1_3','source_refs':['s:R3']}]}
        calc={'total':{'source_refs':['s:R1']},'assessments':{'A':row},'assessment_points':{'A:P':{**row,'point_id':'P1_3','assessment_id':'A'}},
              'goals':{'G1':row},'points':{'P1_3':row},'snapshot_sha256':'snapshot'}
        if estimate:calc['evaluation_context']={'mode':'scenario_estimate'}
        return facts,calc

    def test_chart_type_entity_and_metric_are_named_consistently(self):
        f,c=self.fixture();planned=jobs(f,c);lookup={j['id']:j for j in planned}
        self.assertTrue(all('达成表' not in j['caption'] and j['caption'].endswith('图') for j in planned))
        self.assertIn('课程目标G1个体达成度散点图',lookup['goal_G1']['caption'])
        self.assertIn('观测点P1_3',lookup['point_P1_3']['caption'])
        self.assertIn('平时考核支撑观测点P1_3',lookup['assessment_A_P1_3']['caption'])
        self.assertIn('最低、平均和最高达成度',lookup['assessment_A']['lead_in'])
        self.assertNotIn('达成率',lookup['goals']['lead_in'])

    def test_estimate_marks_never_apply_to_actual_grade_charts(self):
        f,c=self.fixture(True);planned=jobs(f,c)
        for j in planned:
            estimated=j['id'] not in ('distribution','pie','score_scatter','assessment_raw_summary')
            self.assertEqual('代理估计' in j['caption'],estimated)
            self.assertNotIn('代理估计',j['lead_in'])

    def test_caption_mismatch_or_duplicate_is_a_failure(self):
        f,c=self.fixture();manifest=[{**j,'status':'filled'} for j in jobs(f,c)];paras=[j['caption'] for j in manifest]
        self.assertEqual(validate_captions(paras,manifest)['checked'],len(manifest))
        with self.assertRaises(ValueError):validate_captions(paras[:-1],manifest)
        with self.assertRaises(ValueError):validate_captions(paras+[paras[0]],manifest)
        with self.assertRaises(ValueError):validate_captions(['旧名称']+paras[1:],manifest)

    def test_class_summary_does_not_claim_individual_extremes(self):
        f,c=self.fixture();c['goals']['G1']=copy.deepcopy(c['goals']['G1']);c['goals']['G1']['mean']=None
        c['aggregate_goals']={'G1':{'mean':.8,'source_refs':['s:D27']}}
        chart=next(j for j in jobs(f,c) if j['id']=='goals')
        self.assertEqual(chart['kind'],'aggregate_summary')
        self.assertIn('班级平均达成度',chart['caption']);self.assertNotIn('最低',chart['lead_in'])

if __name__=='__main__':unittest.main()
