"""数据查询：专业培养方案查询、选课综合查询（教学进程/教学任务/课表/选课名单）、打印学生课表、教师课表。"""
from flask import Blueprint, abort, g, render_template, request

from ..auth import require
from ..db import get_db
from ..services import selection
from ..util import build_grid, now_str
from .academic import default_term

bp = Blueprint("query", __name__, url_prefix="/query")


def _terms(conn):
    return conn.execute("SELECT id FROM term ORDER BY id DESC").fetchall()


@bp.route("/")
@require("query")
def index():
    return render_template("query_index.html")


@bp.route("/plan")
@require("query")
def plan():
    conn = get_db()
    majors = conn.execute("SELECT m.id, m.name, o.name AS college FROM major m JOIN org_unit o ON o.id = m.college_id ORDER BY m.id").fetchall()
    mid = request.args.get("major_id") or (majors[0]["id"] if majors else None)
    rows = conn.execute(
        """SELECT p.semester_no, p.course_id, c.name AS course_name, p.hours, p.credits, p.nature, o.name AS college,
                  (SELECT GROUP_CONCAT(CONCAT(pc.prereq_id, ' ', pc2.name) ORDER BY pc.prereq_id SEPARATOR '、') FROM course_prereq pc
                     JOIN course pc2 ON pc2.id = pc.prereq_id WHERE pc.course_id = p.course_id) AS prereqs
             FROM curriculum_plan p JOIN course c ON c.id = p.course_id JOIN org_unit o ON o.id = p.college_id
            WHERE p.major_id = ? ORDER BY p.semester_no, p.nature, p.course_id""", (mid,)).fetchall()
    by_sem = {}
    for r in rows:
        by_sem.setdefault(r["semester_no"], []).append(r)
    total = (sum(r["credits"] for r in rows), sum(r["hours"] for r in rows))
    return render_template("query_plan.html", majors=majors, mid=int(mid) if mid else None, by_sem=by_sem, total=total)


@bp.route("/selection")
@require("query")
def selection_query():
    conn = get_db()
    terms = _terms(conn)
    tid = request.args.get("term") or default_term(conn)
    term = conn.execute("SELECT * FROM term WHERE id = ?", (tid,)).fetchone()
    classes = conn.execute("SELECT * FROM v_class WHERE (? - enroll_year) * 2 + ? BETWEEN 1 AND 8 ORDER BY major_id, enroll_year",
                           (term["year_start"], term["term_no"])).fetchall() if term else []
    cid = request.args.get("class_id") or (classes[0]["id"] if classes else None)
    ctx = dict(terms=terms, term=term, classes=classes, cid=int(cid) if cid else None, cls=None)
    if term and cid:
        cls = conn.execute("SELECT * FROM v_class WHERE id = ?", (cid,)).fetchone()
        if cls is None:
            abort(404)
        sem = (term["year_start"] - cls["enroll_year"]) * 2 + term["term_no"]
        tasks = conn.execute(
            """SELECT t.id AS task_id, t.course_id, c.name AS course_name, t.hours, t.nature, t.required_count, tc.student_count,
                      p.credits, p.semester_no
                 FROM teaching_task_class tc JOIN teaching_task t ON t.id = tc.task_id JOIN course c ON c.id = t.course_id
                 LEFT JOIN curriculum_plan p ON p.course_id = t.course_id AND p.major_id = ?
                WHERE tc.class_id = ? AND t.term_id = ? ORDER BY t.course_id""", (cls["major_id"], cid, term["id"])).fetchall()
        secs = []
        for t in tasks:
            secs += selection.task_sections(conn, t["task_id"])
        slots = conn.execute(
            """SELECT x.* FROM v_slot x JOIN teaching_task_class tc ON tc.task_id = x.task_id
                WHERE tc.class_id = ? AND x.term_id = ?""", (cid, term["id"])).fetchall()
        roster = []
        if g.user["role"] != "student":      # 学生不可查看其他同学的选课名单
            for t in tasks:
                rows = conn.execute(
                    """SELECT s.id, s.name, e.section_id, v.section_no FROM student s
                         LEFT JOIN enrollment e ON e.student_id = s.id AND e.task_id = ?
                         LEFT JOIN v_section v ON v.section_id = e.section_id
                        WHERE s.class_id = ? ORDER BY s.id""", (t["task_id"], cid)).fetchall()
                roster.append((t, rows))
        ctx.update(cls=cls, sem=sem, tasks=tasks, secs=secs, grid=build_grid([dict(s) for s in slots]), roster=roster)
    return render_template("query_selection.html", **ctx)


@bp.route("/timetable")
@require("query")
def student_timetable():
    conn = get_db()
    terms = _terms(conn)
    term_id = request.args.get("term") or default_term(conn)
    if g.user["role"] == "student":
        sid = g.user["student_id"]
    else:
        sid = (request.args.get("sid") or "").strip()
    stu = conn.execute("SELECT * FROM v_student WHERE id = ?", (sid,)).fetchone() if sid else None
    grid = items = None
    if stu:
        slots = conn.execute(
            """SELECT x.* FROM enrollment e JOIN v_slot x ON x.section_id = e.section_id
                WHERE e.student_id = ? AND x.term_id = ?""", (sid, term_id)).fetchall()
        items = selection.my_enrollments(conn, sid, term_id)
        grid = build_grid([dict(s) for s in slots])
    elif sid:
        pass
    return render_template("query_timetable.html", terms=terms, term_id=term_id, stu=stu, sid=sid, grid=grid,
                           items=items, mode="student")


@bp.route("/teacher")
@require("query")
def teacher_timetable():
    conn = get_db()
    terms = _terms(conn)
    term_id = request.args.get("term") or default_term(conn)
    if g.user["role"] == "teacher":
        tid = g.user["teacher_id"]
    else:
        tid = (request.args.get("tid") or "").strip()
    te = conn.execute("SELECT t.*, o.name AS dept FROM teacher t JOIN org_unit o ON o.id = t.dept_id WHERE t.id = ?",
                      (tid,)).fetchone() if tid else None
    grid = items = None
    if te:
        slots = conn.execute(
            """SELECT x.* FROM section_teacher st JOIN v_slot x ON x.section_id = st.section_id
                WHERE st.teacher_id = ? AND x.term_id = ?""", (tid, term_id)).fetchall()
        items = conn.execute(
            """SELECT v.*, st.is_lead FROM section_teacher st JOIN v_section v ON v.section_id = st.section_id
                WHERE st.teacher_id = ? AND v.term_id = ? ORDER BY v.course_id""", (tid, term_id)).fetchall()
        texts = selection.slot_texts(conn, [r["section_id"] for r in items])
        items = [dict(r, slots=texts[r["section_id"]]) for r in items]
        grid = build_grid([dict(s) for s in slots])
    return render_template("query_timetable.html", terms=terms, term_id=term_id, te=te, sid=tid, grid=grid,
                           items=items, mode="teacher")
