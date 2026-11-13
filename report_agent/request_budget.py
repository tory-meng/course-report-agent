"""完整请求预算：保留当前证据，逐步减少可选参考；不按字符猜 token 数。"""
import copy
import json


class InputBudgetError(ValueError):
    def __init__(self, audit, messages):
        self.audit = audit
        self.messages = messages
        super().__init__(f"完整输入 {audit['input_tokens']} tokens 超过可用预算 {audit['target_tokens']} "
                         f"（服务输入上限 {audit['max_input_tokens']}，输出预留 {audit['output_tokens']}）；"
                         "已压缩可选上下文，需要拆分当前证据；未提交本次生成请求")


def dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def evidence_view(block):
    """完整来源留在source_manifest。模型只看原文和必要列位置，不重复整表布局。"""
    result = {k:copy.deepcopy(block[k]) for k in
              ('id', 'source_id', 'role', 'text', 'table_id', 'row', 'context_only', 'context_label',
               'source_char_range', 'column_spans', 'column_span_format') if k in block}
    # Excel原文已有C1=等列坐标；Word保留单元格在原文中的范围及跨列信息。
    if block.get('cells') and any('anchor_column' in c for c in block['cells']):
        spans = []; seen = set(); cursor = 0
        for cell in block['cells']:
            anchor = cell.get('anchor_column', cell['column'])
            if anchor in seen: continue
            seen.add(anchor); text = cell.get('text', '')
            start = block['text'].find(text, cursor) if text else -1
            if start < 0: continue
            end = start + len(text); cursor = end
            spans.append([anchor, cell.get('grid_span', 1), start, end, cell.get('vertical_merge', '')])
        if spans:
            result['column_spans'] = spans
            result['column_span_format'] = ['column', 'span', 'text_start', 'text_end', 'vertical_merge']
    return result


def reference_view(lookup, chars=500, hits=2, notes=True):
    """只用于解释的副本；查询审计与数据库原文保持完整。"""
    if not isinstance(lookup, dict): return lookup
    out = {k:copy.deepcopy(lookup[k]) for k in ('policy', 'decision_rule') if k in lookup}
    out['scope'] = '参考资料检索结果仅辅助解释；不是本轮课程证据；未审核笔记不裁决数值矛盾。'
    out['hits'] = []
    for hit in lookup.get('hits', [])[:hits]:
        h = {k:copy.deepcopy(hit[k]) for k in ('reference_id', 'file', 'locator', 'view') if k in hit}
        text = str(hit.get('text', ''))
        h['text'] = text[:chars]
        if len(text) > chars: h['excerpt_truncated'] = True
        if notes and hit.get('interpretation_notes'):
            h['interpretation_notes'] = copy.deepcopy(hit['interpretation_notes'])
        out['hits'].append(h)
    return out


def _payload(messages):
    try:
        p = json.loads(messages[-1]['content'])
        return p if isinstance(p, dict) else None
    except (ValueError, TypeError, KeyError, IndexError): return None


def _with_payload(messages, payload):
    result = copy.deepcopy(messages)
    result[-1]['content'] = dumps(payload)
    return result


def _chars(messages, payload):
    # 分项是字符数（可加性明确），总量才是服务端实际token计数。
    groups = {'system':sum(len(m['content']) for m in messages if m.get('role') == 'system')}
    if payload:
        for key in ('evidence', 'template_targets', 'known_entities', 'reference_examples', 'retry_reference_examples', 'retry_instruction'):
            if key in payload: groups[key] = len(dumps(payload[key]))
        groups['other_prompt'] = len(dumps({k:v for k,v in payload.items() if k not in groups}))
    else: groups['user'] = sum(len(m['content']) for m in messages if m.get('role') != 'system')
    return groups


