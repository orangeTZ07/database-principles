"""选课总况统计（同比/环比，图表+表格）与数据挖掘（任选课人数预测 → 教室推荐）。"""

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..auth import require
from ..db import DatabaseError, get_db, tx
from ..services import mining, sections
from ..util import bar_chart, last_year_term_id, line_chart, pct, prev_term_id
from .academic import default_term

bp = Blueprint("stats", __name__)


def _compare(conn, view, key, cols, metrics, term_id, order):
    """从统计视图取某学期数据，并用窗口函数 LAG() 取同一对象的上一学期（环比）与上学年同期（同比）。

    LAG 只是按行序取值，所以要用 term_id 校验：上一行确实是「上学期」「去年同期」才算数，否则视为无数据。"""
    lag = ",\n".join(
        f"LAG({m}, 1) OVER w AS {m}_prev, LAG({m}, 2) OVER w AS {m}_yoy" for m in metrics)
    rows = conn.execute(
        f"""SELECT * FROM (
                SELECT term_id, {key}, {cols}, {", ".join(metrics)},
                       LAG(term_id, 1) OVER w AS prev_term, LAG(term_id, 2) OVER w AS yoy_term,
                       {lag}
                  FROM {view}
                WINDOW w AS (PARTITION BY {key} ORDER BY term_id)) x
             WHERE term_id = ? ORDER BY {order}""", (term_id,)).fetchall()
    prev, yoy = prev_term_id(term_id), last_year_term_id(term_id)
    out = []
    for r in rows:
        item = dict(r)
        for m in metrics:
            if item["prev_term"] != prev:
                item[f"{m}_prev"] = None
            if item["yoy_term"] != yoy:
                item[f"{m}_yoy"] = None
            item[f"{m}_qoq"] = pct(item[m], item[f"{m}_prev"])
            item[f"{m}_yoy_pct"] = pct(item[m], item[f"{m}_yoy"])
        out.append(item)
    return out


def class_stats(conn, term_id):
    """班级维度：在读人数、选课门次、选课学时（视图 v_class_term_stat），含环比 / 同比。"""
    rows = _compare(conn, "v_class_term_stat", "class_id", "class_name, major_id, major_name, students",
                    ("selections", "hours"), term_id, "major_id, class_id")
    for r in rows:
        r["id"], r["name"] = r["class_id"], r["class_name"]
    return rows


def teacher_stats(conn, term_id):
    """教师维度：课头数、主讲课头数、学时（视图 v_teacher_term_stat），含环比 / 同比。"""
    rows = _compare(conn, "v_teacher_term_stat", "teacher_id", "name, title, lead_sections",
                    ("sections", "hours"), term_id, "sections DESC, teacher_id")
    for r in rows:
        r["id"] = r["teacher_id"]
    return rows


