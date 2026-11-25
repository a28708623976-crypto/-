"""Business rules transcribed from the supplied export, not a general formula engine."""
from datetime import datetime, timezone, timedelta
SH = timezone(timedelta(hours=8))
ASSET = {'全剧资产（含人物、场景、道具）', '人物资产', '场景资产', '道具资产'}

def text(v):
    if isinstance(v, list):
        return '、'.join((text(x) for x in v))
    return '' if v is None else str(v)

def members(v):
    return v if isinstance(v, list) else [v] if v else []

def stamp(v):
    if not v:
        return 0
    if isinstance(v, (int, float)):
        return v / 1000 if v > 100000000000.0 else v
    try:
        return datetime.fromisoformat(v).replace(tzinfo=SH).timestamp()
    except (ValueError, TypeError):
        return 0

def calculate(tables, now=None):
    now = now or datetime.now(SH)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    by = {t['name']: t for t in tables}

    def rows(name):
        return [r['values'] for r in by.get(name, {}).get('records', [])]
    projects = rows('项目总控表')
    tasks = rows('任务排期一览表')
    reviews = rows('交付审核日志')

    def unique(values):
        vals = list(dict.fromkeys((text(x) for x in values if x)))
        return vals[0] if len(vals) == 1 else vals if vals else None
    for name, task_name in [('交付审核日志', '任务排期一览表'), ('历史审核记录', '历史排期任务')]:
        for r in rows(name):
            ts = [t for t in rows(task_name) if r.get('提交任务') and t.get('任务详情') == r.get('提交任务') and set(members(r.get('提交人'))) & set(members(t.get('任务负责人')))]
            r['项目名称'] = unique((t.get('项目名称') for t in ts))
            r['集数'] = unique((t.get('集数') for t in ts))
            exact = [t for t in ts if t.get('项目名称') == r['项目名称'] and t.get('集数') == r['集数']]
            r['任务类型'] = unique((t.get('任务类型') for t in exact))
            complete = all((r.get(k) for k in ['提交人', '提交任务', '项目名称', '交付物存放路径', '交付物版本号', '审核人']))
            r['审核结果'] = ('待审核' if not r.get('审核意见') else '定稿' if r['审核意见'] == '✅' else '返修') if complete else ''
    for task_name, review_name in [('任务排期一览表', '交付审核日志'), ('历史排期任务', '历史审核记录')]:
        for t in rows(task_name):
            matches = [r for r in rows(review_name) if r.get('提交任务') and r.get('提交任务') == t.get('任务详情') and (r.get('项目名称') == t.get('项目名称'))]
            finals = [stamp(r.get('定稿时间')) for r in matches if r['审核结果'] == '定稿' and set(members(t.get('任务负责人'))).issubset(set(members(r.get('提交人'))))]
            completed = max(finals, default=0)
            t['实际完成时间'] = completed or None
            t['任务返修次数'] = sum((r['审核结果'] == '返修' for r in matches))
            t['任务情况'] = '待分配' if not t.get('任务负责人') else '已完成' if completed else '制作中'
            due = stamp(t.get('任务结束时间'))
            t['卡点情况'] = '✅ 已完成' if completed else '🚨 卡点' if due and today > due else '⏳ 推进中'
            t['交付时效差(天)'] = int((due - completed) / 86400) if due and completed else 0
            p = next((p for p in projects if p.get('项目名称') == t.get('项目名称')), {})
            t['所属业务'] = p.get('所属业务')
            t['项目类型'] = p.get('项目类型')
    for p in projects:
        ts = [t for t in tasks if t.get('项目名称') == p.get('项目名称')]
        for stage, types in [('资产', ASSET), ('生成', {'AI生成'}), ('后期', {'后期剪辑'})]:
            subset = [t for t in ts if t.get('任务类型') in types]
            total = len(subset)
            done = sum((t['任务情况'] == '已完成' for t in subset))
            p[stage + '总任务数'] = total
            p[stage + '已完成任务数'] = done
            p[stage + '进度'] = done / total if total else 0
        total = sum((p[s + '总任务数'] for s in ['资产', '生成', '后期']))
        done = sum((p[s + '已完成任务数'] for s in ['资产', '生成', '后期']))
        p['项目进度（总进度）'] = done / total if total else 0
        p['卡点情况'] = unique((t.get('任务类型') for t in ts if t['卡点情况'] == '🚨 卡点'))
    for name in ['发行排期', '社媒账号表']:
        for r in rows(name):
            actual = stamp(r.get('实际发布时间'))
            plan = stamp(r.get('预发布时间'))
            r['发布情况'] = '待发布' if not actual else '✅' if actual == plan else '提前发布✅' if actual < plan else '延期发布❌'
    for r in rows('作品库'):
        basic = all((r.get(k) for k in ['作品来源', '作者姓名', '载体', '题材', '评级', '撰稿开始时间', '作品名称（中文）']))
        draft = basic and bool(r.get('稿件定稿'))
        world = draft and bool(r.get('世界框架与背景'))
        people = world and bool(r.get('人物小传'))
        finished = people and r.get('台词本') and (r.get('载体') == '剧本' or r.get('改编剧本'))
        r['进度'] = '任务完结' if finished else '稿件+世界框+人物小传 定稿' if people else '稿件+世界框 定稿' if world else '稿件定稿' if draft else '撰稿中' if basic else '待开始'
    for r in rows('商务'):
        basic = all((r.get(k) for k in ['作者姓名', '载体', '题材', '开始撰稿日期'])) and (r.get('作品名称（中文）') or r.get('作品名称（英文）'))
        method = r.get('合作方式')
        pay = bool(r.get('买断/前置费用 支付方式')) and r.get('买断/前置费用 总金额') is not None if method in ['买断', '前置费用+纯利润分红'] else r.get('纯利润分红比例') is not None if method == '纯利润分红' else False
        r['进度'] = '已完成签署' if basic and method and pay and r.get('合同编号') and r.get('合同存放路径') else '已确认合作方式' if basic and method and pay else '在撰稿未确定商务合作方式' if basic and (not method) and (not r.get('合同编号')) else ''
    return tables
