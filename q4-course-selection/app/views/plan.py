"""制定专业培养方案（教研科）：课程信息（含先导课程）+ 各专业各学期开课计划。"""
from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from ..auth import require
from ..db import DatabaseError, get_db, tx
from ..util import friendly_db_error
from .crud import Entity, Field, register

bp = Blueprint("plan", __name__, url_prefix="/plan")

ENTITIES = [
    Entity("courses", "课程", "course", "id",
           [Field("id", "课程号", readonly_on_edit=True), Field("name", "课程名称"),
            Field("intro", "简介", required=False)],
           "SELECT c.id AS _pk, c.id, c.name, c.intro, (SELECT GROUP_CONCAT(p.prereq_id ORDER BY p.prereq_id SEPARATOR '、') FROM course_prereq p "
           "WHERE p.course_id = c.id) AS pre FROM course c",
           [("id", "课程号"), ("name", "课程名称"), ("pre", "先导课程"), ("intro", "简介")],
           "plan", search=("id", "name"), order="id", row_links=[("先导课程", "plan.prereq", "cid")]),
    Entity("curriculum", "培养方案条目", "curriculum_plan", "id",
           [Field("major_id", "专业", options="SELECT id, name FROM major ORDER BY id"),
            Field("semester_no", "开课学期", "int", options=[(i, f"第{i}学期") for i in range(1, 9)],
                  help="本科四年制，每学年两个学期，共 8 个学期"),
            Field("course_id", "课程", options="SELECT id, CONCAT(id, ' ', name) FROM course ORDER BY id"),
            Field("hours", "学时", "int"), Field("credits", "学分", "float"),
            Field("college_id", "开课学院", options="SELECT id, name FROM org_unit WHERE kind = '学院' ORDER BY id"),
            Field("nature", "课程性质", options=[("必修", "必修"), ("任选", "任选")])],
           "SELECT p.id AS _pk, m.name AS major, p.semester_no, CONCAT(p.course_id, ' ', c.name) AS course, p.hours, "
           "p.credits, o.name AS college, p.nature, p.major_id FROM curriculum_plan p JOIN major m ON m.id = p.major_id "
           "JOIN course c ON c.id = p.course_id JOIN org_unit o ON o.id = p.college_id",
           [("major", "专业"), ("semester_no", "学期"), ("course", "课程"), ("hours", "学时"), ("credits", "学分"),
            ("college", "开课学院"), ("nature", "性质")],
           "plan", search=("course",), order="major_id, semester_no, course",
           filters=[("major_id", "专业", "SELECT id, name FROM major ORDER BY id"),
                    ("semester_no", "学期", [(i, f"第{i}学期") for i in range(1, 9)])]),
]


def tabs(active):
    return [(e.title, url_for(f"plan.{e.key}_list"), e.key == active) for e in ENTITIES]


for _e in ENTITIES:
    register(bp, _e, tabs)


def _reaches(conn, start, target, skip_course):
    """沿「先导课程」边从 start 出发能否到达 target（用于检测环）。"""
    seen, stack = set(), [start]
    while stack:
        c = stack.pop()
        if c == target:
            return True
        if c in seen:
            continue
        seen.add(c)
        stack += [r[0] for r in conn.execute("SELECT prereq_id FROM course_prereq WHERE course_id = ?", (c,))
                  if c != skip_course]
    return False


@bp.route("/courses/<cid>/prereq", methods=["GET", "POST"])
@require("plan")
def prereq(cid):
    conn = get_db()
    c = conn.execute("SELECT * FROM course WHERE id = ?", (cid,)).fetchone()
    if c is None:
        abort(404)
    if request.method == "POST":
        chosen = [x for x in request.form.getlist("item") if x != cid]
        bad = [p for p in chosen if _reaches(conn, p, cid, cid)]
        if bad:
            flash(f"先导关系会形成循环依赖：{', '.join(bad)} 直接或间接以本课程为先导", "error")
        else:
            try:     # 应用层先检查一遍；数据库触发器 trg_prereq_acyclic（递归 CTE）是最后一道防线
                with tx(conn):
                    conn.execute("DELETE FROM course_prereq WHERE course_id = ?", (cid,))
                    for p in chosen:
                        conn.execute("INSERT INTO course_prereq(course_id, prereq_id) VALUES (?,?)", (cid, p))
                flash("先导课程已更新", "ok")
                return redirect(url_for("plan.courses_list"))
            except DatabaseError as e:
                flash(friendly_db_error(e), "error")
    have = {r[0] for r in conn.execute("SELECT prereq_id FROM course_prereq WHERE course_id = ?", (cid,))}
    items = [(r["id"], f"{r['id']} {r['name']}", r["id"] in have)
             for r in conn.execute("SELECT * FROM course WHERE id <> ? ORDER BY id", (cid,))]
    return render_template("checklist.html", title=f"{c['name']}（{cid}）的先导课程", items=items,
                           back=url_for("plan.courses_list"), hint="勾选需要先修的课程；系统会拒绝形成循环依赖的设置。")


@bp.route("/")
@require("plan")
def index():
    return redirect(url_for("plan.courses_list"))
