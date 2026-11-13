"""输入可直接证伪的任务约束，不将代理参考答案差异当硬错误。"""
import copy
import json
import unittest

from training.task_metrics import diagnose


def object_schema(properties, required):
    return {'type':'object','properties':properties,'required':required,'additionalProperties':False}


def string_schema(values=None):
    return {'type':'string',**({'enum':values} if values else {'minLength':1})}


def array_schema(items,minimum=0,maximum=64):
    return {'type':'array','items':items,'minItems':minimum,'maxItems':maximum}


def task_row(kind,data,answer,schema,section=None):
    return {'id':'constraint-fixture','group_id':'fixture-course','task_kind':kind,'section_id':section,
            'messages':[{'role':'user','content':json.dumps(data,ensure_ascii=False)},
                        {'role':'assistant','content':json.dumps(answer,ensure_ascii=False)}],
            'review':{'approved':False,'reviewer':'','answer_isolation_checked':False},
            'evidence_refs':['s:P1','s:P2','s:P3'],'output_schema':schema}


def grade_row(other_same_course=False):
    source_ids=['S_main','S_other','S_attendance']
    source_sheet=object_schema({'source_id':string_schema(source_ids),'sheet':string_schema()},['source_id','sheet'])
    cell=object_schema({'source_id':string_schema(source_ids),'sheet':string_schema(),
                        'column':{'type':'integer','minimum':1}},['source_id','sheet','column'])
    component=copy.deepcopy(cell);component['properties']['item_id']=string_schema(['I_usual','I_final'])
    component['required'].append('item_id')
    schema=object_schema({'status':string_schema(['selected','blocked_identity_conflict']),
         'official_total':{'anyOf':[cell,{'type':'null'}]},'components':array_schema(component),
         'roster_only':array_schema(source_sheet),'excluded_sources':array_schema(string_schema(source_ids)),
         'reason':string_schema()},['status','official_total','components','roster_only','excluded_sources','reason'])
    def source(sid,title,kind='grades'):
        return {'source_id':sid,'sha256':'sha-'+sid,'file_name':('成绩单.xls' if sid=='S_main' else '补充记分册.xls'),
                'role':'current','kind':kind,'tables':[{'sheet':'Sheet1','registered_count':1,'numeric_total_count':1,
                'header_rows':[{'row':1,'cells':['2024-2025学年第1学期成绩登记表']},
                               {'row':2,'cells':['课程名称：'+title]},
                               {'row':3,'cells':['序号','学号','姓名','平时成绩','期末成绩','总评成绩']}]}]}
    main=source('S_main','测试课程');other=source('S_other','测试课程' if other_same_course else '其他课程')
    attendance=source('S_attendance','测试课程','document');attendance['file_name']='考勤表.xls'
    attendance['tables'][0].update(numeric_total_count=0)
    data={'task':'grade_source_selection_v1','course_name':'测试课程','term':'2024-2025-1',
          'instruction':'选择正式总评与独立分项。不同课程抬头必须有本文件哈希绑定的用户确认；随机公式分项不得使用。',
          'sources':[main,other,attendance],'confirmed_identity_overrides':{},'formula_risks':[],
          'requested_items':[{'id':'I_usual','name':'平时成绩'},{'id':'I_final','name':'期末成绩'}]}
    answer={'status':'selected','official_total':{'source_id':'S_main','sheet':'Sheet1','column':6},
            'components':[{'item_id':'I_usual','source_id':'S_main','sheet':'Sheet1','column':4},
                          {'item_id':'I_final','source_id':'S_main','sheet':'Sheet1','column':5}],
            'roster_only':[],'excluded_sources':['S_other','S_attendance'],'reason':'使用同课正式成绩登记。'}
    return task_row('grade_source_selection',data,answer,schema)