@bp.route("/stats/")
@require("stats")
def index():
    conn = get_db()
    terms = conn.execute("SELECT id FROM term ORDER BY id DESC").fetchall()
    tid = request.args.get("term") or default_term(conn)
    if not conn.execute("SELECT 1 FROM term WHERE id = ?", (tid,)).fetchone():
        abort(404)
    view = request.args.get("view", "chart")
    prev, yoy = prev_term_id(tid), last_year_term_id(tid)
    classes, teachers = class_stats(conn, tid), teacher_stats(conn, tid)
    # 学期合计
    def tot(lst, field, suffix=""):
        vals = [r.get(field + suffix) for r in lst]
        return None if all(v is None for v in vals) else sum(v or 0 for v in vals)
    totals = {}
    for f in ("selections", "hours"):
        cur_v, prev_v, yoy_v = tot(classes, f), tot(classes, f, "_prev"), tot(classes, f, "_yoy")
        totals[f] = (cur_v, prev_v, yoy_v, pct(cur_v, prev_v), pct(cur_v, yoy_v))
    charts = {
        "classes": bar_chart([c["name"] for c in classes], [
            ("本学期", [c["selections"] for c in classes]),
            (f"环比 {prev}", [c["selections_prev"] or 0 for c in classes]),
            (f"同比 {yoy}", [c["selections_yoy"] or 0 for c in classes])]),
        "teachers": bar_chart([t["name"] for t in teachers[:20]], [
            ("本学期课头数", [t["sections"] for t in teachers[:20]]),
            (f"环比 {prev}", [t["sections_prev"] or 0 for t in teachers[:20]]),
            (f"同比 {yoy}", [t["sections_yoy"] or 0 for t in teachers[:20]])]),
    }
    cid = request.args.get("class_id")
    students = []
    if cid:
        students = conn.execute(
            """SELECT s.id, s.name, COUNT(x.hours) AS n, COALESCE(SUM(x.hours), 0) AS hours FROM student s
                 LEFT JOIN (SELECT e.student_id, t.hours FROM enrollment e JOIN teaching_task t ON t.id = e.task_id
                             WHERE t.term_id = ?) x ON x.student_id = s.id WHERE s.class_id = ?
                GROUP BY s.id ORDER BY s.id""", (tid, cid)).fetchall()
    return render_template("stats.html", terms=terms, tid=tid, prev=prev, yoy=yoy, view=view, classes=classes,
                           teachers=teachers, totals=totals, charts=charts, cid=cid, students=students)


# ================================================================ 数据挖掘
@bp.route("/mining/")
@require("mining")
def mining_home():
    conn = get_db()
    terms = conn.execute("SELECT id FROM term ORDER BY id DESC").fetchall()
    tid = request.args.get("term") or default_term(conn)
    if not conn.execute("SELECT 1 FROM term WHERE id = ?", (tid,)).fetchone():
        abort(404)
    courses = conn.execute("SELECT DISTINCT c.id, c.name FROM curriculum_plan p JOIN course c ON c.id = p.course_id "
                           "WHERE p.nature = '任选' ORDER BY c.id").fetchall()
    rows = []
    for c in courses:
        f = mining.forecast(conn, c["id"], tid)
        f["name"] = c["name"]
        f["task"] = conn.execute("SELECT * FROM teaching_task WHERE term_id = ? AND course_id = ?", (tid, c["id"])).fetchone()
        f["saved"] = conn.execute("SELECT * FROM course_forecast WHERE term_id = ? AND course_id = ?", (tid, c["id"])).fetchone()
        rows.append(f)
    sel = request.args.get("course") or (courses[0]["id"] if courses else None)
    detail = next((r for r in rows if r["course_id"] == sel), None)
    chart = None
    if detail and detail["history"]:
        labels = [h["term_id"] for h in detail["history"]] + [tid]
        hist = [h["enrolled"] for h in detail["history"]] + [None]
        pred = [None] * (len(labels) - 2) + [hist[-2], detail["predicted"]] if len(labels) > 1 else [None]
        chart = line_chart(labels, hist, pred)
    return render_template("mining.html", terms=terms, tid=tid, rows=rows, detail=detail, chart=chart,
                           limit=sections.section_limit(conn), room_max=sections.max_room_capacity(conn))


@bp.route("/mining/adopt", methods=["POST"])
@require("mining")
def mining_adopt():
    conn = get_db()
    tid, cid = request.form.get("term"), request.form.get("course")
    try:
        with tx(conn):
            f = mining.adopt(conn, tid, cid)
        msg = f"已采纳预测：{cid} 预计 {f['predicted']} 人"
        if request.form.get("split"):
            task_id = conn.execute("SELECT id FROM teaching_task WHERE term_id = ? AND course_id = ?", (tid, cid)).fetchone()[0]
            n, cap = sections.split_task(conn, task_id)
            msg += f"，已按预测人数拆分为 {n} 个课头（每班上限 {cap}）"
        flash(msg, "ok")
    except (ValueError, DatabaseError) as e:
        flash(str(e), "error")
    return redirect(url_for("stats.mining_home", term=tid, course=cid))
