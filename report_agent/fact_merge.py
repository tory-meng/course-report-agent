"""保守合并逐批事实；保留字段级候选和证据，不凭出现次数裁决。"""
import copy
import json
import re
from .common import save, signature

MISSING = (None, '')

def text_key(value):
    # 中文排版换行可忽略；英文内部空格不删除（避免合并不同名称/代码）。
    value = re.sub(r'\s+', ' ', value).strip()
    return re.sub(r'(?<=[\u3400-\u9fff]) (?=[\u3400-\u9fff])', '', value)

def equivalent_key(field, value):
    value = text_key(value)
    if field == 'academic_year':
        m = re.fullmatch(r'(\d{4})\s*[-—–－/～~]\s*(\d{4})(?:学年)?', value)
        if m: return '-'.join(m.groups())
    if field == 'semester':
        m = re.fullmatch(r'第?([12一二])(?:学期)?', value)
        if m: return {'一':'1','二':'2'}.get(m[1], m[1])
    if field == 'term':
        m = re.fullmatch(r'(\d{4})[-—–－/～~](\d{4})(?:学年第?|-)([12一二])(?:学期)?', value)
        if m: return m[1]+'-'+m[2]+'-'+{'一':'1','二':'2'}.get(m[3],m[3])
    return value

def merge_value(field, left, right):
    """返回(可确定合并, 值)；不同文字论述、不同数值仍为冲突。"""
    if left in MISSING: return True, copy.deepcopy(right)
    if right in MISSING: return True, copy.deepcopy(left)
    if isinstance(left, bool) != isinstance(right, bool): return False, left
    if left == right: return True, copy.deepcopy(left)
    if isinstance(left,str) and isinstance(right,str):
        if equivalent_key(field,left) == equivalent_key(field,right): return True,left
        if field == 'teacher':
            a=set(filter(None,re.split(r'[/／、,，;；]',re.sub(r'\s+','',left))))
            b=set(filter(None,re.split(r'[/／、,，;；]',re.sub(r'\s+','',right))))
            if a and b and (a<=b or b<=a): return True,right if a<b else left
        return False,left
    if isinstance(left,dict) and isinstance(right,dict):
        result=copy.deepcopy(left)
        for key,value in right.items():
            if key == 'source_refs': result[key]=sorted(set(result.get(key,[])+value));continue
            ok,merged=merge_value(key,result.get(key),value)
            if not ok:return False,left
            result[key]=merged
        return True,result
    if field == 'goal_ids' and isinstance(left,list) and isinstance(right,list):
        # 不擅自扩大映射范围；仅消除次序差异。
        if set(left)==set(right):return True,left
    if field == 'terms' and isinstance(left,list) and isinstance(right,list):
        # 相同项目的系数必须一致；不同项目按局部事实合并，重复项目不接受。
        for rows in (left,right):
            if any(not isinstance(x,dict) or not x.get('item_id') for x in rows):return False,left
            if len({x['item_id'] for x in rows})!=len(rows):return False,left
        result=copy.deepcopy(left);lookup={x['item_id']:x for x in result}
        for row in right:
            key=row['item_id']
            if key not in lookup:result.append(copy.deepcopy(row));lookup[key]=result[-1]
            else:
                ok,value=merge_value(field,lookup[key],row)
                if not ok:return False,left
                lookup[key].update(value)
        return True,result
    return False,left

class FactMerger:
    def __init__(self, facts, evidence):
        self.facts=facts;self.evidence=evidence;self.fields={};self.conflicts={};self.resolved={}

    def add(self, record, task_id=''):
        kind=record['kind'];value=copy.deepcopy(record['value']);rid=value.get('id')
        if kind=='narratives':
            # 叙述是独立证据陈述，不是需要跨表关联的计算实体。相同内容去重；不同内容不共用模型临时ID。
            rid='N_'+signature([value.get('section'),text_key(value.get('text',''))])[:24]
            value['id']=rid
        existing=self.facts['metadata'] if kind=='metadata' else next((x for x in self.facts[kind] if x['id']==rid),None)
        if existing is None:
            existing={'id':rid};self.facts[kind].append(existing)
        existing['source_refs']=sorted(set(existing.get('source_refs',[])+value['source_refs']))
        quotes=existing.setdefault('quotes',{})
        for ref,quote in value['quotes'].items():
            quotes[ref]=self.evidence[ref]['text'] if ref in quotes and quotes[ref]!=quote else quote
        for field,val in value.items():
            if field in ('id','source_refs','quotes') or val in MISSING:continue
            key=(kind,rid,field);candidates=self.fields.setdefault(key,[])
            candidate={'value':copy.deepcopy(val),'source_refs':list(value['source_refs']),
                       'quotes':copy.deepcopy(value['quotes']),'task_id':task_id}
            if candidate not in candidates:candidates.append(candidate)
            merged=None;valid=True
            for c in candidates:
                ok,merged=merge_value(field,merged,c['value'])
                if not ok:valid=False;break
            if valid:
                existing[field]=merged;self.conflicts.pop(key,None)
                if len({signature(c['value']) for c in candidates})>1:
                    self.resolved[key]={'kind':kind,'id':rid,'field':field,'value':merged,
                                       'reason':'格式等价、已明确的包含关系或不矛盾的局部字段','candidates':candidates}
            else:
                # 值暂留首个，conflicts非空时禁止向下生成；绝非默认采信首个。
                existing[field]=copy.deepcopy(candidates[0]['value']);self.resolved.pop(key,None)
                self.conflicts[key]={'conflict_id':'conflict_'+signature([kind,rid,field])[:12],
                   'kind':kind,'id':rid,'field':field,'values':[c['value'] for c in candidates],
                   'candidates':candidates,'reason':'存在不等价值；须核对适用范围、来源版本或同ID是否指向不同实体'}
        self.facts['conflicts']=copy.deepcopy(list(self.conflicts.values()))

    def write_review(self, run, sources):
        source_lookup={s['id']:s for s in sources}
        conflicts=copy.deepcopy(list(self.conflicts.values()))
        for conflict in conflicts:
            for c in conflict['candidates']:
                c['locations']=[{'ref':r,'path':source_lookup.get(self.evidence[r].get('source_id',r.split(':')[0]),{}).get('path',''),
                                  'quote':c['quotes'][r]} for r in c['source_refs']]
        report={'status':'needs_review' if conflicts else 'merge_passed','conflict_count':len(conflicts),
                'auto_merged_count':len(self.resolved),'auto_merged':list(self.resolved.values()),'conflicts':conflicts,
                'policy':'不按多数票、不按最后一条、不借参考报告覆盖本轮事实；合并通过不等于人工审核通过。'}
        save(run/'fact_conflicts.json',report)
        lines=['事实合并核对','未解决冲突：'+str(len(conflicts)),'自动合并字段：'+str(len(self.resolved)),'']
        for c in conflicts:
            lines.append(f"{c['conflict_id']} | {c['kind']} | {c['id']} | {c['field']}")
            for n,v in enumerate(c['candidates'],1):
                lines.append(f"  候选{n}："+json.dumps(v['value'],ensure_ascii=False))
                for loc in v['locations']:lines.extend(['  来源：'+loc['path']+' | '+loc['ref'],'  原文：'+loc['quote']])
            lines.append('')
        (run/'fact_conflicts.txt').write_text('\n'.join(lines),encoding='utf-8')
        return report
