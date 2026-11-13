import copy
import unittest
from report_agent.grade_selection import select
from report_agent.grades import parse_grades

def source(sid,name,course,header,rows):
    return {'id':sid,'path':'/fixtures/'+name,'sha256':sid+'-hash','kind':'grades','role':'current',
            'sheets':[{'name':'表1','rows':[['课程名称：'+course],header]+rows,'formula_cells':[],'merged_cells':[]}], 'blocks':[]}

class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.case={'course_name':'课程甲','grade_mapping_policy':{'mode':'auto_select_v1'}}
        self.facts={'items':[{'id':'usual','name':'平时','score_basis':'raw'},{'id':'final','name':'期末成绩','score_basis':'raw'}]}
        self.header=['学号','姓名','班级','平时成绩','期末','总评成绩']
    def test_confirmed_stale_header_blank_roster_and_zero_are_distinct(self):
        filled=source('filled','学生记分册.xls','课程乙',self.header,[['001','甲','班甲',0,0,0]])
        blank=source('blank','学生记分册空白.xls','课程甲',self.header,[['001','甲','班甲','','',''],['002','乙','班甲','','','']])
        with self.assertRaisesRegex(ValueError,'没有可确认'):select(self.case,[filled,blank],self.facts)
        self.case['grade_mapping_policy']['identity_overrides']={'filled':{'sha256':'filled-hash','confirmation':'用户确认仅标题未改'}}
        mappings,audit=select(self.case,[blank,filled],self.facts)
        self.assertEqual(mappings[0]['items'],{'usual':4,'final':5});self.assertNotIn('total',mappings[1]);self.assertEqual(mappings[1]['items'],{})
        grades=parse_grades({**self.case,'grade_tables':mappings},[filled,blank],self.facts)
        self.assertEqual([s['total'] for s in grades['students']],[0,None])
        self.assertTrue(audit['candidate_tables'][1]['identity_override_used'])
        filled['sha256']='changed'
        with self.assertRaises(ValueError):select(self.case,[filled,blank],self.facts)
    def test_official_total_wins_over_unrounded_calculation_and_leaf_supplement(self):
        main=source('main','成绩单.xls','课程甲',self.header,[['001','甲','班甲',81,80,81],['002','乙','班乙',90,90,90]])
        calc=source('calc','成绩核算.xls','课程甲',['学号','姓名','班级','小组展示','平时成绩','期末','总成绩'],[['002','乙','班乙',95,90,90,90],['001','甲','班甲',80,80.4,80,80.2]])
        facts=copy.deepcopy(self.facts);facts['items'].append({'id':'group','name':'小组展示','score_basis':'raw'})
        mappings,audit=select(self.case,[calc,main],facts)
        self.assertEqual(mappings[0]['source_id'],'main');self.assertEqual(mappings[1]['items'],{'group':4})
        grades=parse_grades({**self.case,'grade_tables':mappings},[calc,main],facts)
        self.assertEqual(grades['students'][0]['total'],81);self.assertEqual(grades['students'][0]['scores']['group'],80)
        self.assertTrue(grades['sample_scope']['multiple_classes']);self.assertFalse(grades['sample_scope']['year_inferred_from_student_id'])
    def test_conflicting_official_tables_or_duplicate_component_copies_are_not_silently_selected(self):
        main=source('main','成绩单.xls','课程甲',self.header,[['001','甲','班甲',80,80,80]])
        other=copy.deepcopy(main);other.update(id='other',path='/fixtures/成绩单副本.xls');other['sheets'][0]['rows'][-1][-1]=90
        with self.assertRaisesRegex(ValueError,'同级正式'):select(self.case,[main,other],self.facts)
        facts=copy.deepcopy(self.facts);facts['items'].append({'id':'group','name':'小组展示','score_basis':'raw'})
        a=source('a','成绩核算.xls','课程甲',['学号','姓名','小组展示','总成绩'],[['001','甲',80,80]])
        b=copy.deepcopy(a);b.update(id='b',path='/fixtures/成绩核算副本.xls');b['sheets'][0]['rows'][-1][2]=90
        with self.assertRaisesRegex(ValueError,'同级补充'):select(self.case,[main,a,b],facts)
    def test_other_course_attendance_ambiguous_totals_and_converted_scores_are_guarded(self):
        main=source('main','成绩单.xls','课程甲',self.header,[['001','甲','班甲',80,80,80]])
        attendance=source('att','学生考勤.xls','课程甲',self.header,[['002','乙','班甲',80,80,80]])
        wrong=source('wrong','成绩单.xls','课程乙',self.header,[['003','丙','班甲',99,99,99]])
        mappings,audit=select(self.case,[attendance,wrong,main],self.facts)
        self.assertEqual(len(mappings),1);self.assertEqual(len(audit['excluded_sources']),2)
        duplicate=copy.deepcopy(main);duplicate['sheets'][0]['rows'][1].append('总成绩')
        with self.assertRaisesRegex(ValueError,'多个总评'):select(self.case,[duplicate],self.facts)
        converted=copy.deepcopy(main);converted['sheets'][0]['rows'][1][3]='平时成绩（折算分）'
        with self.assertRaisesRegex(ValueError,'口径冲突'):select(self.case,[converted],self.facts)

    def test_newer_semester_is_not_merged_into_registered_evaluation_term(self):
        main=source('main','成绩单.xls','课程甲',self.header,[['001','甲','班甲',80,80,80]])
        main['sheets'][0]['rows'][0].append('2021-2022学年第2学期')
        newer=copy.deepcopy(main);newer['id']='new';newer['sheets'][0]['rows'][0][-1]='2022-2023学年第2学期'
        facts={**self.facts,'metadata':{'term':'2021-2022-2'}}
        mappings,audit=select(self.case,[newer,main],facts)
        self.assertEqual(mappings[0]['source_id'],'main');self.assertIn('学期',audit['excluded_sources'][0]['reason'])

if __name__=='__main__':unittest.main()
