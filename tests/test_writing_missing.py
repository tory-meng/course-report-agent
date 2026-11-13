import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from report_agent.writing import write_sections,factual_numbers,method_body,future_plan,implementation_view
import json

class NumberedParagraphTests(unittest.TestCase):
    def test_only_sequential_layout_ordinals_are_excluded(self):
        self.assertEqual(factual_numbers(['1.原理讲授：使用STM32。','2.小组协作：共35人。'],True),{'32','35'})
        self.assertEqual(factual_numbers(['（1）拟开展答辩。','（2）不得新增90%比例。'],True),{'90'})
        self.assertEqual(factual_numbers(['1.文字'],False),{'1'})
        with self.assertRaises(ValueError):factual_numbers(['1.文字','4.文字'],True)

class MissingWritingTests(unittest.TestCase):
    def setUp(self):
        self.case={'id':'c','course_name':'数字图像处理'}
        self.template={'prompt':'你是报告助手。','contract':{'template_id':'T','editorial_policy':{'suppressed_blocks':[]}},'hashes':{}}
    def test_all_missing_sections_emit_correct_notice_without_model(self):
        with tempfile.TemporaryDirectory() as tmp,patch('report_agent.writing.task',side_effect=AssertionError('缺少依据不应调用模型')):
            for offline in (False,True):
                result,issues=write_sections(self.case,{},None,self.template,None,Path(tmp),offline=offline)
                self.assertEqual(result['6.1'],['缺少上一轮评价及改进记录，本节待补充核验。'])
                self.assertEqual(len(issues),4)
                self.assertTrue(all(len(v)==1 and v[0].startswith('缺少') for v in result.values()))
    def test_online_24_then_missing_61_then_62_and_7_complete(self):
        facts={'methods':[{'id':'M1','name':'讲授','description':'课堂讲授','source_refs':['s:1']}],
               'narratives':[{'id':'N1','section':'6.2','text':'学生参与讨论','source_refs':['s:1']},
                             {'id':'N2','section':'7','text':'计划增加讨论','source_refs':['s:1']}]}
        sections=[]
        def fake(client,messages,record,check,metadata,**kw):
            sections.append(metadata['section_id'])
            answer={'paragraphs':['依据资料填写本节内容。'],'source_refs':['s:1']}
            check(answer);return answer
        with tempfile.TemporaryDirectory() as tmp,patch('report_agent.writing.task',side_effect=fake):
            result,issues=write_sections(self.case,facts,None,self.template,None,Path(tmp))
        self.assertEqual(sections,['2.4','6.2','7'])
        self.assertEqual(issues,['6.1缺少写作依据'])
        self.assertEqual(result['7'],['拟依据资料填写本节内容。'])

    def test_reference_style_titles_are_structured_and_numeric_content_remains_checked(self):
        facts={'methods':[{'id':'M1','name':'讲授','description':'课堂讲授','source_refs':['s:1']}],
               'teaching_reference_context':{'scope':'只借鉴组织表达'}}
        def fake(client,messages,record,check,metadata,**kw):
            self.assertNotIn('method_titles',kw['output_schema']['required'])
            answer={'paragraphs':['1.课堂讲授。'],'source_refs':['s:1']}
            check(answer)
            with self.assertRaises(ValueError):check({**answer,'paragraphs':['课堂讲授占比90%。']})
            return answer
        with tempfile.TemporaryDirectory() as tmp,patch('report_agent.writing.task',side_effect=fake):
            result,_=write_sections(self.case,facts,None,self.template,None,Path(tmp))
        self.assertEqual(result['2.4'],['讲授与演示：课堂讲授。'])

    def test_reference_course_activities_are_not_sent_as_current_writing_evidence(self):
        facts={'methods':[{'id':'M1','name':'课堂讨论','description':'讨论专业词汇','source_refs':['s:1']}],
               'teaching_reference_context':{'hits':[{'text':'其他课程的操作系统分组软件开发实验。'}]}}
        def fake(client,messages,record,check,metadata,**kw):
            data=json.loads(messages[-1]['content'])
            self.assertNotIn('reference_examples',data)
            self.assertNotIn('操作系统',messages[-1]['content'])
            self.assertIn('reference_style',data)
            answer={'paragraphs':['课堂讨论：课堂讨论：讨论专业词汇。'],'source_refs':['s:1']}
            check(answer);return answer
        with tempfile.TemporaryDirectory() as tmp,patch('report_agent.writing.task',side_effect=fake):
            result,_=write_sections(self.case,facts,None,self.template,None,Path(tmp))
        self.assertEqual(result['2.4'],['课堂讨论：讨论专业词汇。'])

    def test_strip_repeated_method_titles_preserves_body_colons(self):
        self.assertEqual(method_body('（1）自主学习：自主学习:阅读材料：专业词汇。','自主学习'),'阅读材料：专业词汇。')
        self.assertEqual(method_body('讨论专业文档。','课堂讨论'),'讨论专业文档。')

    def test_future_arrangements_do_not_guarantee_outcomes(self):
        self.assertEqual(future_plan('需要增加课堂测试以保证词汇掌握。'),'拟增加课堂测试以支持词汇掌握。')
        self.assertEqual(future_plan('计划增加练习。'),'计划增加练习。')
        self.assertEqual(future_plan('缺少已确认的安排。'),'缺少已确认的安排。')

    def test_implementation_claims_without_comparative_data_are_rejected(self):
        facts={'narratives':[{'section':'6.2','text':'材料自评显著提高理解能力，开展课堂讨论。','source_refs':['s:1']}]}
        def fake(client,messages,record,check,metadata,**kw):
            self.assertNotIn('显著提高',json.dumps(json.loads(messages[-1]['content'])['facts'],ensure_ascii=False))
            with self.assertRaisesRegex(ValueError,'效果判断'):
                check({'paragraphs':['据材料记载，显著提高了理解能力。'],'source_refs':['s:1']})
            answer={'paragraphs':['开展课堂讨论。'],'source_refs':['s:1']};check(answer);return answer
        with tempfile.TemporaryDirectory() as tmp,patch('report_agent.writing.task',side_effect=fake):
            result,_=write_sections(self.case,facts,None,self.template,None,Path(tmp))
        self.assertIn('不能据此确认能力提升',result['6.2'][-1])

    def test_activity_view_does_not_modify_source_self_evaluation(self):
        record={'id':'N1','text':'采用案例教学法，介绍软硬件知识，起到了较好的教学效果。同时案例教学法很适合本课，显著提高理解能力。','source_refs':['s:1'],'quotes':{'s:1':'保留原文'}}
        view,audit=implementation_view([record])
        self.assertEqual(view[0]['text'],'采用案例教学法，介绍软硬件知识。')
        self.assertNotIn('quotes',view[0]);self.assertIn('显著提高',record['text']);self.assertEqual(len(audit[0]['omitted_clauses']),3)

if __name__=='__main__':unittest.main()