def fit_messages(client, messages, output_tokens):
    """所有削减均为确定性操作；证据、规则、目标字段和实体ID不删改。"""
    payload = _payload(messages)
    original = copy.deepcopy(messages)
    current = original
    if payload is not None: current = _with_payload(original, payload)
    actions = []; history = []
    stages = ('short_references', 'one_reference', 'omit_references', 'compact_template', 'omit_example')
    for stage in (None, *stages):
        if stage and payload is not None:
            changed = False
            if stage in ('short_references', 'one_reference', 'omit_references'):
                for key in ('reference_examples', 'retry_reference_examples'):
                    if key not in payload: continue
                    if stage == 'omit_references':
                        payload.pop(key); changed = True
                    else:
                        v = reference_view(payload[key], chars=250 if stage == 'short_references' else 160,
                                           hits=2 if stage == 'short_references' else 1, notes=False)
                        changed |= v != payload[key]; payload[key] = v
            elif stage == 'compact_template' and isinstance(payload.get('template_targets'), dict):
                # 保留字段/章节需求；表格排版和图表配置留给后续渲染，不在事实阶段重复传输。
                target = payload['template_targets']
                for key in ('tables', 'chart_inputs'):
                    if key in target: target.pop(key); changed = True
            elif stage == 'omit_example' and 'output_example_not_evidence' in payload:
                payload.pop('output_example_not_evidence'); changed = True
            if not changed: continue
            actions.append(stage); current = _with_payload(original, payload)
        elif stage: continue
        counts = client.count_tokens(current, output_tokens)
        hard = int(counts.get('max_input_tokens', client.identity.get('max_input_tokens', 8192)))
        context = counts.get('max_context_tokens', client.identity.get('max_context_tokens'))
        usable = min(hard, int(context) - output_tokens) if context is not None else hard
        reserve = max(0, int(client.config.get('input_safety_tokens', 128)))
        target = max(0, usable - reserve)
        n = int(counts['input_tokens']); history.append({'action':stage or 'initial', 'input_tokens':n})
        audit = {'input_tokens':n, 'max_input_tokens':hard, 'max_context_tokens':context,
                 'target_tokens':target, 'output_tokens':output_tokens, 'safety_tokens':reserve,
                 'actions':list(actions), 'counts':list(history), 'component_characters':_chars(current, payload)}
        if n <= target: return current, audit
    raise InputBudgetError(audit, current)


def split_evidence(blocks):
    """优先按完整行拆分，两个子批均保留已有表头；单段保留重叠上下文。"""
    contexts = [copy.deepcopy(b) for b in blocks if b.get('context_only')]
    headers = {b['table_id']:b for b in blocks if b.get('table_id') and b.get('row') == 1}
    content = [b for b in blocks if not b.get('context_only')]
    if not content: return None
    if len(content) > 1:
        total = sum(len(b['text']) for b in content); size = 0; mid = 1
        for i, b in enumerate(content[:-1], 1):
            size += len(b['text']); mid = i
            if size >= total / 2: break
        groups = [copy.deepcopy(content[:mid]), copy.deepcopy(content[mid:])]
    else:
        b = content[0]; text = b['text']
        if len(text) < 160: return None
        mid = len(text) // 2
        cuts = [i + 1 for i in range(max(1, mid - 100), min(len(text)-1, mid+100)) if text[i] in '。\n；|']
        cut = min(cuts, key=lambda i:abs(i-mid)) if cuts else mid
        overlap = min(60, len(text)//8)
        pieces = [(0, min(len(text), cut+overlap)), (max(0, cut-overlap), len(text))]
        groups = []
        view = evidence_view(b)
        for start, end in pieces:
            part = {k:copy.deepcopy(v) for k,v in b.items() if k not in ('cells', 'merged_cells')}
            offset = b.get('source_char_range', [0])[0]
            part.update(text=text[start:end], source_char_range=[offset+start,offset+end])
            if view.get('column_spans'):
                part['column_span_format'] = view['column_span_format']
                part['column_spans'] = [[col,span,max(a,start)-start,min(z,end)-start,merge]
                    for col,span,a,z,merge in view['column_spans'] if a<end and z>start]
            groups.append([part])
    children = []
    for group in groups:
        ids = {b['id'] for b in group}
        needed = list(contexts)
        for key in dict.fromkeys(b.get('table_id') for b in group):
            if key in headers and headers[key]['id'] not in {b['id'] for b in needed}:
                needed.append({**copy.deepcopy(headers[key]), 'context_only':True})
        children.append([copy.deepcopy(b) for b in needed if b['id'] not in ids] + group)
    return children
