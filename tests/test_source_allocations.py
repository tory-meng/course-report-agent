import copy
import unittest
from report_agent.source_allocations import derive,verify

class SourceAllocationTests(unittest.TestCase):
    def fixture(self):
        quotes={'h':'学习目标 | 1 | 2 | 总计','u':'作业 | 0.4 | 0.25 | 0.75 | 1','f':'考试 | 0.6 | 0.5 | 0.5 | 1'}
        matrix={'goal_header_ref':'h','row_refs':['u','f'],'item_rows':{'i1':['u'],'i2':['f']}}
        return matrix,quotes

    def test_aggregates_actual_source_rows_without_equalizing(self):
        matrix,quotes=self.fixture();w,a=derive(matrix,quotes,['G1','G2'])
        self.assertEqual(w,{'i1':.4,'i2':.6});self.assertAlmostEqual(a['i1']['G1'],.25)
        matrix['item_rows']={'i':['u','f']};w,a=derive(matrix,quotes,['G1','G2'])
        self.assertAlmostEqual(a['i']['G1'],.4)

    def test_actual_goal_scores_split_an_exam_without_double_counting(self):
        matrix,quotes=self.fixture();matrix['item_slices']={
            'i1':[{'source_ref':'u','goal_ids':['G1','G2']}],
            'e1':[{'source_ref':'f','goal_ids':['G1']}],
            'e2':[{'source_ref':'f','goal_ids':['G2']}]}
        w,a=derive(matrix,quotes,['G1','G2'])
        self.assertEqual(w,{'i1':.4,'e1':.3,'e2':.3})
        self.assertEqual(a['e1'],{'G1':1,'G2':0})
        matrix['item_slices']['e2'][0]['goal_ids']=['G1','G2']
        with self.assertRaisesRegex(ValueError,'重复计分'):derive(matrix,quotes,['G1','G2'])

    def test_missing_rows_reordered_goals_and_altered_allocations_rejected(self):
        matrix,quotes=self.fixture();broken=copy.deepcopy(matrix);broken['item_rows']={'i1':['u']}
        with self.assertRaises(ValueError):derive(broken,quotes,['G1','G2'])
        with self.assertRaises(ValueError):derive(matrix,quotes,['G2','G1'])
        w,a=derive(matrix,quotes,['G1','G2'])
        p={'source_matrix':matrix,'goal_ids':['G1','G2'],'item_total_weights':w,'allocations':a}
        f={'metadata':{'quotes':quotes},'goals':[],'items':[{'id':i} for i in w]}
        verify(p,f);p['allocations']['i1']={'G1':.5,'G2':.5}
        with self.assertRaisesRegex(ValueError,'改写'):verify(p,f)

if __name__=='__main__':unittest.main()
