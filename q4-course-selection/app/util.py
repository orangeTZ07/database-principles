"""通用工具：时间、友好错误提示、课表网格、SVG 图表。"""
from datetime import datetime

from markupsafe import Markup, escape

WEEKDAYS = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]
PERIOD_TIMES = {1: "08:00", 2: "08:55", 3: "10:00", 4: "10:55", 5: "14:00", 6: "14:55",
                7: "16:00", 8: "16:55", 9: "19:00", 10: "19:55", 11: "20:50", 12: "21:45"}
ROLE_NAMES = {"admin": "系统管理员", "research": "教研科", "academic": "教务科",
              "teacher": "教师", "student": "学生"}


def now_str(app=None):
    from flask import current_app
    app = app or current_app
    fn = app.config.get("NOW")
    dt = fn() if fn else datetime.now()
    return dt.strftime("%Y-%m-%d %H:%M")


def friendly_db_error(e):
    """把 MySQL 服务器错误转成面向用户的中文提示。
    触发器 / 存储过程用 SIGNAL 抛出的业务提示（错误号 1644）原样返回。"""
    code = e.args[0] if e.args and isinstance(e.args[0], int) else None
    msg = str(e.args[1]) if len(e.args) > 1 else str(e)
    if code == 1644:
        return msg
    if code == 1062:
        return "保存失败：该记录已存在（违反唯一性约束）"
    if code in (1451, 1452, 1216, 1217):
        return "操作失败：存在关联数据（被引用或引用对象不存在）"
    if code == 3819:
        return "保存失败：数据不满足约束条件，请检查输入"
    if code in (1048, 1364):
        return "保存失败：必填项不能为空"
    if code in (1265, 1366, 1292, 1406, 1264):
        return "保存失败：输入的值格式不正确或超出允许范围"
    return msg


def pct(cur, prev):
    """同比/环比变化率，基数为 0 或缺失时返回 None。"""
    if prev in (None, 0) or cur is None:
        return None
    return round((cur - prev) * 100.0 / prev, 1)


def prev_term_id(term_id):
    """上一学期：x-x+1-2 的上一学期是 x-x+1-1；x-x+1-1 的上一学期是 x-1-x-2。"""
    y, _, n = term_id.split("-")
    y, n = int(y), int(n)
    return f"{y}-{y + 1}-1" if n == 2 else f"{y - 1}-{y}-2"


def last_year_term_id(term_id):
    y, _, n = term_id.split("-")
    y = int(y)
    return f"{y - 1}-{y}-{n}"


# ---------------------------------------------------------------- 课表网格
def build_grid(slots, max_period=None):
    """slots: 含 weekday/start_period/period_count 的 dict 列表。
    返回 (rows, days, periods)；rows[p][d] 为 None / 'covered' / {'items': [...], 'rowspan': n}"""
    items = [dict(s) for s in slots]
    used_days = sorted({s["weekday"] for s in items} | {1, 2, 3, 4, 5})
    top = max([s["start_period"] + s["period_count"] - 1 for s in items] + [8])
    if max_period:
        top = max(top, max_period)
    top = min(top, 12)
    cell = {}
    for s in items:
        cell.setdefault((s["weekday"], s["start_period"]), []).append(s)
    rows = {}
    covered = set()
    for p in range(1, top + 1):
        row = {}
        for d in used_days:
            if (d, p) in covered:
                row[d] = "covered"
                continue
            lst = cell.get((d, p))
            if not lst:
                row[d] = None
                continue
            span = max(x["period_count"] for x in lst)
            for k in range(1, span):
                covered.add((d, p + k))
            row[d] = {"items": lst, "rowspan": span}
        rows[p] = row
    return rows, used_days, list(range(1, top + 1))


# ---------------------------------------------------------------- SVG 图表
PALETTE = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2"]


def _nice_max(v):
    if v <= 0:
        return 1
    mag = 10 ** (len(str(int(v))) - 1)
    for k in (1, 2, 2.5, 5, 10):
        if v <= k * mag:
            return k * mag
    return 10 * mag


