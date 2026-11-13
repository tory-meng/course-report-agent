import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from report_agent.extraction_quality import formal_goal_records
from report_agent.checkbox_allocations import find,derive
from report_agent.score_formulas import unsafe_cells,enforce
from report_agent.standalone_profile import compile_plan
from report_agent.compute import calculate

class LegacyMaterialTests(unittest.TestCase):
    def block(self,ref,text):return {'id':ref,'text':text,'source_id':'s','role':'current'}
    def test_repeated_objective_rows_keep_explicit_numbers_and_split_multiple_points(self):
        text='5 价值：在真实项目中合作并说明个人在团队中的价值。'
        bs=[self.block('s:T1R1','课程目标 | 支撑的毕业要求 | 支撑的毕业要求指标点'),
            self.block('s:T1R2',text+' | 团队 | 9-1：理解团队角色。\n9-2：开展合作。'),
            self.block('s:T1R3','1. 知识：掌握网络规划的知识并能够解释设计方案。 | 问题分析 | 2-1：识别关键参数。'),
            self.block('s:T1R4',text+' | 团队 | 9-2：开展合作。')]
        records,used=formal_goal_records(bs)
        goals=[r for r in records if r['kind']=='goals'];points=[r for r in records if r['kind']=='points']
        self.assertEqual([g['value']['id'] for g in goals],['G1','G5'])
        self.assertEqual({p['value']['id'] for p in points},{'P9_1','P9_2','P2_1'})
        self.assertEqual(next(p for p in points if p['value']['id']=='P9_2')['value']['goal_weights'],{'G5':None})
        self.assertEqual(len(goals[1]['evidence_ids']),3);self.assertEqual(len(used),3)

    def test_one_point_can_retain_multiple_formal_goal_links(self):
        bs=[self.block('s:T1R1','课程目标 | 支撑的毕业要求 | 支撑的毕业要求指标点'),
            self.block('s:T1R2','1.知识：掌握并解释课程基本理论及应用方法。 | 工程知识 | 1-2：分析问题。'),
            self.block('s:T1R3','2.应用：使用课程理论解决实际场景中的问题。 | 工程知识 | 1-2：分析问题。')]
        rs,_=formal_goal_records(bs)
        self.assertEqual(next(r for r in rs if r['kind']=='points')['value']['goal_weights'],{'G1':None,'G2':None})

    def test_checkbox_is_a_relationship_not_an_original_numeric_target_weight(self):
        quotes={'h':'考核方式 | 内容 | 占比 | 占比 | 1 | 2',
            'u':'过程性考核 | 作业 | 100% | 40% | √ | √',
            'f':'终结性考核 | 考试 | 60% | 60% | √ | '}
        binding={'header_ref':'h','row_refs':['u','f'],'item_rows':{'i1':['u'],'i2':['f']}}
        weights,allocations,evidence=derive(binding,quotes,['G1','G2'])
        self.assertEqual(weights,{'i1':.4,'i2':.6});self.assertEqual(allocations['i1'],{'G1':.5,'G2':.5})
        self.assertIn('均分',evidence['allocation_scope'])
        bad=copy.deepcopy(binding);bad['item_rows']['i2'].append('u')
        with self.assertRaises(ValueError):derive(bad,quotes,['G1','G2'])

    def test_inconsistent_multi_component_weights_are_not_normalized_away(self):
        q={'h':'考核方式 | 内容 | 占比 | 占比 | 1',
           'a':'过程性考核 | 作业 | 50% | 40% | √','b':'过程性考核 | 实验 | 60% | 40% | √','f':'终结性考核 | 考试 | 100% | 60% | √'}
        b={'header_ref':'h','row_refs':['a','b','f'],'item_rows':{'a':['a'],'b':['b'],'f':['f']}}
        with self.assertRaisesRegex(ValueError,'不闭合'):derive(b,q,['G1'])

    def test_random_formulas_and_transitive_dependents_are_excluded(self):
        fs={'G6':'=ROUND(I6-10*RAND(),0)','H6':'=ROUND((I6-F6*0.2-G6*0.4)/0.4,0)', 'J6':'=H6+1','K6':'=F6*.4+I6*.6'}
        unsafe=unsafe_cells(fs);self.assertEqual(set(unsafe),{'G6','H6','J6'})
        report={'sheets':[{'source_id':'s','sheet':'Sheet1','unsafe_score_cells':unsafe}]}
        enforce([{'source_id':'s','sheet':'Sheet1','total':9,'items':{'a':6}}],report)
        with self.assertRaisesRegex(ValueError,'随机生成'):enforce([{'source_id':'s','sheet':'Sheet1','items':{'a':8}}],report)

    def test_multi_goal_point_normalizes_each_goal_before_combining(self):
        facts={'metadata':{'term':'t','total_max':100},'goals':[{'id':'G1'},{'id':'G2'}],
            'items':[{'id':'i1','assessment_id':'a','max_score':50},{'id':'i2','assessment_id':'b','max_score':100}],
            'assessments':[{'id':'a','name':'平时成绩','raw_item_id':'i1'},{'id':'b','name':'期末成绩','raw_item_id':'i2'}],
            'points':[{'id':'P','goal_weights':{'G1':None,'G2':None}}]}
        p={'term':'t','id':'p','goal_ids':['G1','G2'],'allocations':{'i1':{'G1':1,'G2':0},'i2':{'G1':0,'G2':1}},
           'item_maxima':{'i1':50,'i2':100},'item_total_weights':{'i1':.3,'i2':.7},'point_course_weights':{'P':.2},'threshold':.6,
           'point_goal_weights':{'P':{'G1':.25,'G2':.75}},'assumptions':['candidate']}
        view=compile_plan(p,{},facts,{})
        g={'registered_count':1,'warnings':[],'students':[{'anonymous_id':'S1','status':'normal','total':64,'scores':{'i1':40,'i2':40},'score_status':{'i1':'normal','i2':'normal','total':'normal'},'refs':{'i1':'r','i2':'r','total':'r'}}]}
        result=calculate({'evaluation_policy':{'normal':'include','missing':'exclude','absent':'exclude','deferred':'exclude','exempt':'exclude','source_refs':['r']}},view,g)
        self.assertAlmostEqual(result['points']['P']['values']['S1'],.5)
        self.assertAlmostEqual(result['points']['P']['contribution'],.1)
        self.assertAlmostEqual(result['assessment_points']['a:P']['values']['S1'],.8)
        self.assertAlmostEqual(result['assessment_points']['b:P']['values']['S1'],.4)
        p['point_goal_weights']['P']={'G1':.25,'G2':.5}
        with self.assertRaises(ValueError):compile_plan(p,{},facts,{})

    def test_targeted_teaching_review_repairs_coverage_without_changing_source_facts(self):
        from report_agent.teaching_reference import repair_coverage
        from report_agent.teaching_presentation import uncovered_goals
        facts={'goals':[{'id':'G1','text':'掌握本课基础知识。','source_refs':['g1']},{'id':'G2','text':'通过团队合作体现个人价值。','source_refs':['g2']}],
               'methods':[{'id':'lecture','name':'讲授','description':'讲授课程基础知识。','source_refs':['m1']},
                          {'id':'group','name':'小组合作','description':'小组合作完成任务并说明分工。','source_refs':['m2']}]}
        links=[{'method_id':'lecture','goal_ids':['G1'],'reason':'讲解基础知识。'},{'method_id':'group','goal_ids':[],'reason':'尚无建议关系。'}]
        before=copy.deepcopy(facts)
        answer={'repairs':[{'goal_id':'G2','method_ids':['group'],'reason':'通过合作任务及个人分工体现团队和个人贡献。'}]}
        def fake(client,messages,record,check,*args,**kwargs):check(answer);return answer
        with tempfile.TemporaryDirectory() as d,patch('report_agent.teaching_reference.task',side_effect=fake):
            result=repair_coverage({'id':'c','course_name':'课程'},facts,links,None,Path(d))
        self.assertFalse(uncovered_goals(facts,result));self.assertEqual(facts,before)
        self.assertEqual(links[1]['goal_ids'],[])

    def test_teaching_gap_review_cannot_assign_team_goal_to_lecture_without_activity(self):
        from report_agent.teaching_reference import repair_coverage
        facts={'goals':[{'id':'G1','text':'培养团队合作能力。','source_refs':['g']}],
               'methods':[{'id':'lecture','name':'讲授','description':'讲解基础知识。','source_refs':['m']}]}
        links=[{'method_id':'lecture','goal_ids':[],'reason':'缺少协作活动。'}]
        def fake(client,messages,record,check,*args,**kwargs):
            answer={'repairs':[{'goal_id':'G1','method_ids':['lecture'],'reason':'声称讲授即支持团队。'}]};check(answer);return answer
        with tempfile.TemporaryDirectory() as d,patch('report_agent.teaching_reference.task',side_effect=fake),self.assertRaisesRegex(ValueError,'活动条件'):
            repair_coverage({'id':'c','course_name':'课程'},facts,links,None,Path(d))

    def test_missing_evidence_remains_missing_after_targeted_review(self):
        from report_agent.teaching_reference import repair_coverage
        from report_agent.teaching_presentation import uncovered_goals
        facts={'goals':[{'id':'G1','text':'培养团队合作能力。','source_refs':['g']}],
               'methods':[{'id':'lecture','name':'讲授','description':'讲解基础知识。','source_refs':['m']}]}
        links=[{'method_id':'lecture','goal_ids':[],'reason':'缺少活动。'}]
        def fake(client,messages,record,check,*args,**kwargs):
            answer={'repairs':[{'goal_id':'G1','method_ids':[],'reason':'当前没有团队协作活动依据。'}]};check(answer);return answer
        with tempfile.TemporaryDirectory() as d,patch('report_agent.teaching_reference.task',side_effect=fake):
            result=repair_coverage({'id':'c','course_name':'课程'},facts,links,None,Path(d))
        self.assertEqual(uncovered_goals(facts,result),['G1'])

    def test_foreign_graphics_example_is_excluded_from_network_teaching(self):
        from report_agent.material_preparation import methods_from_outline
        outline={'blocks':[self.block('s:P1','【教学方法与学习活动】'),
            self.block('s:P2','1.通过课堂讨论理解网络协议的适用条件。'),
            self.block('s:P3','2.通过讨论理解坐标系变换、透视投影。')]}
        excluded=[];methods=methods_from_outline(outline,'计算机网络与应用','理解网络协议。',excluded)
        self.assertEqual(len(excluded),1);self.assertNotIn('透视投影',''.join(m['description'] for m in methods))
        retained=methods_from_outline(outline,'计算机图形学','理解坐标系变换、透视投影。',[])
        self.assertIn('透视投影',''.join(m['description'] for m in retained))

    def test_gap_review_rejects_generic_reason_even_when_method_ids_are_valid(self):
        from report_agent.teaching_reference import repair_coverage
        facts={'goals':[{'id':'G1','text':'理解项目的设计与实现。','source_refs':['g']}],
               'methods':[{'id':'lab','name':'项目实践','description':'完成项目配置并展示结果。','source_refs':['m']}]}
        links=[{'method_id':'lab','goal_ids':[],'reason':'尚无建议。'}]
        def fake(client,messages,record,check,*args,**kwargs):
            answer={'repairs':[{'goal_id':'G1','method_ids':['lab'],'reason':'按目标原文选择能够支撑该能力的活动，并保留原目标表述。'}]};check(answer);return answer
        with tempfile.TemporaryDirectory() as d,patch('report_agent.teaching_reference.task',side_effect=fake),self.assertRaisesRegex(ValueError,'空泛'):
            repair_coverage({'id':'c','course_name':'课程'},facts,links,None,Path(d))

    def test_autonomous_design_goal_requires_an_active_task(self):
        from report_agent.teaching_reference import bind_proposal_links
        from report_agent.report_scope import method_label
        goals=[{'id':'G5','text':'通过自主设计图像算法的挑战培养兴趣与自主创新能力。','source_refs':['g']}]
        methods=[{'id':'lecture','name':'课堂讲授','description':'讲解图像分割的基本概念。','source_refs':['m1']},
                 {'id':'lab','name':'实验与实践','presentation_name':'汇报与答辩','description':'开展图像算法练习并汇报实验结果。','source_refs':['m2']}]
        answer={'links':[{'method_id':m['id'],'goal_ids':['G5'],'reason':'安排课程任务。'} for m in methods]}
        links=bind_proposal_links(answer,methods,goals)
        self.assertEqual(links[0]['goal_ids'],[]);self.assertEqual(links[1]['goal_ids'],['G5'])
        self.assertEqual(method_label({'methods':methods},methods[1]),'实验与实践')

if __name__=='__main__':unittest.main()
