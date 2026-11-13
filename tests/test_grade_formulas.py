import tempfile
import unittest
from pathlib import Path
import openpyxl
from report_agent.grade_formulas import audit,apply_verified,evaluate,expression_tree,linear_terms
from report_agent.grades import parse_grades
from report_agent.compute import calculate


class GradeFormulaTests(unittest.TestCase):
    def test_excel_rounding_and_whitelisted_arithmetic(self):
        self.assertEqual(evaluate(expression_tree('=ROUND(92.5,0)'),lambda _:0),93)
        self.assertEqual(evaluate(expression_tree('=ROUND(-2.5,0)'),lambda _:0),-3)
        self.assertEqual(linear_terms('=ROUND(0.2*F6+0.4*G6+0.4*H6,0)',6),({'F':.2,'G':.4,'H':.4},0))
        with self.assertRaises(ValueError):evaluate(expression_tree('=IMPORT("outside")'),lambda _:0)
        with self.assertRaises(ValueError):linear_terms('=F6*G6',6)
        with self.assertRaises(ValueError):linear_terms('=F7',6)

    def fixture(self,folder,bad=False,intermediate=False):
        header=['学号','姓名','平时考核','设计报告','验收答辩','考核成绩','成绩','成绩']
        rows=[header,['001','甲',92,92,89,91,91,91.2],['002','乙',60,79,60,70,68,68],['003','丙',86,70,83,77,78,78.8]]
        if bad:rows[-1][6]=81
        p=folder/'register.xlsx';book=openpyxl.Workbook();ws=book.active;ws.title='Sheet1'
        for row in rows:ws.append(row)
        for n in range(2,5):
            ws.cell(n,6,f'=ROUND(0.5*D{n}+0.5*E{n},0)')
            ws.cell(n,7,f'=ROUND(0.2*C{n}+0.4*D{n}+0.4*E{n},0)' if not intermediate else f'=ROUND(0.2*C{n}+0.8*F{n},0)')
            ws.cell(n,8,f'=0.2*C{n}+0.8*F{n}')
        book.save(p)
        source={'id':'s','role':'current','kind':'grades','path':str(p),'sha256':'test-fixture',
                'sheets':[{'name':'Sheet1','rows':rows,'formula_cells':[]}],
                'blocks':[{'id':'s:Sheet1:R1','text':' | '.join(header),'role':'current','source_id':'s'}]}
        return source

    def test_formulas_are_proof_and_aggregate_is_not_fourth_independent_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);source=self.fixture(folder);results=audit({},[source],folder)
            proof=results[0];self.assertEqual(proof['status'],'verified')
            self.assertEqual(proof['four_column_average_matches'],1)
            self.assertEqual([c['weight'] for c in proof['components']],[.2,.4,.4])
            self.assertEqual(proof['total_column'],7)
            facts={'metadata':{'course_name':'测试课程','total_max':100},'assessments':[],'items':[],
                   'goals':[],'points':[],'methods':[],'goal_rules':[],'assessment_goal_weights':[],'assessment_point_rules':[]}
            case={'id':'c','decision_policy':{'mode':'grade_first_minimal_v1'},'evaluation_policy':{'normal':'include'}}
            apply_verified(case,facts,[source],results)
            self.assertEqual(len(facts['items']),3)
            self.assertFalse(any(i['name']=='考核成绩' for i in facts['items']))
            grades=parse_grades(case,[source],facts);calc=calculate(case,facts,grades)
            parent=next(a for a in facts['assessments'] if a['name']=='设计报告')
            self.assertAlmostEqual(calc['assessments'][parent['id']]['mean'],(92+79+70)/3)
            self.assertNotIn('max_score',facts['items'][0])

    def test_mismatched_cache_and_intermediate_dependency_do_not_create_scheme(self):
        for kwargs in ({'bad':True},{'intermediate':True}):
            with tempfile.TemporaryDirectory() as tmp:
                source=self.fixture(Path(tmp),**kwargs)
                self.assertEqual(audit({},[source],Path(tmp))[0]['status'],'not_inferred')
