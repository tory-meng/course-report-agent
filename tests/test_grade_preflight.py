import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from report_agent.grade_preflight import check,inspect_source
from report_agent.common import save,load
from report_agent.material_catalog import scan

def source(rows,name='成绩单.csv'):
    return {'id':'s','role':'current','path':name,'sheets':[{'name':'Sheet1','rows':rows}]}

class GradePreflightTests(unittest.TestCase):
    def test_real_zero_passes_empty_does_not(self):
        rows=[['学号','姓名','总评'],['1','张同学',0]]
        self.assertEqual(check({'id':'c'},[source(rows)])['status'],'passed')
        rows[1][-1]=''
        self.assertEqual(check({'id':'c'},[source(rows)])['status'],'blocked_empty_grades')

    def test_attendance_and_aggregate_not_grade_rosters(self):
        self.assertEqual(check({'id':'c'},[source([['学号','姓名','总评'],['1','张同学',90]],'学生考勤.csv')])['status'],'blocked_missing_grades')
        self.assertEqual(check({'id':'c'},[source([['平均分','人数'],[89,30]])])['status'],'blocked_missing_grades')

    def test_numeric_remarks_are_not_assumed_to_be_scores(self):
        result=check({'id':'c'},[source([['学号','姓名','备注'],['12345678','张同学',90]])])
        self.assertEqual(result['status'],'blocked_grade_semantics')
        self.assertNotIn('passed',result['status'])

    def test_english_and_name_only_headers(self):
        s=source([['Paper ID','Presenter','Score'],['20240001','王同学',85]])
        r=inspect_source(s)
        self.assertTrue(r['tables'][0]['normalized_english_headers'])
        self.assertEqual(check({'id':'c'},[s])['status'],'passed')
        s=source([['姓名','总评'],['王同学',85]])
        self.assertTrue(inspect_source(s)['tables'][0]['requires_name_only_adapter'])
        self.assertEqual(check({'id':'c'},[s])['status'],'passed')

    def test_headerless_needs_explicit_grade_filename_and_one_score_column(self):
        rows=[['2024000'+str(i),'王同学',80+i,'论文评价'] for i in range(3)]
        self.assertEqual(check({'id':'c'},[source(rows)])['status'],'passed')
        self.assertEqual(check({'id':'c'},[source(rows,'学生名单.csv')])['status'],'blocked_grade_semantics')
        rows=[r+[90] for r in rows]
        self.assertEqual(check({'id':'c'},[source(rows)])['status'],'blocked_grade_semantics')

    def test_missing_grades_stops_before_template_and_model(self):
        from report_agent.pipeline import run
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);save(d/'case.json',{'id':'missing','course_name':'缺成绩课程','sources':[]});save(d/'runtime.json',{})
            with patch('report_agent.pipeline.Client',side_effect=AssertionError('must not call model')) as model,patch('report_agent.pipeline.load_template',side_effect=AssertionError('must not load template')):
                result=run(d/'case.json',d/'runtime.json',d/'out')
            model.assert_not_called();self.assertEqual(result['status'],'blocked_missing_grades')
            self.assertFalse((d/'out/report.docx').exists());self.assertFalse(result['model_requested'])
            self.assertTrue((d/'out/STOP_REASON.txt').exists())

class MaterialCatalogTests(unittest.TestCase):
    @patch('report_agent.material_catalog._known_cases',return_value=({},{}))
    def test_new_materials_reclassify_stable_package_without_moving_sources(self,_):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);root=d/'materials';p=root/'2025-1数字逻辑';p.mkdir(parents=True)
            (p/'课程大纲.txt').write_text('课程资料')
            first=scan(root,d/'first',cache=d/'cache')
            grade=p/'成绩单.csv';text='学号,姓名,总评\n20250001,王同学,80\n';grade.write_text(text)
            second=scan(root,d/'second',previous=d/'first/catalog.json',cache=d/'cache')
            self.assertEqual(first['packages'][0]['id'],second['packages'][0]['id'])
            self.assertEqual(first['packages'][0]['status'],'blocked_missing_grades')
            self.assertEqual(second['packages'][0]['status'],'ready_layout')
            self.assertEqual(len(second['changes']['reclassified']),1)
            self.assertEqual(grade.read_text(),text)
            third=scan(root,d/'third',previous=d/'second/catalog.json',cache=d/'cache')
            self.assertFalse(third['changes']['changed'])
            self.assertEqual(len(third['changes']['unchanged']),1)

    @patch('report_agent.material_catalog._known_cases',return_value=({},{}))
    def test_graduation_is_deferred_and_loose_files_not_dropped(self,_):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);root=d/'materials';p=root/'毕业设计样本';p.mkdir(parents=True);(p/'学生成绩单.csv').write_text('anything');(root/'新增材料.txt').write_text('new')
            result=scan(root,d/'out',cache=d/'cache')
            self.assertEqual(result['status_counts'],{'deferred_graduation':1,'unassigned_materials':1})
            self.assertFalse(load(d/'out/batch_queue.json'))
            self.assertEqual(len(load(d/'out/file_inventory.json')),2)

    @patch('report_agent.material_catalog._known_cases',return_value=({},{}))
    def test_nested_archive_grade_is_found_without_unpacking_student_programs(self,_):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);root=d/'materials';p=root/'2025-1数字逻辑';p.mkdir(parents=True)
            inner=io.BytesIO()
            with zipfile.ZipFile(inner,'w') as z:z.writestr('成绩.csv','学号,姓名,总评\n20250001,王同学,80\n20250002,李同学,90\n');z.writestr('run.bat','do not execute')
            with zipfile.ZipFile(p/'资料.zip','w') as z:z.writestr('内层.zip',inner.getvalue())
            result=scan(root,d/'out',cache=d/'cache');package=result['packages'][0]
            self.assertEqual(package['status'],'ready_layout');self.assertEqual(package['scored_count'],2)
            self.assertEqual(package['primary_grade']['origin']['member_chain'],['内层.zip','成绩.csv'])
            self.assertFalse(list((d/'cache').rglob('run.bat')))

    @patch('report_agent.material_catalog._known_cases',return_value=({},{}))
    def test_office_lock_file_is_not_an_unreadable_grade(self,_):
        with tempfile.TemporaryDirectory() as tmp:
            d=Path(tmp);root=d/'materials';p=root/'2025-1数字逻辑';p.mkdir(parents=True)
            with zipfile.ZipFile(p/'资料.zip','w') as z:
                z.writestr('~$学生成绩单.xlsx',b'office temporary lock')
            result=scan(root,d/'out',cache=d/'cache');package=result['packages'][0]
            self.assertEqual(package['status'],'blocked_missing_grades')
            self.assertFalse(package['read_errors'])

if __name__=='__main__':unittest.main()
