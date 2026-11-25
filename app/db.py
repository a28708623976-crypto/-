import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone

class Store:

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript('CREATE TABLE IF NOT EXISTS tables_meta(id TEXT PRIMARY KEY, name TEXT, schema_json TEXT);\n            CREATE TABLE IF NOT EXISTS records(id TEXT, table_id TEXT, values_json TEXT, revision INTEGER DEFAULT 1, PRIMARY KEY(table_id,id));\n            CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY, table_id TEXT, record_id TEXT, action TEXT, before_json TEXT, after_json TEXT, at TEXT);')

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.path, timeout=15)
        c.row_factory = sqlite3.Row
        try:
            with c:
                yield c
        finally:
            c.close()

    def import_tables(self, tables):
        with self.connect() as c:
            if c.execute('SELECT count(*) FROM tables_meta').fetchone()[0]:
                raise ValueError('数据库已有数据，导入已停止；请使用新的数据库路径。')
            for t in tables:
                c.execute('INSERT INTO tables_meta VALUES(?,?,?)', (t['id'], t['name'], json.dumps({k: v for k, v in t.items() if k != 'records'}, ensure_ascii=False)))
                c.executemany('INSERT INTO records(id,table_id,values_json) VALUES(?,?,?)', [(r['id'], t['id'], json.dumps(r['values'], ensure_ascii=False)) for r in t['records'].values()])

    def snapshot(self):
        with self.connect() as c:
            tables = [json.loads(r[0]) for r in c.execute('SELECT schema_json FROM tables_meta ORDER BY rowid')]
            by = {t['id']: t for t in tables}
            for t in tables:
                t['records'] = []
            for r in c.execute('SELECT * FROM records ORDER BY rowid'):
                by[r['table_id']]['records'].append({'id': r['id'], 'revision': r['revision'], 'values': json.loads(r['values_json'])})
            return tables

    def save(self, tid, rid, values, revision=None):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old = c.execute('SELECT * FROM records WHERE table_id=? AND id=?', (tid, rid)).fetchone() if rid else None
            if rid and (not old):
                raise ValueError('记录不存在')
            if old and old['revision'] != revision:
                raise ValueError('记录已被其他窗口修改，请刷新后再保存')
            before = json.loads(old['values_json']) if old else {}
            after = before | values
            rid = rid or 'rec_' + uuid.uuid4().hex[:16]
            now = datetime.now(timezone.utc).isoformat()
            if old:
                c.execute('UPDATE records SET values_json=?,revision=revision+1 WHERE table_id=? AND id=?', (json.dumps(after, ensure_ascii=False), tid, rid))
            else:
                c.execute('INSERT INTO records(id,table_id,values_json) VALUES(?,?,?)', (rid, tid, json.dumps(after, ensure_ascii=False)))
            c.execute('INSERT INTO audit(table_id,record_id,action,before_json,after_json,at) VALUES(?,?,?,?,?,?)', (tid, rid, 'update' if old else 'create', json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), now))
            return rid

    def audit(self):
        with self.connect() as c:
            return [dict(r) for r in c.execute('SELECT id,table_id,record_id,action,at FROM audit ORDER BY id DESC LIMIT 100')]
