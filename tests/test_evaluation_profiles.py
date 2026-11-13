import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile, ZIP_DEFLATED
import xml.etree.ElementTree as ET
import openpyxl
from report_agent.common import ROOT, load, digest, save
from report_agent.aggregate_evidence import audit_workbook
from report_agent.evaluation_profiles import compile_plan, validate_profile, VERSION, MODEL
from report_agent.compute import calculate
from report_agent.readers import ingest
from report_agent.grades import parse_grades
from report_agent.tables import build
from report_agent.charts import jobs
from report_agent.pipeline import run, fingerprint, load_template


class AggregateEvidenceTests(unittest.TestCase):
    def fixture(self,folder,bad_cache=False,bad_formula=False):
        file=folder/'aggregate.xlsx';book=openpyxl.Workbook();ws=book.active
        ws.title='Goals';ws['A1']='本轮测试课程';ws['H1']=2
        ws['D2']='课程目标1';ws['E2']='课程目标2';ws['B3']='平时';ws['B6']='报告'
        for cell,value in {'D3':20,'D4':40,'D5':.5,'D6':40,'D7':60,'D8':.5,
                           'E6':40,'E7':100,'E8':1,'D10':.4,'E10':.6}.items():ws[cell]=value
        ws['D9']='=(D3*D5+D6*D8)/(D4*D5+D7*D8)' + ('+1' if bad_formula else '')
        ws['E9']='=(E3*E5+E6*E8)/(E4*E5+E7*E8)';ws['D11']='=D9*D10+E9*E10'
        ws['C12']='课程整体目标达成度较高为0.9。'
        book.save(file);book.close()
        # 仅测试夹具：同时写入公式与预先计算缓存，验证读取器会独立拒绝错误缓存。
        with ZipFile(file) as z:contents={name:z.read(name) for name in z.namelist()}
        ns={'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        tree=ET.fromstring(contents['xl/worksheets/sheet1.xml'])
        caches={'D9':.65 if bad_cache else .6,'E9':.4,'D11':.48}
        for cell in tree.findall('.//s:c',ns):
            if cell.attrib['r'] in caches:
                value=cell.find('s:v',ns)
                if value is None:value=ET.SubElement(cell,'{'+ns['s']+'}v')
                value.text=str(caches[cell.attrib['r']])
        contents['xl/worksheets/sheet1.xml']=ET.tostring(tree)
        with ZipFile(file,'w',ZIP_DEFLATED) as z:
            for name,data in contents.items():z.writestr(name,data)
        config={'id':'fixture','sheet':'Goals','course_name_cell':'A1','sample_count_cell':'H1',
            'header_row':2,'assessment_label_column':'B','goal_columns':{'G1':'D','G2':'E'},
            'components':[{'name':'平时','average_row':3,'maximum_row':4,'weight_row':5},
                          {'name':'报告','average_row':6,'maximum_row':7,'weight_row':8}],
            'mean_row':9,'goal_weight_row':10,'overall_cell':'D11','analysis_cell':'C12'}
        source={'id':'s','role':'current','path':str(file),'sha256':digest(file)}
        return source,config

    def test_class_mean_is_verified_without_individual_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            source,config=self.fixture(Path(tmp));result=audit_workbook(source,config,'测试课程',2,['G1','G2'])
            self.assertAlmostEqual(result['overall'],.48)
            self.assertEqual(result['goals']['G2']['mean'],.4)
            self.assertEqual(len(result['goals']['G2']['unentered_components']),1)
            self.assertFalse(result['individual_scores_available'])
            self.assertTrue(result['text_discrepancies'])
            self.assertNotIn('values',result['goals']['G1'])

    def test_wrong_cache_or_formula_or_course_or_cohort_rejected(self):
        for kwargs in ({'bad_cache':True},{'bad_formula':True}):
            with tempfile.TemporaryDirectory() as tmp:
                source,config=self.fixture(Path(tmp),**kwargs)
                with self.assertRaises(ValueError):audit_workbook(source,config,'测试课程',2,['G1','G2'])
        with tempfile.TemporaryDirectory() as tmp:
            source,config=self.fixture(Path(tmp))
            for course,count in [('其他课程',2),('测试课程',3)]:
                with self.assertRaises(ValueError):audit_workbook(source,config,course,count,['G1','G2'])

    def test_table_can_show_class_mean_without_fabricating_rate_or_scatter(self):
        case=load(ROOT/'examples/demo_course/case.json');facts=load(ROOT/'examples/demo_course/facts.json')
        grades=parse_grades(case,ingest(case),facts);facts['goal_rules']=[]
        aggregate={'goals':{g['id']:{'mean':.6,'source_refs':['aggregate:mean']} for g in facts['goals']},'audits':[]}
        calc=calculate(case,facts,grades,aggregate)
        self.assertIsNone(calc['goals']['G1']['mean']);self.assertIsNone(calc['goals']['G1']['rate'])
        self.assertEqual(calc['goals']['G1']['values'],{})
        table=build(facts,calc,load(ROOT/'templates/course/buu_course_r2/template_contract.json'))['T5-3'][0]
        self.assertEqual(table['rows'][1][5:9],['0.6','—','—','—'])
        chart=next(j for j in jobs(facts,calc) if j['id']=='goals')
        self.assertEqual(chart['kind'],'aggregate_summary');self.assertEqual(chart['evidence_status'],'aggregate_source')
        self.assertFalse(next(j for j in jobs(facts,calc) if j['id']=='goal_G1')['series']['values'])


class EvaluationProfileTests(unittest.TestCase):
    def fixture(self):
        facts={'metadata':{'course_name':'课程','term':'学期'},'review':{'approved':True,'reviewer':'fixture'},
            'goals':[{'id':'G1'},{'id':'G2'}],'points':[{'id':'P1','goal_weights':{'G1':None}}, {'id':'P2','goal_weights':{'G2':None}}],
            'assessments':[{'id':'A','name':'平时考核','raw_item_id':'I1','total_weight':.5},
                           {'id':'B','name':'设计报告','raw_item_id':'I2','total_weight':.5}],
            'items':[{'id':'I1','assessment_id':'A'},{'id':'I2','assessment_id':'B'}]}
        aggregate={'audits':[{'config_id':'matrix','goals':{
            'G1':{'components':[{'assessment':'平时成绩','maximum':20,'weight':.3}, {'assessment':'验收报告','maximum':50,'weight':.7}]},
            'G2':{'components':[{'assessment':'验收报告','maximum':50,'weight':1}]}}}]}
        profile={'goal_ids':['G1','G2'],'term':'学期','workbook_config_id':'matrix',
            'aliases':{'平时成绩':'平时考核','验收报告':'设计报告'},'threshold':.7,'id':'p','assumptions':['candidate']}
        return facts,aggregate,profile

    def test_compilation_preserves_official_facts_and_marks_proxy(self):
        facts,aggregate,profile=self.fixture();before=copy.deepcopy(facts)
        model=compile_plan(profile,{},facts,aggregate)
        self.assertEqual(facts,before);self.assertFalse(model['review']['approved'])
        self.assertEqual(model['evaluation_context']['mode'],'scenario_estimate')
        self.assertEqual(model['items'][0]['max_score'],20)
        self.assertEqual(model['items'][0]['goal_weights']['G2'],0)
        self.assertEqual(model['goal_rules'][0]['terms'],[{'item_id':'I1','coefficient':.3},{'item_id':'I2','coefficient':.35}])

    def test_ambiguous_alias_or_multiple_goal_point_rejected(self):
        facts,aggregate,profile=self.fixture();profile['aliases']['验收报告']='平时考核'
        with self.assertRaises(ValueError):compile_plan(profile,{},facts,aggregate)

    def test_known_course_rules_are_never_overwritten_by_reference(self):
        for kind in ('goal_threshold','goal_rule','item_scale','point_rule'):
            facts,aggregate,profile=self.fixture()
            if kind=='goal_threshold':facts['goals'][0]['threshold']=.6
            if kind=='goal_rule':facts['goal_rules']=[{'id':'G1','terms':[{'item_id':'I1','coefficient':1}]}]
            if kind=='item_scale':facts['items'][0]['max_score']=100
            if kind=='point_rule':facts['points'][0]['goal_weights']['G1']=.8
            with self.subTest(kind=kind),self.assertRaises(ValueError):compile_plan(profile,{},facts,aggregate)
        facts,aggregate,profile=self.fixture();facts['points'][0]['goal_weights']['G2']=None
        with self.assertRaises(ValueError):compile_plan(profile,{},facts,aggregate)

    def test_estimate_requires_explicit_flag_before_service_or_output(self):
        with tempfile.TemporaryDirectory() as tmp, patch('report_agent.pipeline.reference_identity',return_value=None),patch('report_agent.pipeline.Client') as client:
            with self.assertRaisesRegex(ValueError,'allow-estimates'):
                run('examples/demo_course/case.json','configs/runtime.example.json',str(Path(tmp)/'run'),evaluation_profile='unused.json')
            client.assert_not_called();self.assertFalse((Path(tmp)/'run').exists())

    def test_profile_content_is_part_of_fingerprint_but_not_source_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile=Path(tmp)/'profile.json';save(profile,{'threshold':.6})
            case=load(ROOT/'examples/demo_course/case.json');case['evaluation_profile']=str(profile)
            runtime=load(ROOT/'configs/runtime.example.json');template=load_template(runtime,'course')
            first,inputs1=fingerprint(case,runtime,template)
            save(profile,{'threshold':.7});second,inputs2=fingerprint(case,runtime,template)
            self.assertNotEqual(first,second);self.assertEqual(inputs1,inputs2)

    def test_wrong_input_binding_or_approval_cannot_be_used_as_proposal(self):
        with tempfile.TemporaryDirectory() as tmp:
            reference=Path(tmp)/'reference.pdf';reference.write_bytes(b'fixture reference')
            p={'version':VERSION,'model':MODEL,'status':'proposed','case_id':'c','group_id':'c','course_name':'课程',
               'source_hashes':{'s':'sha'},'template_id':'t','reference_library_sha256':'db','threshold':.7,
               'reference':{'source_path':str(reference),'sha256':digest(reference),'threshold_evidence':{'value':.7}},
               'blank_policy':'not_participating_in_this_proposal','point_policy':'existing_single_goal_identity','assumptions':['proposal']}
            case={'id':'c','course_name':'课程'};template={'contract':{'template_id':'t'}};refs={'sha256':'db'}
            validate_profile(p,case,template,refs,{'s':'sha'})
            with self.assertRaises(ValueError):validate_profile(p,case,template,refs,{'s':'changed'})
            p['approval']={'approved':True}
            with self.assertRaises(ValueError):validate_profile(p,case,template,refs,{'s':'sha'})

    def test_trial_render_marks_every_header_variant(self):
        from report_agent import render
        from docx import Document
        case=load(ROOT/'examples/demo_course/case.json');facts=load(ROOT/'examples/demo_course/facts.json')
        facts['evaluation_context']={'mode':'scenario_estimate','warning':'方案试算，含代理估计'}
        grades=parse_grades(case,ingest(case),facts);calc=calculate(case,facts,grades)
        template=load_template(load(ROOT/'configs/runtime.example.json'),'course')
        data=build(facts,calc,template['contract']);figures=[{**j,'status':'missing_data','reason':'test fixture'} for j in jobs(facts,calc)]
        with tempfile.TemporaryDirectory() as tmp:
            filename=Path(tmp)/'trial.docx'
            render.generate(template,facts,calc,data,figures,{k:['测试夹具'] for k in ('2.4','6.1','6.2','7')},filename)
            doc=Document(filename)
            for section in doc.sections:
                for header in (section.header,section.first_page_header,section.even_page_header):
                    self.assertIn('代理估计',header.paragraphs[0].text)
            for table in doc.tables:
                for cell in table.rows[-1].cells:
                    self.assertFalse(cell.paragraphs[0].paragraph_format.keep_with_next)
