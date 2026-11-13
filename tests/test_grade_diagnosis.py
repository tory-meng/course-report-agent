import unittest
from pathlib import Path
from unittest.mock import patch
from scripts.diagnose_grades import review

class SemanticGuardTests(unittest.TestCase):
    def table(self):
        return {'source_id':'s','sheet':'成绩','columns':[
            {'column':1,'label':'序号','role':'unknown','score_basis':'unknown'},
            {'column':2,'label':'学号','role':'student_id','score_basis':'unknown'},
            {'column':3,'label':'平时考核','role':'unknown','score_basis':'unknown'}]}

    def test_real_error_patterns_cannot_be_adopted_as_column_semantics(self):
        valid={'columns':{'1':{'role':'other','score_basis':'not_score','reason':'序号不是学生身份。'},
                          '2':{'role':'student_id','score_basis':'not_score','reason':'学号。'},
                          '3':{'role':'unresolved','score_basis':'unknown','reason':'分数口径待核对。'}}}
        def fake(client,messages,out,check,metadata,**kwargs):
            import copy
            for col,field,value in [('1','role','student_id'),('3','score_basis','converted')]:
                bad=copy.deepcopy(valid);bad['columns'][col][field]=value
                with self.assertRaises(ValueError):check(bad)
            check(valid);return valid
        with patch('scripts.diagnose_grades.task',side_effect=fake):
            self.assertEqual(review(None,{'id':'c','course_name':'课程'},self.table(),Path('/unused')),valid)

    def test_formula_verified_roles_remain_separate_from_unknown_full_marks(self):
        t=self.table();t['verified_roles']={'3':'assessment'}
        def fake(client,messages,out,check,metadata,**kwargs):
            fields=kwargs['output_schema']['properties']['columns']['properties']
            self.assertEqual(fields['3']['properties']['role']['enum'],['assessment'])
            self.assertEqual(fields['3']['properties']['score_basis']['enum'],['unknown'])
            return {'columns':{}}
        with patch('scripts.diagnose_grades.task',side_effect=fake):review(None,{'id':'c','course_name':'课程'},t,Path('/unused'))

if __name__=='__main__':unittest.main()
