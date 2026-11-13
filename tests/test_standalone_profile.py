import copy
import unittest
from report_agent.standalone_profile import compile_plan
from report_agent.compute import evaluate_terms

class StandaloneProfileTests(unittest.TestCase):
    def fixture(self):
        f={'metadata':{'term':'2025-2026-1'},'goals':[{'id':'G1'},{'id':'G2'}],
           'items':[{'id':'I1','assessment_id':'A1','max_score':100},{'id':'I2','assessment_id':'A2','max_score':100}],
           'assessments':[{'id':'A1','total_weight':.5},{'id':'A2','total_weight':.5}],
           'points':[{'id':'P1','goal_weights':{'G1':None}},{'id':'P2','goal_weights':{'G2':None}}]}
        p={'term':'2025-2026-1','goal_ids':['G1','G2'],'allocations':{'I1':{'G1':1,'G2':0},'I2':{'G1':0,'G2':1}},
           'item_total_weights':{'I1':.5,'I2':.5},'item_maxima':{'I1':100,'I2':100},
           'point_course_weights':{'P1':.1,'P2':.1},'threshold':.6,'id':'candidate','assumptions':['explicit proxy']}
        return f,p
    def test_weighted_full_marks_and_no_fact_mutation(self):
        f,p=self.fixture();before=copy.deepcopy(f);view=compile_plan(p,{},f,{})
        self.assertEqual(f,before);self.assertFalse(view['review']['approved'])
        terms=view['goal_rules'][0]['terms'];s={'anonymous_id':'S1','status':'normal','score_status':{'I1':'normal'},'scores':{'I1':80},'refs':{'I1':'s:R1'}}
        r=evaluate_terms(terms,[s],{i['id']:i for i in view['items']},{'normal':'include'})
        self.assertAlmostEqual(r['values']['S1'],.8);self.assertEqual(r['denominator'],50)
        self.assertEqual(view['points'][0]['course_weight'],.1)
    def test_mixed_maxima_are_weighted_as_score_rates(self):
        f,p=self.fixture();f['goals']=f['goals'][:1];f['points']=f['points'][:1]
        f['items'][0]['max_score']=50;p['item_maxima']['I1']=50;p['goal_ids']=['G1']
        p['allocations']={'I1':{'G1':1},'I2':{'G1':1}}
        view=compile_plan(p,{},f,{})
        s={'anonymous_id':'S1','status':'normal','score_status':{'I1':'normal','I2':'normal'},
           'scores':{'I1':25,'I2':100},'refs':{'I1':'s:R1','I2':'s:R1'}}
        r=evaluate_terms(view['goal_rules'][0]['terms'],[s],{i['id']:i for i in view['items']},{'normal':'include'})
        self.assertAlmostEqual(r['values']['S1'],.75)
        self.assertEqual(r['denominator'],100)
    def test_known_maximum_and_goal_rules_cannot_be_overwritten(self):
        f,p=self.fixture();f['items'][0]['max_score']=90
        with self.assertRaises(ValueError):compile_plan(p,{},f,{})
        f,p=self.fixture();f['goal_rules']=[{'id':'G1','terms':[{'item_id':'I1','coefficient':1}]}]
        with self.assertRaises(ValueError):compile_plan(p,{},f,{})
    def test_known_point_weight_and_multiple_goal_mapping_are_guarded(self):
        f,p=self.fixture();f['points'][0]['course_weight']=.2
        with self.assertRaises(ValueError):compile_plan(p,{},f,{})
        f,p=self.fixture();f['points'][0]['goal_weights']['G2']=None
        with self.assertRaises(ValueError):compile_plan(p,{},f,{})
    def test_empty_goal_and_invalid_total_weights_are_rejected(self):
        f,p=self.fixture();p['allocations']['I2']={'G1':1,'G2':0}
        with self.assertRaises(ValueError):compile_plan(p,{},f,{})
        f,p=self.fixture();p['item_total_weights']['I1']=.4
        with self.assertRaises(ValueError):compile_plan(p,{},f,{})
    def test_different_term_and_unknown_item_are_rejected(self):
        f,p=self.fixture();p['term']='2026-2027-1'
        with self.assertRaises(ValueError):compile_plan(p,{},f,{})
        f,p=self.fixture();p['allocations']['wrong']=p['allocations'].pop('I1')
        with self.assertRaises(ValueError):compile_plan(p,{},f,{})

if __name__=='__main__':unittest.main()
