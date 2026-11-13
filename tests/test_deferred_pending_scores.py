import unittest
from report_agent.grades import parse_grades
from report_agent.compute import status_score
from report_agent.course_presentation import student_total_text

class DeferredPendingScoresTests(unittest.TestCase):
    def fixture(self,status):
        case={'grade_tables':[{'source_id':'s','sheet':'Sheet1','header_row':1,'student_id':1,'name':2,'status':6,
            'total':5,'items':{'u':3,'f':4}}]}
        sources=[{'id':'s','role':'current','sheets':[{'name':'Sheet1','formula_cells':[],
            'rows':[['学号','姓名','平时','期末','总评','备注'],['2999000000001','赵同学',80,'待定',73,status]]}]}]
        facts={'items':[{'id':'u'},{'id':'f'}]}
        return case,sources,facts

    def test_explicit_deferred_state_preserves_total_but_excludes_it(self):
        args=self.fixture('缓考');student=parse_grades(*args)['students'][0]
        self.assertIsNone(student['scores']['f']);self.assertEqual(student['total'],73)
        self.assertEqual(student['status'],'deferred');self.assertEqual(student['score_status']['f'],'deferred')
        self.assertEqual(status_score(student,'total',{'deferred':'exclude'}),(None,'deferred'))

    def test_pending_without_explicit_state_is_not_silently_accepted(self):
        with self.assertRaisesRegex(ValueError,'不是可确认成绩'):parse_grades(*self.fixture(''))

    def test_display_distinguishes_cached_score_from_statistical_inclusion(self):
        self.assertEqual(student_total_text({'status':'deferred'},'73',False),'73（缓考，不计统计）')
        self.assertEqual(student_total_text({'status':'deferred'},'73',True),'73（缓考，按政策计入）')
        self.assertEqual(student_total_text({'status':'normal'},'85',True),'85')

    def test_class_headers_bind_only_the_following_student_block(self):
        case,sources,facts=self.fixture('缓考')
        sources[0]['sheets'][0]['rows']=[['学号','姓名','平时','期末','总评','备注'],
            ['行政班：软件2101B'],['2999000000002','甲',80,82,81,''],
            ['行政班：自动化1901B'],['2999000000003','乙',75,77,76,'']]
        grades=parse_grades(case,sources,facts)
        self.assertTrue(grades['sample_scope']['multiple_classes'])
        self.assertEqual(grades['students'][0]['class_names'],['软件2101B'])
        self.assertEqual(grades['students'][1]['class_names'],['自动化1901B'])
        self.assertEqual(grades['students'][1]['class_refs']['自动化1901B'],['s:Sheet1:R4:C1'])

if __name__=='__main__':unittest.main()
