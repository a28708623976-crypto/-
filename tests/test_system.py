import copy
import json
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from pathlib import Path
from http.server import ThreadingHTTPServer
from datetime import datetime
from app.db import Store
from app.importer import allowed, decode
from app.logic import calculate, SH
from app.server import make_handler
ROOT = Path(__file__).resolve().parents[1]

class SystemTest(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / 'test.sqlite3')
        self.store.import_tables(json.loads((ROOT / 'app/schema.json').read_text()))

    def tearDown(self):
        self.tmp.cleanup()

    def t(self, name, tables=None):
        return next((t for t in tables or self.store.snapshot() if t['name'] == name))

    def put(self, name, values):
        return self.store.save(self.t(name)['id'], None, values)

    def fixture(self):
        self.put('项目总控表', {'项目名称': '测试项目', '所属业务': 'AI短剧'})
        self.put('任务排期一览表', {'项目名称': '测试项目', '任务详情': '测试项目 EP01', '任务类型': 'AI生成', '任务负责人': ['测试创作者'], '任务结束时间': '2026-11-20T18:00', '集数': 'EP01'})
        self.put('交付审核日志', {'提交任务': '测试项目 EP01', '提交人': ['测试创作者'], '审核人': ['测试审核人'], '交付物存放路径': '/test/V1.mp4', '交付物版本号': 'V1', '审核意见': '修正字幕', '定稿时间': None})

    def test_review_progress_chain_and_revisions(self):
        self.fixture()
        tables = calculate(self.store.snapshot(), datetime(2026, 11, 25, tzinfo=SH))
        r = self.t('交付审核日志', tables)['records'][0]
        task = self.t('任务排期一览表', tables)['records'][0]['values']
        self.assertEqual(r['values']['审核结果'], '返修')
        self.assertEqual(task['任务返修次数'], 1)
        self.assertEqual(task['任务情况'], '制作中')
        self.assertEqual(task['卡点情况'], '🚨 卡点')
        self.store.save(self.t('交付审核日志')['id'], r['id'], {'审核意见': '✅', '定稿时间': '2026-11-24T12:00'}, r['revision'])
        tables = calculate(self.store.snapshot())
        self.assertEqual(self.t('项目总控表', tables)['records'][0]['values']['项目进度（总进度）'], 1)
        self.assertEqual(self.t('任务排期一览表', tables)['records'][0]['values']['任务情况'], '已完成')
        with self.assertRaisesRegex(ValueError, '其他窗口'):
            self.store.save(self.t('交付审核日志')['id'], r['id'], {'审核意见': '冲突'}, r['revision'])
        self.assertEqual(Store(self.store.path).snapshot()[2]['records'][0]['revision'], 2)

    def test_blank_review_and_different_owner(self):
        self.fixture()
        r = self.t('交付审核日志')['records'][0]
        self.store.save(self.t('交付审核日志')['id'], r['id'], {'审核意见': None}, 1)
        tables = calculate(self.store.snapshot())
        self.assertEqual(self.t('交付审核日志', tables)['records'][0]['values']['审核结果'], '待审核')
        self.store.save(self.t('交付审核日志')['id'], r['id'], {'提交人': ['另一创作者'], '审核意见': '✅', '定稿时间': '2026-11-24T12:00'}, 2)
        tables = calculate(self.store.snapshot())
        self.assertEqual(self.t('项目总控表', tables)['records'][0]['values']['项目进度（总进度）'], 0)

    def test_stage_weighting_and_release(self):
        self.fixture()
        self.put('任务排期一览表', {'项目名称': '测试项目', '任务详情': '资产', '任务类型': '人物资产'})
        self.put('发行排期', {'预发布时间': '2026-11-25T12:00', '实际发布时间': '2026-11-25T11:00'})
        tables = calculate(self.store.snapshot())
        self.assertEqual(self.t('项目总控表', tables)['records'][0]['values']['资产总任务数'], 1)
        self.assertEqual(self.t('发行排期', tables)['records'][0]['values']['发布情况'], '提前发布✅')

    def test_sensitive_fields(self):
        for name in ['密码', '微信密码', '支付密码', '证件号码', '收款账号', '注册邮箱', '银行卡号']:
            self.assertFalse(allowed('账号表', name))
        self.assertFalse(allowed('花名册', '转正底薪'))
        self.assertTrue(allowed('花名册', '职位名称'))

    def test_import_scoped_options_and_duplicate_snapshot(self):
        import gzip, base64
        snapshots = []
        for n, label in enumerate(['AI短剧', 'AI漫剧']):
            snapshots.append({'schema': {'tableMap': {f't{n}': {'name': label}}, 'data': {'table': {'meta': {'id': f't{n}'}, 'fieldMap': {'f': {'name': '类型', 'type': 3, 'property': {'options': [{'id': 'same', 'name': label}]}}}, 'viewMap': {}}, 'recordMap': {'r': {'f': {'value': 'same'}}}}}})
        snapshots.append(copy.deepcopy(snapshots[0]))
        source = Path(self.tmp.name) / 'source.base'
        source.write_text(json.dumps({'gzipSnapshot': base64.b64encode(gzip.compress(json.dumps(snapshots).encode())).decode()}))
        tables, _ = decode(source)
        self.assertEqual(len(tables), 2)
        self.assertEqual(tables[0]['records']['r']['values']['类型'], 'AI短剧')
        self.assertEqual(tables[1]['records']['r']['values']['类型'], 'AI漫剧')

    def test_api_save_reopen_and_guards(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(self.store))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f'http://127.0.0.1:{server.server_port}'

        def req(path, data=None, origin=None):
            headers = {'Content-Type': 'application/json'}
            if origin:
                headers['Origin'] = origin
            r = urllib.request.Request(url + path, data=json.dumps(data).encode() if data is not None else None, headers=headers)
            try:
                with urllib.request.urlopen(r) as response:
                    return json.load(response)
            except urllib.error.HTTPError as e:
                e.close()
                raise
        try:
            p = self.t('项目总控表')
            created = req('/api/records', {'table_id': p['id'], 'values': {'项目名称': 'API 测试'}})
            data = req('/api/state')
            self.assertTrue(any((r['id'] == created['id'] for r in self.t('项目总控表', data['tables'])['records'])))
            with self.assertRaises(urllib.error.HTTPError):
                req('/api/records', {'table_id': p['id'], 'values': {'项目进度（总进度）': 1}})
            with self.assertRaises(urllib.error.HTTPError):
                req('/api/records', {'table_id': p['id'], 'values': {'项目名称': '跨站请求'}}, 'https://external.example')
            with self.assertRaises(urllib.error.HTTPError):
                req('/api/records', {'table_id': p['id'], 'values': {'项目名称': 'API 测试'}})
            self.assertEqual(len(req('/api/export')['tables']), 14)
            h = self.t('历史排期任务')
            with self.assertRaises(urllib.error.HTTPError):
                req('/api/records', {'table_id': h['id'], 'values': {'任务详情': '不可写'}})
            self.fixture()
            review = self.t('交付审核日志')['records'][0]
            tid = self.t('交付审核日志')['id']
            req('/api/records', {'table_id': tid, 'id': review['id'], 'revision': 1, 'values': {'审核意见': '✅'}})
            tables = req('/api/state')['tables']
            review = self.t('交付审核日志', tables)['records'][0]
            self.assertTrue(review['values']['定稿时间'])
            self.assertEqual(self.t('任务排期一览表', tables)['records'][0]['values']['任务情况'], '已完成')
            req('/api/records', {'table_id': tid, 'id': review['id'], 'revision': 2, 'values': {'审核意见': '需要返修'}})
            self.assertFalse(self.t('交付审核日志', req('/api/state')['tables'])['records'][0]['values']['定稿时间'])
        finally:
            server.shutdown()
            server.server_close()
            thread.join()
if __name__ == '__main__':
    unittest.main()