def teaching_row(gap=True):
    methods=[{'id':'M1','name':'课堂讲授','description':'通过案例讲授本课基本原理。','source_refs':['s:P1']},
             {'id':'M2','name':'小组协作','description':'小组共同完成本课任务并展示成果。','source_refs':['s:P2']},
             {'id':'M3','name':'课堂讨论','description':'组织分组讨论，交流共同任务的结果。','source_refs':['s:P3']}]
    goals=[{'id':'G1','text':'学生参与协作学习，具有团队意识，能够展示成果并有效交流。','source_refs':['s:P2']}]
    reason=string_schema();reason['maxLength']=160
    if gap:
        data={'task':'teaching_goal_selection_v1','uncovered_goals':goals,'available_methods':methods,
              'instruction':'选择直接支持本目标的活动，最少一项最多两项，没有依据可以为空。'}
        item=object_schema({'goal_id':string_schema(['G1']),'method_ids':array_schema(string_schema(['M1','M2','M3']),0,2),
                            'reason':reason},['goal_id','method_ids','reason'])
        schema=object_schema({'repairs':array_schema(item,1,1)},['repairs'])
        answer={'repairs':[{'goal_id':'G1','method_ids':['M2'],'reason':'小组共同完成任务并展示结果，提供协作与交流练习。'}]}
        return task_row('teaching_design_gap_review',data,answer,schema)
    data={'methods':methods,'goals':goals,'task':'每种教学方式须且仅须一行，只关联有活动依据的目标。'}
    item=object_schema({'method_id':string_schema(['M1','M2','M3']),'goal_ids':array_schema(string_schema(['G1']),0,3),
                        'reason':reason},['method_id','goal_ids','reason'])
    schema=object_schema({'links':array_schema(item,3,3)},['links'])
    answer={'links':[{'method_id':'M1','goal_ids':[],'reason':'讲授基本原理，缺少团队协作活动。'},
                     {'method_id':'M2','goal_ids':['G1'],'reason':'小组共同完成任务并展示结果。'},
                     {'method_id':'M3','goal_ids':['G1'],'reason':'分组讨论共同任务的结果。'}]}
    return task_row('teaching_design',data,answer,schema)


def writing_row():
    methods=[{'id':'M1','name':'课堂讲授','description':'结合案例讲解本课原理。','source_refs':['s:P1']},
             {'id':'M2','name':'小组协作','description':'组织小组讨论并汇报共同完成的任务。','source_refs':['s:P2']}]
    data={'task':'course_section_v1','section_id':'2.4','course_name':'测试课程','methods':methods,
          'instruction':'按输入顺序每种教学方式写一段，paragraphs不加序号或方式标题，名称由宿主添加。'}
    schema=object_schema({'paragraphs':array_schema(string_schema(),2,2),
                          'source_refs':array_schema(string_schema(['s:P1','s:P2']),1,2)},['paragraphs','source_refs'])
    answer={'paragraphs':['通过案例讲解本课原理。','组织小组讨论并交流任务成果。'],'source_refs':['s:P1','s:P2']}
    return task_row('write_section',data,answer,schema,'2.4')


def answer_of(row):
    return json.loads(row['messages'][-1]['content'])


