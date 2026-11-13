import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from report_agent.common import save,load,digest
from report_agent.compact_material_preparation import source_records,compare_official_totals,prepare,VERSION


class CompactMaterialPreparationTests(unittest.TestCase):
    def fixture(self,p):
        source=p/'grades.csv';source.write_text('frozen source')
        reference=p/'reference.docx';reference.write_text('reference threshold evidence')
        block={'id':'s:Sheet1:R1','text':'本课程：测试课程；课程目标：解释方法并完成实践；评价项目及成绩。','role':'current','source_id':'s'}
        source_data={'id':'s','path':str(source),'sha256':digest(source),'role':'current','kind':'grades','blocks':[block],
            'sheets':[{'name':'Sheet1','rows':[['课程名称：测试课程'],['学号','姓名','过程','期末','总评'],
                ['20240001','甲',80,90,88],['20240002','乙',80,'缓考',0]],'formula_cells':[]}]}
        ref=['s:Sheet1:R1']
        def record(**fields):return {**fields,'source_refs':ref}
        case={'id':'c','group_id':'cg','course_name':'测试课程','report_type':'course',
            'sources':[{'id':'s','path':str(source),'role':'current','kind':'grades'}],
            'evaluation_policy':{'normal':'include','missing':'exclude','absent':'exclude','deferred':'exclude','exempt':'exclude','source_refs':ref}}
        design={'version':VERSION,'case_id':'c','source_hashes':{'s':digest(source)},
            'metadata':record(course_name='测试课程',term='2024-2025-1',total_max=100),
            'goals':[record(id='G1',text='解释方法并完成实践')],
            'points':[record(id='P1',requirement='本课学习表现',text='本课学习表现',goal_weights={'G1':1})],
            'methods':[record(id='M1',name='课堂讲授',description='结合本课内容说明方法。')],
            'assessments':[record(id='A1',name='过程'),record(id='A2',name='期末')],
            'items':[record(id='I1',name='过程',assessment_id='A1',max_score=100),record(id='I2',name='期末',assessment_id='A2',max_score=100)],
            'mappings':[{'source_id':'s','sheet':'Sheet1','header_row':2,'student_id':1,'name':2,'total':5,'items':{'I1':3,'I2':4}}],
            'evaluation':{'item_maxima':{'I1':100,'I2':100},'item_total_weights':{'I1':.5,'I2':.5},
                'allocations':{'I1':{'G1':1},'I2':{'G1':1}},'point_goal_weights':{'P1':{'G1':1}},'point_course_weights':{'P1':.3},
                'assumptions':['分项及观测点关系为本次依据课程材料设计；不改登记总评。']},
            'reference':{'source_path':str(reference),'sha256':digest(reference),'file':'reference.docx',
                'threshold_evidence':{'value':.7,'quote':'阈值0.7','reference_id':'r1'}},
            'comparison':{'official_total_policy':'preserve_registered','round_digits':0}}
        save(p/'case.json',case);save(p/'runtime.json',{'template_registry':'unused'});save(p/'design.json',design)
        return case,design,source_data

    def test_bound_design_preserves_official_total_despite_component_mismatch(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);case,design,source=self.fixture(p)
            with patch('report_agent.compact_material_preparation.case_preflight',return_value={'status':'passed'}),\
                 patch('report_agent.compact_material_preparation.ingest',return_value=[source]),\
                 patch('report_agent.compact_material_preparation.reference_identity',return_value={'sha256':'library','db':str(p/'ref.db')}),\
                 patch('report_agent.compact_material_preparation.rank_references',return_value=[]),\
                 patch('report_agent.compact_material_preparation.load_template',return_value={'contract':{'template_id':'test-template'}}):
                result=prepare(p/'case.json',p/'runtime.json',p/'design.json',p/'out')
            prepared=load(result);grades=load(p/'out/normalized_grades.preview.json');audit=load(p/'out/registered_component_comparison.json')
            self.assertEqual(grades['students'][0]['total'],88)
            self.assertEqual(audit['rows'][0]['designed_component_total'],85)
            self.assertEqual(audit['mismatch_count'],1)
            self.assertFalse(prepared['evaluation_design']['enabled'])
            self.assertEqual(prepared['grade_mapping_policy']['mode'],'explicit_source_design_v1')
            self.assertFalse(load(p/'out/facts.source_draft.json')['review']['approved'])
            self.assertFalse(load(p/'out/evaluation_profile.proposed.json')['approval']['approved'])

    def test_external_or_false_quote_is_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            _,design,source=self.fixture(Path(d))
            design['goals'][0]['quotes']={'s:Sheet1:R1':'不存在的原文'}
            with self.assertRaisesRegex(ValueError,'引用不能定位'):source_records(design,[source],'测试课程')
            design['goals'][0]['source_refs']=['foreign:P1']
            with self.assertRaisesRegex(ValueError,'本轮有效引用'):source_records(design,[source],'测试课程')

    def test_changed_source_hash_blocks_preparation_before_ingest(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);self.fixture(p);(p/'grades.csv').write_text('mutated')
            with patch('report_agent.compact_material_preparation.ingest') as ingest:
                with self.assertRaisesRegex(ValueError,'SHA'):prepare(p/'case.json',p/'runtime.json',p/'design.json',p/'out')
                ingest.assert_not_called()

    def test_source_design_cannot_grant_human_approval(self):
        with tempfile.TemporaryDirectory() as d:
            _,design,source=self.fixture(Path(d));design['review']={'approved':True,'reviewer':'automated'}
            with self.assertRaisesRegex(ValueError,'人工批准'):source_records(design,[source],'测试课程')


if __name__=='__main__':unittest.main()
