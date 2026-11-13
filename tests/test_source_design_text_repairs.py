import copy
import json
from pathlib import Path
import tempfile
import unittest
from report_agent.common import save
from report_agent.course_design import improvement_candidates
from report_agent.report_scope import method_label
from report_agent.writing import method_body
from report_agent.refresh import verified_sections,verified_total_rule_description
from report_agent.structured import obj,array,choice
from report_agent.course_presentation import student_record_pages

class SourceDesignTextRepairsTests(unittest.TestCase):
    def facts(self):
        return {'metadata':{'course_name':'创新任务训练'},'preparation':{'origin':'explicit_source_bound_course_design'},
            'goals':[{'id':'G'+str(n),'text':('学生应能够理解并实践在不同任务中承担的角色，分析方法并整理反馈。'*3)} for n in range(1,7)],
            'methods':[{'id':'M1','name':'创新创业项目实践','description':'围绕研究思路撰写文档并制作汇报PPT。','source_refs':['s:P1'],'presentation_name':'汇报与答辩'}]}

    def test_long_goal_text_is_never_cut_in_the_middle(self):
        facts=self.facts();before=copy.deepcopy(facts);result=improvement_candidates(facts)
        self.assertEqual(facts,before)
        self.assertTrue(all(len(p)<=180 and p.endswith('。') for rows in result.values() for p in rows))
        self.assertNotIn('在不。',''.join(result['6.1']))
        self.assertIn('创新创业项目实践',''.join(result['6.1']))

    def test_bound_source_name_wins_over_a_generic_keyword_alias(self):
        facts=self.facts();self.assertEqual(method_label(facts,facts['methods'][0]),'创新创业项目实践')

    def test_discussion_body_does_not_repeat_an_assessment_label(self):
        self.assertEqual(method_body('课堂讨论：课堂表现：鼓励参与讨论。','课堂讨论'),'鼓励参与讨论。')

    def test_balanced_student_pages_keep_every_record_and_order(self):
        for n in (23,62,86):
            records=list(range(n));pages=student_record_pages(records)
            self.assertEqual([r for _,rows in pages for r in rows],records)
            self.assertLessEqual(max(len(rows) for _,rows in pages),26)
        self.assertEqual([len(rows) for _,rows in student_record_pages(list(range(62)))],[21,21,20])

    def record(self,p):
        old={sid:['旧宿主段落一。','旧宿主段落二。'] for sid in ('6.1','6.2','7')}
        schema=obj({s:array(choice(old[s]),2,2) for s in old},list(old))
        record={'ok':True,'answer':old,'output_schema':schema,
            'messages':[{'role':'user','content':json.dumps({'section_candidates':old},ensure_ascii=False)}]}
        save(p/'records/improvement_design.json',record);return record

    def test_host_repair_is_explicit_and_preserves_original_record(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);record=self.record(p)
            with self.assertRaisesRegex(ValueError,'需重新生成'):verified_sections(p,self.facts())
            audit={};result=verified_sections(p,self.facts(),True,audit)
            self.assertEqual(set(audit),{'6.1','6.2','7'});self.assertNotEqual(result['6.1'],record['answer']['6.1'])
            self.assertEqual(json.loads((p/'records/improvement_design.json').read_text()),record)

    def test_host_repair_refuses_a_mismatched_original_prompt(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);record=self.record(p)
            record['messages'][0]['content']='{"section_candidates":{}}';save(p/'records/improvement_design.json',record)
            with self.assertRaisesRegex(ValueError,'不能迁移'):verified_sections(p,self.facts(),True,{})

    def test_known_total_rule_requires_source_percentages_and_all_row_agreement(self):
        facts={'metadata':{},'items':[{'id':'u','max_score':100},{'id':'f','max_score':100}],
            'assessments':[{'name':'平时成绩','raw_item_id':'u','total_weight':.5,'quotes':{'s:R82':'平时：50%；期末：50%'}},
                           {'name':'期末成绩','raw_item_id':'f','total_weight':.5,'quotes':{'s:R82':'平时：50%；期末：50%'}}]}
        audit={'mismatch_count':0,'compared_count':61,'comparison_round_digits':0}
        rule=verified_total_rule_description(facts,audit);self.assertIn('61条有效评分',rule['text'])
        self.assertEqual(rule['source_refs'],['s:R82'])
        self.assertIsNone(verified_total_rule_description(facts,{**audit,'mismatch_count':2}))
        facts['assessments'][0]['quotes']={'s:R5':'平时成绩'}
        self.assertIsNone(verified_total_rule_description(facts,audit))

if __name__=='__main__':unittest.main()