def bar_chart(labels, series, width=820, height=340):
    """分组柱状图。series: [(名称, [数值...])]"""
    ml, mr, mt, mb = 52, 12, 28, 86
    W, H = width - ml - mr, height - mt - mb
    vmax = _nice_max(max([v or 0 for _, vals in series for v in vals] + [1]))
    n, k = max(len(labels), 1), max(len(series), 1)
    gw = W / n
    bw = gw * 0.8 / k
    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" xmlns="http://www.w3.org/2000/svg">']
    for i in range(5):
        y = mt + H - H * i / 4
        out.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{width - mr}" y2="{y:.1f}" stroke="#e5e5e5"/>')
        out.append(f'<text x="{ml - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#666">{vmax * i / 4:g}</text>')
    for gi, lab in enumerate(labels):
        gx = ml + gi * gw + gw * 0.1
        for si, (_, vals) in enumerate(series):
            v = vals[gi] or 0
            h = H * v / vmax
            x = gx + si * bw
            out.append(f'<rect x="{x:.1f}" y="{mt + H - h:.1f}" width="{bw:.1f}" height="{h:.1f}" '
                       f'fill="{PALETTE[si % 5]}"><title>{escape(lab)} {escape(series[si][0])}: {v}</title></rect>')
        cx, cy = ml + gi * gw + gw / 2, mt + H + 14
        out.append(f'<text x="{cx:.1f}" y="{cy}" font-size="11" fill="#333" text-anchor="end" '
                   f'transform="rotate(-35 {cx:.1f} {cy})">{escape(lab)}</text>')
    for si, (name, _) in enumerate(series):
        lx = ml + si * 130
        out.append(f'<rect x="{lx}" y="6" width="12" height="12" fill="{PALETTE[si % 5]}"/>'
                   f'<text x="{lx + 17}" y="16" font-size="12">{escape(name)}</text>')
    out.append("</svg>")
    return Markup("".join(out))


def line_chart(labels, hist, pred, width=640, height=280):
    """历史折线 + 预测点。hist/pred 为与 labels 等长、可含 None 的数值列表。"""
    ml, mr, mt, mb = 52, 20, 28, 40
    W, H = width - ml - mr, height - mt - mb
    vals = [v for v in hist + pred if v is not None]
    vmax = _nice_max(max(vals + [1]))
    n = len(labels)
    step = W / max(n - 1, 1)

    def xy(i, v):
        return ml + i * step, mt + H - H * v / vmax

    out = [f'<svg viewBox="0 0 {width} {height}" class="chart" xmlns="http://www.w3.org/2000/svg">']
    for i in range(5):
        y = mt + H - H * i / 4
        out.append(f'<line x1="{ml}" y1="{y:.1f}" x2="{width - mr}" y2="{y:.1f}" stroke="#e5e5e5"/>')
        out.append(f'<text x="{ml - 6}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#666">{vmax * i / 4:g}</text>')
    for series, color, dash in ((hist, PALETTE[0], ""), (pred, PALETTE[3], "6 4")):
        pts = [(i, v) for i, v in enumerate(series) if v is not None]
        if len(pts) > 1:
            d = " ".join(f"{xy(i, v)[0]:.1f},{xy(i, v)[1]:.1f}" for i, v in pts)
            out.append(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="2" stroke-dasharray="{dash}"/>')
        for i, v in pts:
            x, y = xy(i, v)
            out.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{color}"/>'
                       f'<text x="{x:.1f}" y="{y - 8:.1f}" font-size="11" text-anchor="middle">{v}</text>')
    for i, lab in enumerate(labels):
        x, _ = xy(i, 0)
        out.append(f'<text x="{x:.1f}" y="{height - 14}" font-size="11" text-anchor="middle" fill="#333">{escape(lab)}</text>')
    out.append(f'<rect x="{ml}" y="6" width="12" height="12" fill="{PALETTE[0]}"/><text x="{ml + 17}" y="16" font-size="12">历史选课人数</text>'
               f'<rect x="{ml + 120}" y="6" width="12" height="12" fill="{PALETTE[3]}"/><text x="{ml + 137}" y="16" font-size="12">预测人数</text>')
    out.append("</svg>")
    return Markup("".join(out))
