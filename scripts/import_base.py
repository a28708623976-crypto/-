import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.importer import decode
from app.db import Store
p = argparse.ArgumentParser()
p.add_argument('source')
p.add_argument('--db', default='data/isleon.sqlite3')
a = p.parse_args()
tables, excluded = decode(a.source)
Store(a.db).import_tables(tables)
print(f"导入完成：{len(tables)} 张表，{sum((len(t['records']) for t in tables))} 条记录；排除 {len(excluded)} 个敏感字段。")
