"""Decode a local Feishu .base export. Never import credentials or identity documents."""
import base64
import gzip
import json
from pathlib import Path
HR_FIELDS = {'中文姓名', '英文姓名', '工号', '员工状态', '职位名称', '职级名称', '上级中文名', '入职日期', '转正日期', '合同开始日期', '合同到期日期', '雇员类型', '合同类型', '转正状态', '试用期（月）', '飞书用户'}
BLOCKED = ('密码', '证件', '身份证', '银行卡', '收款账号', '开户行', '支行', '所属银行', '公积金', '社保账号', '手机', '邮箱', '联系地址', '现居地址', '户口', '紧急联系人')

def allowed(table, name):
    return name in HR_FIELDS if table == '花名册' else not any((w in name for w in BLOCKED))

def decode(path):
    envelope = json.loads(Path(path).read_text())
    snapshots = json.loads(gzip.decompress(base64.b64decode(envelope['gzipSnapshot'])))
    tables = {}
    def value(v, mapping):
        if v is None:
            return None
        if isinstance(v, str):
            return mapping.get(v, v)
        if isinstance(v, list):
            if all((isinstance(x, dict) and 'text' in x for x in v)):
                return ''.join((x['text'] for x in v))
            return [value(x, mapping) for x in v]
        if isinstance(v, dict):
            if 'users' in v:
                return [u.get('name', u.get('enName', '未知成员')) for u in v['users']]
            if 'recordIds' in v:
                return v['recordIds']
            if 'text' in v:
                return v['text']
            return v
        return v
    excluded = []
    for snap in snapshots:
        s = snap['schema']
        d = s['data']
        t = d['table']
        tid = t['meta']['id']
        name = s['tableMap'][tid]['name']
        entry = tables.setdefault(tid, {'id': tid, 'name': name, 'fields': [], 'records': {}, 'views': []})
        fields = []
        field_options = {}
        for fid, f in t['fieldMap'].items():
            if not allowed(name, f['name']):
                excluded.append(name + '/' + f['name'])
                continue
            p = f.get('property', {})
            rule = p.get('optionsRule', {})
            target = rule.get('targetTable')
            target_field = rule.get('targetField')
            opts = p.get('options', [])
            if target:
                for other in snapshots:
                    ot = other['schema']['data']['table']
                    if ot['meta']['id'] == target:
                        opts = ot['fieldMap'].get(target_field, {}).get('property', {}).get('options', [])
                        break
            field_options[fid] = {o['id']: o['name'] for o in opts}
            fields.append({'id': fid, 'name': f['name'], 'type': f['type'], 'options': [o['name'] for o in opts], 'readonly': f['type'] in [19, 20, 1001, 1002, 1003, 1004], 'formula': p.get('formula', '')})
        entry['fields'] = fields
        entry['views'] = [{'name': v['name'], 'type': v['type']} for v in t['viewMap'].values()]
        for rid, row in d['recordMap'].items():
            entry['records'][rid] = {'id': rid, 'values': {f['name']: value((row.get(f['id']) or {}).get('value'), field_options.get(f['id'], {})) for f in fields}}
    return (list(tables.values()), sorted(set(excluded)))
