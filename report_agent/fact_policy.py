"""显式、有证据锚点的本轮修复策略；不将案例规则默认为所有课程规则。"""
import copy
import re
from .common import signature,save


def compact(s):return re.sub(r'\s+','',str(s))

class FactPolicy:
    def __init__(self, config, case, evidence):
        self.config=config;self.evidence=evidence;self.actions=[];self.seen=set()
        if config.get('case_id')!=case['id'] or config.get('course_name')!=case['course_name']:
            raise ValueError('事实修复策略不属于当前课程')
        for ref,quote in config['evidence_anchors'].items():
            if ref not in evidence or compact(quote) not in compact(evidence[ref]['text']):
                raise ValueError('修复依据改变或缺失：'+ref)
        self.excluded=set(config.get('excluded_sources',{}))

    def note(self, action, record, detail):
        self.actions.append({'action':action,'kind':record['kind'],'record':copy.deepcopy(record['value']),'detail':detail})

    def apply(self, record):
        r=copy.deepcopy(record);v=r['value'];kind=r['kind']
        source_ids={self.evidence[x].get('source_id',x.split(':')[0]) for x in v['source_refs']}
        if source_ids & self.excluded:
            self.note('quarantine_other_term',record,sorted(source_ids & self.excluded));return None
        if kind=='metadata':
            for field,source in self.config.get('metadata_sources',{}).items():
                if field not in v:continue
                if source_ids=={source}:self.seen.add(field)
                else:
                    self.note('metadata_scope',record,{'field':field,'authority_source':source,'removed_value':v.pop(field)})
            for field,reason in self.config.get('deferred_fields',{}).items():
                if field in v:self.note('defer_field',record,{'field':field,'value':v.pop(field),'reason':reason})
            # 所有业务字段均被移走时，不把该段误当作metadata的证据。
            if not set(v)-{'source_refs','quotes'}:return None
        if kind=='items':
            rule=self.config.get('item_names',{}).get(v['id'])
            if rule:
                if v.get('name') not in rule['aliases']:raise ValueError('考核项目名称出现未核对候选：'+v['id'])
                if v['name']!=rule['canonical']:
                    self.note('item_alias',record,rule)
                    v['name']=rule['canonical']
                    ref=rule['canonical_ref']
                    v['source_refs']=sorted(set(v['source_refs']+[ref]));v['quotes'][ref]=self.evidence[ref]['text']
        if kind in ('items','assessments') and self.config.get('clear_global_assessment_weights') and 'total_weight' in v:
            self.note('defer_global_weight',record,{'removed':v.pop('total_weight'),
                'reason':'10/10/30/50是大作业内部分项分值；不能直接当作课程总评权重。成绩表另有平时50%、期末50%。'})
        if kind=='narratives':
            allowed=set(self.config.get('narrative_allow_refs',[]))
            if not set(v['source_refs'])<=allowed:
                self.note('quarantine_non_effect_narrative',record,'课程内容/课次编号/计划不能直接当作已实施效果或持续改进');return None
        return r

    def finish(self, facts, sources, run):
        missing=set(self.config.get('metadata_sources',{}))-self.seen
        if missing:raise ValueError('指定的本轮来源没有抽取到字段：'+','.join(sorted(missing)))
        # 明确写明的教学方式从当前原文回填，不构造目标映射或实施效果。
        for method in self.config.get('methods',[]):
            ref=method['source_ref'];quote=self.evidence[ref]['text']
            record={'id':method['id'],'name':method['name'],'description':method['description'],
                    'source_refs':[ref],'quotes':{ref:quote}}
            if compact(method['description']) not in compact(quote):raise ValueError('教学方式描述与原文不符')
            if any(x['id']==record['id'] for x in facts['methods']):raise ValueError('新增教学方式ID已存在')
            facts['methods'].append(record)
        facts['data_quality_issues']=list(self.config.get('remaining_issues',[]))
        facts['review']={'approved':False,'reviewer':'','note':'程序修复与证据核对，不代替课程负责人审核；性质与目标计算条件尚待补充。'}
        audit={'policy_id':self.config['policy_id'],'policy_sha256':signature(self.config),'actions':self.actions,
               'remaining_issues':facts['data_quality_issues'],'automatically_approved':False}
        save(run/'fact_policy_audit.json',audit)
        # 未标成本轮的材料从新case清单移出；原文件不删除，不冒充上一轮证据。
        return [s for s in sources if s['id'] not in self.excluded]
