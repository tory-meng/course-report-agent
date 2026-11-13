import copy
import unittest
from report_agent.teaching_reference import bind_proposal_links

class TeachingBindingTests(unittest.TestCase):
    def setUp(self):
        self.methods=[{'id':'M1','source_refs':['s:activity']},{'id':'M2','source_refs':['s:reading']}]
        self.goals=[{'id':'G1','source_refs':['s:goal1']},{'id':'G2','source_refs':['s:goal2']}]
        self.answer={'links':[{'method_id':'M1','goal_ids':['G1'],'reason':'通过活动练习专业词汇。'},
                              {'method_id':'M2','goal_ids':['G2'],'reason':'通过课外阅读开展自主学习。'}]}
    def test_host_binds_both_inputs_without_claiming_explicit_relationship(self):
        original=copy.deepcopy(self.answer);links=bind_proposal_links(self.answer,self.methods,self.goals)
        self.assertEqual(links[0]['source_refs'],['s:activity','s:goal1'])
        self.assertEqual(links[1]['source_refs'],['s:goal2','s:reading'])
        self.assertIn('not_explicit_source_relationship',links[0]['evidence_binding'])
        self.assertEqual(self.answer,original)
    def test_unknown_duplicate_missing_methods_and_goals_rejected(self):
        for key,value in [('method_id','other'),('goal_ids',['other']),('goal_ids',['G1','G1'])]:
            answer=copy.deepcopy(self.answer);answer['links'][0][key]=value
            with self.assertRaises(ValueError):bind_proposal_links(answer,self.methods,self.goals)
        with self.assertRaises(ValueError):bind_proposal_links({'links':self.answer['links'][:1]},self.methods,self.goals)
    def test_missing_input_evidence_rejected_and_unsupported_goals_can_stay_empty(self):
        for records in [self.methods,self.goals]:
            original=records[0]['source_refs'];records[0]['source_refs']=[]
            with self.assertRaises(ValueError):bind_proposal_links(self.answer,self.methods,self.goals)
            records[0]['source_refs']=original
        self.answer['links'][0]['goal_ids']=[]
        self.assertEqual(bind_proposal_links(self.answer,self.methods,self.goals)[0]['source_refs'],['s:activity'])

    def test_content_summary_cannot_make_lecture_or_self_reading_teamwork(self):
        methods=[{'id':'M1','name':'课堂讲授','description':'讲解专业词汇','source_refs':['s:activity']},
                 {'id':'M2','name':'自主学习','description':'课余阅读资讯','source_refs':['s:reading']}]
        goals=[{'id':'G1','text':'参与协作学习，具有团队意识。','source_refs':['s:goal1']},
               {'id':'G2','text':'利用课余时间开展自主学习。','source_refs':['s:goal2']}]
        answer=copy.deepcopy(self.answer)
        for link in answer['links']:link['goal_ids']=['G1','G2']
        links=bind_proposal_links(answer,methods,goals)
        self.assertEqual(links[0]['goal_ids'],[])
        self.assertEqual(links[1]['goal_ids'],['G2'])
        self.assertEqual(len(links[0]['rejected_proposals']),2)
        self.assertEqual(links[0]['model_reason'],answer['links'][0]['reason'])
        methods[0]['description']='分组讨论专业词汇'
        self.assertEqual(bind_proposal_links(answer,methods,goals)[0]['goal_ids'],['G1'])

if __name__=='__main__':unittest.main()