class TaskMetricConstraintTests(unittest.TestCase):
    def assert_hard_error(self,row,actual):
        issues,_=diagnose(row,actual)
        self.assertTrue(issues,'输入可证伪的错误未进入任务约束问题列表')

    def test_selected_grade_requires_nonempty_total(self):
        row=grade_row();actual=answer_of(row);actual['official_total']=None
        self.assert_hard_error(row,actual)

    def test_grade_sheet_and_column_must_exist_in_input(self):
        for changes in ({'sheet':'Ghost'},{'column':999}):
            with self.subTest(changes=changes):
                row=grade_row();actual=answer_of(row);actual['official_total'].update(changes)
                self.assert_hard_error(row,actual)

    def test_exact_duplicate_component_is_not_hidden_by_set_conversion(self):
        row=grade_row();actual=answer_of(row);actual['components'].append(copy.deepcopy(actual['components'][0]))
        self.assert_hard_error(row,actual)

    def test_official_source_cannot_also_be_excluded(self):
        row=grade_row();actual=answer_of(row);actual['excluded_sources'].append('S_main')
        self.assert_hard_error(row,actual)

    def test_unconfirmed_other_course_scores_are_not_usable(self):
        row=grade_row();actual=answer_of(row);actual['official_total']['source_id']='S_other'
        actual['excluded_sources'].remove('S_other')
        self.assert_hard_error(row,actual)

    def test_valid_identity_override_must_match_source_sha(self):
        row=grade_row();data=json.loads(row['messages'][-2]['content'])
        data['confirmed_identity_overrides']={'S_other':{'sha256':'wrong-sha','confirmation':'该表确属本课'}}
        row['messages'][-2]['content']=json.dumps(data,ensure_ascii=False)
        actual=answer_of(row);actual['official_total']['source_id']='S_other';actual['excluded_sources'].remove('S_other')
        self.assert_hard_error(row,actual)

    def test_random_generated_component_column_is_rejected(self):
        row=grade_row(other_same_course=True);data=json.loads(row['messages'][-2]['content'])
        data['formula_risks']=[{'source_id':'S_other','sheet':'Sheet1',
            'unsafe_score_cells':{'D4':{'reason':'volatile_random_function','formula':'=RAND()*100'}}}]
        row['messages'][-2]['content']=json.dumps(data,ensure_ascii=False)
        actual=answer_of(row);actual['components'][0]['source_id']='S_other';actual['excluded_sources'].remove('S_other')
        self.assert_hard_error(row,actual)

    def test_non_grade_source_cannot_supply_total(self):
        row=grade_row();actual=answer_of(row);actual['official_total']['source_id']='S_attendance'
        actual['excluded_sources'].remove('S_attendance')
        self.assert_hard_error(row,actual)

    def test_blank_roster_cannot_supply_total(self):
        row=grade_row(other_same_course=True);data=json.loads(row['messages'][-2]['content'])
        data['sources'][1]['tables'][0]['numeric_total_count']=0
        row['messages'][-2]['content']=json.dumps(data,ensure_ascii=False)
        actual=answer_of(row);actual['official_total']['source_id']='S_other';actual['excluded_sources'].remove('S_other')
        self.assert_hard_error(row,actual)

    def test_production_method_rows_must_be_unique_and_complete(self):
        row=teaching_row(False);actual=answer_of(row)
        actual['links']=[copy.deepcopy(actual['links'][0]) for _ in actual['links']]
        self.assert_hard_error(row,actual)

    def test_lecture_without_collaboration_cannot_support_team_goal(self):
        row=teaching_row();actual=answer_of(row)
        actual['repairs'][0].update(method_ids=['M1'],reason='讲授知识即可形成团队合作能力。')
        self.assert_hard_error(row,actual)

    def test_explicit_no_method_title_instruction_is_checked(self):
        row=writing_row();actual=answer_of(row)
        actual['paragraphs'][0]='课堂讲授：'+actual['paragraphs'][0]
        self.assert_hard_error(row,actual)

    def test_another_grounded_method_is_not_an_error_just_for_reference_difference(self):
        row=teaching_row();actual=answer_of(row)
        actual['repairs'][0].update(method_ids=['M3'],reason='分组讨论共同任务的结果，练习团队交流。')
        issues,_=diagnose(row,actual)
        self.assertEqual(issues,[])

    def test_reasonable_paraphrase_is_not_required_to_match_exactly(self):
        row=writing_row();actual=answer_of(row)
        actual['paragraphs']=['围绕本课原理开展案例讲解。','通过小组讨论交流共同任务的成果。']
        issues,_=diagnose(row,actual)
        self.assertEqual(issues,[])

    def test_explicit_task_division_can_support_team_goal(self):
        row=teaching_row();data=json.loads(row['messages'][-2]['content'])
        data['available_methods'][0]['description']='组织成果展示并记录任务分工。'
        row['messages'][-2]['content']=json.dumps(data,ensure_ascii=False)
        actual=answer_of(row);actual['repairs'][0].update(method_ids=['M1'],reason='按任务分工展示成果，提供协作与交流练习。')
        issues,_=diagnose(row,actual);self.assertEqual(issues,[])

    def test_only_random_total_is_not_a_valid_reason_to_force_selection(self):
        row=grade_row();data=json.loads(row['messages'][-2]['content'])
        data['formula_risks']=[{'source_id':'S_main','sheet':'Sheet1','unsafe_score_cells':{'F4':{'formula':'=RAND()*100'}}}]
        row['messages'][-2]['content']=json.dumps(data,ensure_ascii=False)
        actual=answer_of(row);actual.update(status='blocked_identity_conflict',official_total=None,components=[],
            reason='当前同课总评列由随机公式生成，不能使用。')
        issues,_=diagnose(row,actual);self.assertEqual(issues,[])

    def test_invalid_grade_coordinate_also_blocks_candidate_preparation(self):
        from training.preparation import precheck
        row=grade_row();answer=answer_of(row);answer['official_total']['column']=999
        row['messages'][-1]['content']=json.dumps(answer,ensure_ascii=False);row['evidence_available']=row['evidence_refs']
        with self.assertRaisesRegex(ValueError,'列号'):precheck(row)

    def test_missing_hashes_cannot_confirm_other_course_identity(self):
        row=grade_row();data=json.loads(row['messages'][-2]['content'])
        data['sources'][1].pop('sha256');data['confirmed_identity_overrides']={'S_other':{'confirmation':'该表确属本课'}}
        row['messages'][-2]['content']=json.dumps(data,ensure_ascii=False)
        actual=answer_of(row);actual['official_total']['source_id']='S_other';actual['excluded_sources'].remove('S_other')
        self.assert_hard_error(row,actual)

    def test_valid_hash_bound_confirmation_allows_real_title_typo(self):
        row=grade_row();data=json.loads(row['messages'][-2]['content']);valid_hash='a'*64
        data['sources'][1]['sha256']=valid_hash
        data['confirmed_identity_overrides']={'S_other':{'sha256':valid_hash,'confirmation':'该表确属本课，抬头未改'}}
        row['messages'][-2]['content']=json.dumps(data,ensure_ascii=False)
        actual=answer_of(row);actual['official_total']['source_id']='S_other';actual['excluded_sources'].remove('S_other')
        self.assertEqual(diagnose(row,actual)[0],[])

    def test_null_total_and_missing_sheet_fail_precheck_with_clear_value_error(self):
        from training.preparation import precheck
        for coordinate in (None,{'source_id':'S_main','sheet':'Ghost','column':6}):
            row=grade_row();answer=answer_of(row);answer['official_total']=coordinate
            row['messages'][-1]['content']=json.dumps(answer,ensure_ascii=False);row['evidence_available']=row['evidence_refs']
            with self.subTest(coordinate=coordinate),self.assertRaises(ValueError):precheck(row)

    def test_checking_whether_students_have_mastered_is_a_future_plan(self):
        row=writing_row();row['section_id']='6.1';actual=answer_of(row)
        actual['paragraphs']=['后续将检验学生是否已经掌握基本概念。','计划安排小组讨论并评价成果。']
        self.assertEqual(diagnose(row,actual)[0],[])

    def test_future_progress_question_also_passes_candidate_precheck(self):
        from training.preparation import precheck
        row=writing_row();row['section_id']='6.1';answer=answer_of(row)
        answer['paragraphs']=['后续将检验学生是否已经提高实践能力。','计划安排小组讨论并评价成果。']
        row['messages'][-1]['content']=json.dumps(answer,ensure_ascii=False);row['evidence_available']=row['evidence_refs']
        self.assertTrue(precheck(row)['schema_valid'])

    def test_past_achievement_is_still_rejected_in_a_future_plan(self):
        row=writing_row();row['section_id']='6.1';actual=answer_of(row)
        actual['paragraphs']=['后续将安排讨论，学生已经掌握全部概念。','计划安排小组讨论并评价成果。']
        self.assert_hard_error(row,actual)


if __name__=='__main__':unittest.main()
