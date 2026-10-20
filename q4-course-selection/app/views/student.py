"""学生选课：必修课程列表 → 选课程 → 选教学班（有空位才成功）→ 已选课程 / 退选。"""
from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..auth import require
from ..db import get_db
from ..services import selection
from ..util import now_str

bp = Blueprint("student", __name__, url_prefix="/enroll")


def _student(conn):
    sid = g.user["student_id"]
    return conn.execute("SELECT * FROM v_student WHERE id = ?", (sid,)).fetchone() if sid else None


def _term(conn, now):
    tid = request.values.get("term")
    opens = selection.open_terms(conn, now)
    if tid:
        return conn.execute("SELECT * FROM term WHERE id = ?", (tid,)).fetchone()
    return opens[-1] if opens else None


@bp.route("/")
@require("enroll")
def enroll_home():
    conn = get_db()
    stu = _student(conn)
    if stu is None:
        return render_template("enroll_home.html", stu=None)
    now = now_str()
    term = _term(conn, now)
    upcoming = conn.execute("SELECT * FROM term WHERE select_start > ? ORDER BY select_start", (now,)).fetchall()
    ctx = dict(stu=stu, term=term, upcoming=upcoming, is_open=False)
    if term:
        mine = selection.my_enrollments(conn, stu["id"], term["id"])
        taken = {m["task_id"] for m in mine}
        ctx.update(
            is_open=selection.window_open(term, now), mine=mine, taken=taken,
            required=selection.required_tasks(conn, stu["id"], term["id"]),
            electives=selection.elective_tasks(conn, term["id"]),
            total_hours=sum(m["hours"] for m in mine),
            terms=conn.execute("SELECT * FROM term WHERE select_end >= ? AND select_start <= ? OR id IN "
                               "(SELECT t.term_id FROM enrollment e JOIN teaching_task t ON t.id = e.task_id "
                               "WHERE e.student_id = ?) ORDER BY id DESC", (now, now, stu["id"])).fetchall())
    return render_template("enroll_home.html", **ctx)


@bp.route("/course/<int:task_id>")
@require("enroll")
def enroll_course(task_id):
    conn = get_db()
    stu = _student(conn)
    if stu is None:
        abort(403)
    task = conn.execute("SELECT t.*, c.name AS course_name FROM teaching_task t JOIN course c ON c.id = t.course_id "
                        "WHERE t.id = ?", (task_id,)).fetchone()
    if task is None:
        abort(404)
    if task["nature"] == "必修" and not conn.execute(
            "SELECT 1 FROM teaching_task_class WHERE task_id = ? AND class_id = ?", (task_id, stu["class_id"])).fetchone():
        abort(403)
    term = conn.execute("SELECT * FROM term WHERE id = ?", (task["term_id"],)).fetchone()
    mine = conn.execute("SELECT section_id FROM enrollment WHERE student_id = ? AND task_id = ?",
                        (stu["id"], task_id)).fetchone()
    prereqs = conn.execute("SELECT c.id, c.name FROM course_prereq p JOIN course c ON c.id = p.prereq_id "
                           "WHERE p.course_id = ?", (task["course_id"],)).fetchall()
    return render_template("enroll_course.html", task=task, term=term, secs=selection.task_sections(conn, task_id),
                           my_section=mine["section_id"] if mine else None, prereqs=prereqs,
                           is_open=selection.window_open(term, now_str()))


@bp.route("/join", methods=["POST"])
@require("enroll")
def join():
    conn = get_db()
    stu = _student(conn)
    if stu is None:
        abort(403)
    try:
        sec = selection.enroll(conn, stu["id"], int(request.form["section_id"]), now_str())
        flash(f"选课成功：{sec['course_name']}", "ok")
    except (selection.EnrollError, ValueError, KeyError) as e:
        flash(str(e) if isinstance(e, selection.EnrollError) else "请求有误", "error")
    return redirect(url_for("student.enroll_home", term=request.form.get("term")))


@bp.route("/drop", methods=["POST"])
@require("enroll")
def drop():
    conn = get_db()
    stu = _student(conn)
    if stu is None:
        abort(403)
    try:
        selection.drop(conn, stu["id"], int(request.form["task_id"]), now_str())
        flash("已退选", "ok")
    except (selection.EnrollError, ValueError, KeyError) as e:
        flash(str(e) if isinstance(e, selection.EnrollError) else "请求有误", "error")
    return redirect(url_for("student.enroll_home", term=request.form.get("term")))
