"""JSON输出契约。约束结构不等于事实正确，来源和业务校验仍单独执行。"""
import copy

def obj(properties, required=()):
    return {'type':'object','properties':properties,'required':list(required),'additionalProperties':False}

def array(items, minimum=0, maximum=64):
    return {'type':'array','items':items,'minItems':minimum,'maxItems':maximum}

def text(maximum=600):return {'type':'string','minLength':1,'maxLength':maximum}
def nullable_number():return {'anyOf':[{'type':'number'},{'type':'null'}]}
def choice(values):return {'type':'string','enum':list(values)}
def nullable(schema):return {'anyOf':[schema,{'type':'null'}]}

ID={'type':'string','pattern':'^[A-Za-z0-9_.:-]{1,80}$'}
TERMS=array(obj({'item_id':ID,'coefficient':nullable_number()},['item_id','coefficient']))
WEIGHTS={'type':'object','additionalProperties':nullable_number()}
META_TEXT=['course_name','course_code','offering_code','assessment_form','college','major','teacher','class_name','grade','term','academic_year','semester','course_nature','course_category','report_date','standard_name','total_rule_text']
META={k:text() for k in META_TEXT}
META.update({k:nullable_number() for k in ['hours','credits','total_max','pass_score']})
META['evaluation_policy']=obj({k:nullable(choice(['include','exclude','zero'])) for k in ['normal','missing','absent','deferred','exempt']})
VALUES={
 'metadata':obj(META),
 'goals':obj({'id':ID,'text':text(1200),'threshold':nullable_number()},['id','text']),
 'points':obj({'id':ID,'requirement':text(),'text':text(1200),'course_weight':nullable_number(),'threshold':nullable_number(),'goal_weights':WEIGHTS,'aggregation':choice(['weighted_mean'])},['id','text']),
 'methods':obj({'id':ID,'name':text(80),'description':text(1200),'goal_ids':array(ID)},['id','name','description']),
 'assessments':obj({'id':ID,'name':text(80),'total_weight':nullable_number(),'terms':TERMS},['id','name']),
 'items':obj({'id':ID,'name':text(80),'assessment_id':ID,'max_score':nullable_number(),'score_basis':nullable(choice(['raw','converted'])),'total_weight':nullable_number(),'goal_weights':WEIGHTS,'converted_max':nullable_number()},['id','name','assessment_id']),
 'goal_rules':obj({'id':ID,'terms':TERMS},['id','terms']),
 'assessment_goal_weights':obj({'id':ID,'assessment_id':ID,'goal_id':ID,'weight':nullable_number()},['id','assessment_id','goal_id','weight']),
 'assessment_point_rules':obj({'id':ID,'assessment_id':ID,'point_id':ID,'terms':TERMS},['id','assessment_id','point_id','terms']),
 'narratives':obj({'id':ID,'section':choice(['6.1','6.2','7']),'text':text(1200)},['id','section','text'])
}

def extraction_schema(blocks,kinds=None):
    refs=list(dict.fromkeys(b['id'] for b in blocks))
    if not refs:raise ValueError('抽取任务没有证据')
    variants=[]
    for kind in kinds or VALUES:
        variants.append(obj({'kind':choice([kind]),'value':copy.deepcopy(VALUES[kind]),
                             'evidence_ids':array(choice(refs),1,len(refs))},['kind','value','evidence_ids']))
    return obj({'records':array({'anyOf':variants},0,48)},['records'])

def writing_schema(refs):
    return obj({'paragraphs':array(text(220),1,40),'source_refs':array(choice(refs),1,len(refs))},['paragraphs','source_refs'])

def validate_schema(answer,schema):
    try:from jsonschema import Draft202012Validator
    except ImportError as exc:raise RuntimeError('docagent缺少jsonschema，请执行 python -m pip install "jsonschema>=4.18,<5"') from exc
    error=next(Draft202012Validator(schema).iter_errors(answer),None)
    if error:
        location='.'.join(str(x) for x in error.absolute_path) or '$'
        raise ValueError('输出结构不符 '+location+'：'+error.message[:300])

def hydrate_records(answer,blocks):
    """从实际提供的片段回填来源，不让模型重写/拼接引文。"""
    lookup={}
    for b in blocks:
        # 同一长段的不同片段不能拼成一个伪引文。
        if b['id'] in lookup and lookup[b['id']]!=b['text']:raise ValueError('同批证据ID对应多个片段')
        lookup[b['id']]=b['text']
    records=[]
    for wire in answer['records']:
        value=copy.deepcopy(wire['value']);refs=list(dict.fromkeys(wire['evidence_ids']))
        if any(r not in lookup for r in refs):raise ValueError('模型选择了本批以外的证据')
        value['source_refs']=refs;value['quotes']={r:lookup[r] for r in refs}
        if isinstance(value.get('evaluation_policy'),dict):value['evaluation_policy']['source_refs']=refs
        records.append({'kind':wire['kind'],'value':value})
    return records
