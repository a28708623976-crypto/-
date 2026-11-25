import argparse
import json
import math
import re
from datetime import datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from app.db import Store
from app.logic import calculate, SH, stamp
ROOT = Path(__file__).resolve().parents[1]

def validate(table, values, current=None):
    fields = {f['name']: f for f in table['fields']}
    if not isinstance(values, dict):
        raise ValueError('字段格式错误')
    for k, v in values.items():
        f = fields.get(k)
        if not f or f['readonly']:
            raise ValueError('不可修改字段：' + k)
        if v is None or v == '':
            continue
        if f['type'] == 2:
            if isinstance(v, bool) or not isinstance(v, (float, int)) or (not math.isfinite(v)):
                raise ValueError(k + '必须是数字')
        elif f['type'] == 5:
            if not isinstance(v, str):
                raise ValueError(k + '日期格式错误')
            try:
                datetime.fromisoformat(v)
            except ValueError:
                raise ValueError(k + '日期格式错误')
        elif f['type'] in [4, 11, 18]:
            if not isinstance(v, list) or any((not isinstance(x, str) for x in v)):
                raise ValueError(k + '必须是列表')
        elif not isinstance(v, str):
            raise ValueError(k + '必须是文本')
        if len(json.dumps(v)) > 50000:
            raise ValueError('字段内容过长')
    merged = (current or {}) | values
    if table['name'] == '项目总控表' and (not merged.get('项目名称')):
        raise ValueError('请填写项目名称')
    if table['name'] == '任务排期一览表':
        if not all((merged.get(k) for k in ['项目名称', '任务详情', '任务类型'])):
            raise ValueError('请填写项目、任务详情和任务类型')
        if merged.get('开始时间') and merged.get('任务结束时间') and (stamp(merged['开始时间']) > stamp(merged['任务结束时间'])):
            raise ValueError('任务结束时间不能早于开始时间')
    if table['name'] == '交付审核日志' and merged.get('审核意见') == '✅':
        if not all((merged.get(k) for k in ['提交人', '提交任务', '交付物存放路径', '交付物版本号', '审核人'])):
            raise ValueError('定稿前请补全交付信息')
    return merged

def make_handler(store):

    class Handler(BaseHTTPRequestHandler):

        def response(self, status, data):
            body = json.dumps(data, ensure_ascii=False, allow_nan=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def trusted(self):
            host = self.headers.get('Host', '')
            origin = self.headers.get('Origin')
            return bool(re.fullmatch('(localhost|127\\.0\\.0\\.1)(:\\d+)?', host)) and (not origin or origin == 'http://' + host)

        def do_GET(self):
            if not self.trusted():
                return self.response(403, {'error': '仅允许本地访问'})
            path = urlparse(self.path).path
            if path == '/api/state':
                return self.response(200, {'tables': calculate(store.snapshot()), 'audit': store.audit(), 'timezone': 'Asia/Shanghai'})
            if path == '/api/export':
                return self.response(200, {'version': 1, 'tables': store.snapshot()})
            files = {'/': ('index.html', 'text/html'), '/app.js': ('app.js', 'text/javascript'), '/style.css': ('style.css', 'text/css')}
            if path not in files:
                return self.response(404, {'error': '未找到'})
            name, mime = files[path]
            body = (ROOT / 'static' / name).read_bytes()
            self.send_response(200)
            self.send_header('Content-Type', mime + '; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if not self.trusted():
                return self.response(403, {'error': '仅允许同源本地操作'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.response(415, {'error': '需要 JSON'})
            try:
                if not 0 < int(self.headers.get('Content-Length', 0)) <= 1000000:
                    raise ValueError('请求大小错误')
                p = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                if not isinstance(p, dict):
                    raise ValueError('请求格式错误')
                if urlparse(self.path).path != '/api/records':
                    return self.response(404, {'error': '未找到'})
                tables = store.snapshot()
                table = next((t for t in tables if t['id'] == p.get('table_id')), None)
                if not table:
                    raise ValueError('数据表不存在')
                if table['name'].startswith('历史'):
                    raise ValueError('历史数据只读')
                current = next((r for r in table['records'] if r['id'] == p.get('id')), None)
                values = p.get('values', {})
                merged = validate(table, values, current['values'] if current else None)
                if table['name'] == '项目总控表' and any((r['id'] != p.get('id') and r['values'].get('项目名称') == merged.get('项目名称') for r in table['records'])):
                    raise ValueError('项目名称已存在，请使用唯一名称')
                if table['name'] == '项目总控表' and current and (merged.get('项目名称') != current['values'].get('项目名称')):
                    if any((r['values'].get('项目名称') == current['values'].get('项目名称') for t in tables if t['name'] == '任务排期一览表' for r in t['records'])):
                        raise ValueError('项目已有排期任务，暂不支持改名；请保持关联名称一致')
                if table['name'] == '交付审核日志':
                    if not current:
                        if not all((merged.get(k) for k in ['提交人', '提交任务', '交付物存放路径', '交付物版本号', '审核人'])):
                            raise ValueError('请补全提交人、任务、交付路径、版本和审核人')
                        values['提交时间'] = datetime.now(SH).isoformat()
                    if merged.get('审核意见') == '✅':
                        probe = {'id': 'probe', 'values': merged.copy()}
                        table['records'].append(probe)
                        calculate(tables)
                        if not probe['values'].get('项目名称') or isinstance(probe['values']['项目名称'], list):
                            raise ValueError('任务与提交人无法唯一匹配项目，请先检查排期')
                        if not current or current['values'].get('审核意见') != '✅':
                            values['定稿时间'] = datetime.now(SH).isoformat()
                    elif current and current['values'].get('审核意见') == '✅':
                        values['定稿时间'] = None
                rid = store.save(table['id'], p.get('id'), values, p.get('revision'))
                self.response(200, {'id': rid})
            except (ValueError, TypeError, KeyError, json.JSONDecodeError) as e:
                self.response(400, {'error': str(e)})

        def log_message(self, *args):
            pass
    return Handler

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--port', type=int, default=8765)
    p.add_argument('--db', default='data/isleon.sqlite3')
    a = p.parse_args()
    store = Store(a.db)
    if not store.snapshot():
        store.import_tables(json.loads((ROOT / 'app/schema.json').read_text()))
    server = ThreadingHTTPServer(('127.0.0.1', a.port), make_handler(store))
    print(f'屿生代项目管理 http://127.0.0.1:{a.port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
if __name__ == '__main__':
    main()
