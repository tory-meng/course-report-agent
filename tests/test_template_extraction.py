import unittest
from report_agent.direct_extract import labelled_row,course_label,split_direct
from report_agent.facts import validate_extracted
from report_agent.structured import extraction_schema,validate_schema
from report_agent.extraction_targets import build_targets,targets_for

BLOCKS=[
 {'id':'s:R1','role':'current','source_id':'s','text':'C1=2023-2024学年第1学期记分册'},
 {'id':'s:R3','role':'current','source_id':'s','text':'C1=课号：(2023-2024-1)-DEMO321212C-20990011-1 | C4=课程名称：数字图像处理/考查'},
 {'id':'s:R4','role':'current','source_id':'s','text':'C1=开课学院：机器人学院 | C4=任课教师：测试龘/虚构乙 | C5=学分：2.0'},
 {'id':'s:R6','role':'current','source_id':'s','text':'C4=软件工程'},
]
ANSWER={'records':[
 {'kind':'metadata','value':{'academic_year':'2023-2024','semester':'1','course_name':'数字图像处理','college':'机器人学院','teacher':'测试龘/虚构乙','credits':2},'evidence_ids':['s:R1','s:R3','s:R4']},
 {'kind':'metadata','value':{'major':'软件工程'},'evidence_ids':['s:R6']},
]}

class TemplateExtractionTests(unittest.TestCase):
    def test_r4_first_answer_passes_without_modifying_original(self):
        validate_schema(ANSWER,extraction_schema(BLOCKS,['metadata']))
        r=validate_extracted(ANSWER,BLOCKS,{b['id']:b for b in BLOCKS})
        self.assertEqual(r[0]['value']['course_name'],'数字图像处理')
        self.assertIn('/考查',r[0]['value']['quotes']['s:R3'])
    def test_suffix_split_preserves_source_and_separates_offering_code(self):
        b={**BLOCKS[1],'extract_kinds':['metadata']}
        values,covered=labelled_row(b)
        self.assertTrue(covered)
        self.assertEqual(values['course_name'],'数字图像处理')
        self.assertEqual(values['assessment_form'],'考查')
        self.assertEqual(values['offering_code'],'(2023-2024-1)-DEMO321212C-20990011-1')
        self.assertNotIn('course_code',values)
        direct,rest=split_direct([b]);self.assertFalse(rest)
        validate_schema({'records':direct},extraction_schema([b],['metadata']))
        validate_extracted({'records':direct},[b],{b['id']:b})
    def test_unknown_slash_course_and_teacher_not_normalized(self):
        self.assertEqual(course_label('软件/硬件设计'),{'course_name':'软件/硬件设计'})
        self.assertEqual(labelled_row(BLOCKS[2])[0]['teacher'],'测试龘/虚构乙')
    def test_second_attempt_semantic_errors_are_rejected(self):
        cases=[({'course_category':'考查'},'s:R3','assessment_form'),
               ({'course_nature':'考试'},'s:R3','assessment_form'),
               ({'course_code':'(2023-2024-1)-DEMO321212C-20990011-1'},'s:R3','offering_code'),
               ({'course_name':'数字图像'},'s:R3','不能截短'),
               ({'teacher':'测试龘/虚构'},'s:R4','不能截短')]
        for val,ref,error in cases:
            with self.subTest(val=val),self.assertRaisesRegex(ValueError,error):
                validate_extracted({'records':[{'kind':'metadata','value':val,'evidence_ids':[ref]}]},BLOCKS,{b['id']:b for b in BLOCKS})
    def test_template_passes_headers_not_example_rows(self):
        contract={'template_id':'T','sections':[{'id':'2.2','title':'课程目标','inputs':'课程大纲','instruction':'完整目标','table_ids':['T1']}],
                  'tables':[{'id':'T1','rows':[['目标','内容'],['参考G1','参考样本的实际内容']],'header_rows':1}],
                  'chart_slots':[{'id':'F1','section':'5.3.3','kind':'scatter','required_data':'有效逐人达成度'}]}
        plan=build_targets(contract,'T');prompt=targets_for(plan,['goals'])
        self.assertTrue(prompt['bound']);self.assertEqual(prompt['tables'][0]['headers'],[['目标','内容']])
        self.assertNotIn('参考样本',str(plan));self.assertEqual(prompt['chart_inputs'][0]['id'],'F1')
        self.assertEqual(targets_for(plan,['metadata'])['tables'],[])
        with self.assertRaises(ValueError):build_targets(contract,'OTHER')

if __name__=='__main__':unittest.main()
