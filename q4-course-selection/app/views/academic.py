"""教务科：教学进程计划、教学任务（课头 + 教师）、编制课程表。"""
from datetime import datetime, timedelta

from flask import Blueprint, abort, flash, g, redirect, render_template, request, url_for

from ..auth import require
from ..db import DatabaseError, IntegrityError, get_db, tx
from ..services import progress, sections, selection, timetable
from ..services.common import set_setting, get_setting
from ..util import WEEKDAYS, build_grid, friendly_db_error, now_str, prev_term_id

bp = Blueprint("academic", __name__)


def _dt(v):
    """datetime-local 控件的值 → 'YYYY-MM-DD HH:MM'；空值 → None。"""
    v = (v or "").strip().replace("T", " ")
    return v or None


def default_term(conn):
    opened = selection.open_terms(conn, now_str())
    if opened:
        return opened[-1]["id"]
    r = conn.execute("SELECT id FROM term ORDER BY id DESC LIMIT 1").fetchone()
    return r[0] if r else None


def pick_term(conn):
    tid = request.args.get("term") or default_term(conn)
    term = conn.execute("SELECT * FROM term WHERE id = ?", (tid,)).fetchone() if tid else None
    return term, conn.execute("SELECT id FROM term ORDER BY id DESC").fetchall()


def _fmt(e):
    return friendly_db_error(e) if isinstance(e, DatabaseError) else str(e)


# ================================================================ 教学进程计划
@bp.route("/progress/")
@require("progress")
def progress_home():
    conn = get_db()
    now = datetime.strptime(now_str(), "%Y-%m-%d %H:%M")
    terms = []
    for t in conn.execute("SELECT * FROM term ORDER BY id DESC"):
        n = conn.execute("SELECT COUNT(*), COALESCE(SUM(required_count), 0) FROM teaching_task WHERE term_id = ?",
                         (t["id"],)).fetchone()
        prev = conn.execute("SELECT * FROM term WHERE id = ?", (prev_term_id(t["id"]),)).fetchone()
        deadline = None
        if prev:   # 教学任务应在上一学期期中时段制定完毕
            deadline = datetime.strptime(prev["start_date"], "%Y-%m-%d") + timedelta(weeks=prev["weeks"] // 2)
        terms.append(dict(t, n_tasks=n[0], n_required=n[1], deadline=deadline,
                          overdue=bool(deadline and now > deadline and n[0] == 0)))
    return render_template("progress.html", terms=terms)


@bp.route("/progress/term/add", methods=["POST"])
@require("progress")
def term_add():
    f = request.form
    try:
        y, n = int(f["year_start"]), int(f["term_no"])
        tid = f"{y}-{y + 1}-{n}"
        get_db().execute(
            "INSERT INTO term(id, year_start, term_no, weeks, start_date, select_start, select_end) VALUES (?,?,?,?,?,?,?)",
            (tid, y, n, int(f["weeks"]), f["start_date"], _dt(f.get("select_start")), _dt(f.get("select_end"))))
        flash(f"已录入学期 {tid}", "ok")
    except (DatabaseError, ValueError, KeyError) as e:
        flash(_fmt(e), "error")
    return redirect(url_for("academic.progress_home"))


@bp.route("/progress/term/<tid>/edit", methods=["POST"])
@require("progress")
def term_edit(tid):
    f = request.form
    try:
        get_db().execute("UPDATE term SET weeks = ?, start_date = ?, select_start = ?, select_end = ? WHERE id = ?",
                         (int(f["weeks"]), f["start_date"], _dt(f.get("select_start")), _dt(f.get("select_end")), tid))
        flash("学期信息已更新", "ok")
    except (DatabaseError, ValueError, KeyError) as e:
        flash(_fmt(e), "error")
    return redirect(url_for("academic.progress_home"))


@bp.route("/progress/term/<tid>/generate", methods=["POST"])
@require("progress")
def term_generate(tid):
    try:
        created, updated = progress.generate_tasks(get_db(), tid)
        flash(f"教学任务已生成：新建 {created} 个，更新 {updated} 个", "ok")
    except (ValueError, DatabaseError) as e:
        flash(_fmt(e), "error")
    return redirect(url_for("academic.progress_term", tid=tid))


@bp.route("/progress/term/<tid>")
@require("progress")
def progress_term(tid):
    conn = get_db()
    term = conn.execute("SELECT * FROM term WHERE id = ?", (tid,)).fetchone()
    if term is None:
        abort(404)
    tasks = conn.execute(
        """SELECT t.*, c.name AS course_name,
                  (SELECT GROUP_CONCAT(CONCAT(ac.name, '(', tc.student_count, '人)') ORDER BY ac.id SEPARATOR '、')
                     FROM teaching_task_class tc JOIN admin_class ac ON ac.id = tc.class_id
                    WHERE tc.task_id = t.id) AS classes
             FROM teaching_task t JOIN course c ON c.id = t.course_id WHERE t.term_id = ? ORDER BY t.nature, t.course_id""",
        (tid,)).fetchall()
    cohorts = conn.execute(
        "SELECT name, enroll_year, (? - enroll_year) * 2 + ? AS sem FROM admin_class WHERE (? - enroll_year) * 2 + ? BETWEEN 1 AND 8 "
        "ORDER BY enroll_year", (term["year_start"], term["term_no"], term["year_start"], term["term_no"])).fetchall()
    return render_template("progress_term.html", term=term, tasks=tasks, cohorts=cohorts)


# ================================================================ 教学任务
@bp.route("/tasks/")
@require("tasks")
def tasks():
    conn = get_db()
    term, terms_ = pick_term(conn)
    rows = []
    if term:
        rows = conn.execute(
            """SELECT t.*, c.name AS course_name,
                      (SELECT COUNT(*) FROM section s WHERE s.task_id = t.id) AS n_sec,
                      (SELECT COALESCE(SUM(capacity), 0) FROM section s WHERE s.task_id = t.id) AS cap_sum,
                      (SELECT COUNT(*) FROM section s WHERE s.task_id = t.id AND NOT EXISTS
                         (SELECT 1 FROM section_teacher st WHERE st.section_id = s.id)) AS no_teacher
                 FROM teaching_task t JOIN course c ON c.id = t.course_id WHERE t.term_id = ?
                ORDER BY t.nature, t.course_id""", (term["id"],)).fetchall()
    return render_template("tasks.html", term=term, terms=terms_, rows=rows,
                           limit=sections.section_limit(conn), room_max=sections.max_room_capacity(conn))


@bp.route("/tasks/limit", methods=["POST"])
@require("tasks")
def tasks_limit():
    try:
        v = int(request.form["limit"])
        if v < 1:
            raise ValueError
        set_setting(get_db(), "section_limit", v)
        flash("教学班人数限额已更新", "ok")
    except (ValueError, KeyError):
        flash("请输入有效的人数限额", "error")
    return redirect(url_for("academic.tasks", term=request.form.get("term")))


@bp.route("/tasks/term/<tid>/split", methods=["POST"])
@require("tasks")
def tasks_split_all(tid):
    done, skipped = sections.split_term(get_db(), tid)
    flash(f"已为 {done} 个教学任务设置课头", "ok")
    for s in skipped:
        flash("跳过：" + s, "warn")
    return redirect(url_for("academic.tasks", term=tid))


@bp.route("/tasks/term/<tid>/auto-teachers", methods=["POST"])
@require("tasks")
def tasks_auto_teachers(tid):
    done, failed = sections.auto_assign_teachers(get_db(), tid)
    flash(f"已为 {done} 个课头自动分配主讲教师（按本学期负载最轻者优先）", "ok")
    for s in failed:
        flash(s, "warn")
    return redirect(url_for("academic.tasks", term=tid))


@bp.route("/tasks/<int:task_id>")
@require("tasks")
def task_detail(task_id):
    conn = get_db()
    task = conn.execute("SELECT t.*, c.name AS course_name FROM teaching_task t JOIN course c ON c.id = t.course_id "
                        "WHERE t.id = ?", (task_id,)).fetchone()
    if task is None:
        abort(404)
    secs = selection.task_sections(conn, task_id)
    for s in secs:
        s["tlist"] = conn.execute("SELECT st.teacher_id, st.is_lead, te.name, te.title FROM section_teacher st "
                                  "JOIN teacher te ON te.id = st.teacher_id WHERE st.section_id = ? "
                                  "ORDER BY st.is_lead DESC, te.id", (s["section_id"],)).fetchall()
    cands = sections.qualified_teachers(conn, task["course_id"], task["term_id"])
    classes = conn.execute("SELECT ac.name, tc.student_count FROM teaching_task_class tc "
                           "JOIN admin_class ac ON ac.id = tc.class_id WHERE tc.task_id = ?", (task_id,)).fetchall()
    return render_template("task_detail.html", task=task, secs=secs, cands=cands, classes=classes,
                           basis=sections.basis_count(task), limit=sections.section_limit(conn),
                           room_max=sections.max_room_capacity(conn))


def _back(task_id):
    return redirect(url_for("academic.task_detail", task_id=task_id))


@bp.route("/tasks/<int:task_id>/split", methods=["POST"])
@require("tasks")
def task_split(task_id):
    try:
        n, cap = sections.split_task(get_db(), task_id)
        flash(f"已拆分为 {n} 个课头，每个教学班人数上限 {cap}", "ok")
    except (ValueError, DatabaseError) as e:
        flash(_fmt(e), "error")
    return _back(task_id)


@bp.route("/tasks/<int:task_id>/expected", methods=["POST"])
@require("tasks")
def task_expected(task_id):
    try:
        v = int(request.form["expected"])
        get_db().execute("UPDATE teaching_task SET expected_count = ? WHERE id = ? AND nature = '任选'", (v, task_id))
        flash("预计人数已保存", "ok")
    except (ValueError, KeyError, DatabaseError) as e:
        flash(_fmt(e), "error")
    return _back(task_id)


@bp.route("/tasks/<int:task_id>/section/add", methods=["POST"])
@require("tasks")
def section_add(task_id):
    conn = get_db()
    try:
        cap = int(request.form["capacity"])
        with tx(conn):
            nxt = conn.execute("SELECT COALESCE(MAX(section_no), 0) + 1 FROM section WHERE task_id = ?", (task_id,)).fetchone()[0]
            conn.execute("INSERT INTO section(task_id, section_no, capacity) VALUES (?,?,?)", (task_id, nxt, cap))
        flash(f"已新增第 {nxt} 课头", "ok")
    except (ValueError, KeyError, DatabaseError) as e:
        flash(_fmt(e), "error")
    return _back(task_id)


@bp.route("/tasks/section/<int:sid>/capacity", methods=["POST"])
@require("tasks")
def section_capacity(sid):
    conn = get_db()
    task_id = conn.execute("SELECT task_id FROM section WHERE id = ?", (sid,)).fetchone()
    if task_id is None:
        abort(404)
    try:
        conn.execute("UPDATE section SET capacity = ? WHERE id = ?", (int(request.form["capacity"]), sid))
        flash("教学班人数上限已更新", "ok")
    except (ValueError, KeyError, DatabaseError) as e:
        flash("更新失败：人数上限不能小于已选人数。" if isinstance(e, IntegrityError) else _fmt(e), "error")
    return _back(task_id[0])


@bp.route("/tasks/section/<int:sid>/delete", methods=["POST"])
@require("tasks")
def section_delete(sid):
    conn = get_db()
    row = conn.execute("SELECT task_id FROM section WHERE id = ?", (sid,)).fetchone()
    if row is None:
        abort(404)
    try:
        conn.execute("DELETE FROM section WHERE id = ?", (sid,))
        flash("课头已删除", "ok")
    except DatabaseError:
        flash("该课头已有学生选课，不能删除", "error")
    return _back(row[0])


@bp.route("/tasks/section/<int:sid>/teacher/add", methods=["POST"])
@require("tasks")
def section_teacher_add(sid):
    conn = get_db()
    row = conn.execute("SELECT task_id FROM section WHERE id = ?", (sid,)).fetchone()
    if row is None:
        abort(404)
    try:
        sections.add_teacher(conn, sid, request.form["teacher_id"], bool(request.form.get("is_lead")))
        flash("教师已分配", "ok")
    except (ValueError, KeyError) as e:
        flash(str(e), "error")
    return _back(row[0])


@bp.route("/tasks/section/<int:sid>/teacher/<tid>/<action>", methods=["POST"])
@require("tasks")
def section_teacher_action(sid, tid, action):
    conn = get_db()
    row = conn.execute("SELECT task_id FROM section WHERE id = ?", (sid,)).fetchone()
    if row is None or action not in ("lead", "remove"):
        abort(404)
    try:
        (sections.set_lead if action == "lead" else sections.remove_teacher)(conn, sid, tid)
        flash("已更新", "ok")
    except ValueError as e:
        flash(str(e), "error")
    return _back(row[0])


# ================================================================ 编制课程表
@bp.route("/timetable/")
@require("timetable")
def timetable_home():
    conn = get_db()
    term, terms_ = pick_term(conn)
    rows, conflicts = [], []
    if term:
        rows = conn.execute("SELECT * FROM v_section WHERE term_id = ? ORDER BY course_id, section_no", (term["id"],)).fetchall()
        texts = selection.slot_texts(conn, [r["section_id"] for r in rows])
        rows = [dict(r, slots=texts[r["section_id"]]) for r in rows]
        conflicts = timetable.find_conflicts(conn, term["id"])
    return render_template("timetable.html", term=term, terms=terms_, rows=rows, conflicts=conflicts)


@bp.route("/timetable/term/<tid>/auto", methods=["POST"])
@require("timetable")
def timetable_auto(tid):
    ok, skipped = timetable.auto_schedule_term(get_db(), tid)
    flash(f"自动排课完成：{ok} 个课头已排课（同一课头尽量保持教室不变，必须变化时按教学区→教学楼→楼层就近调整）", "ok")
    for s in skipped:
        flash(s, "warn")
    return redirect(url_for("academic.timetable_home", term=tid))


@bp.route("/timetable/term/<tid>/clear", methods=["POST"])
@require("timetable")
def timetable_clear(tid):
    timetable.clear_term(get_db(), tid)
    flash("已清空本学期课程表", "ok")
    return redirect(url_for("academic.timetable_home", term=tid))


@bp.route("/timetable/section/<int:sid>")
@require("timetable")
def timetable_section(sid):
    conn = get_db()
    sec = conn.execute("SELECT * FROM v_section WHERE section_id = ?", (sid,)).fetchone()
    if sec is None:
        abort(404)
    slots = conn.execute("SELECT * FROM v_slot WHERE section_id = ? ORDER BY weekday, start_period", (sid,)).fetchall()
    rooms = conn.execute("SELECT * FROM classroom ORDER BY area, building, room_no").fetchall()
    term = conn.execute("SELECT * FROM term WHERE id = ?", (sec["term_id"],)).fetchone()
    grid = build_grid([dict(s, label=f"{s['building']}{s['room_no']}") for s in slots])
    return render_template("timetable_section.html", sec=sec, slots=slots, rooms=rooms, term=term, grid=grid,
                           weekdays=WEEKDAYS)


@bp.route("/timetable/section/<int:sid>/add", methods=["POST"])
@require("timetable")
def slot_add(sid):
    conn = get_db()
    sec = conn.execute("SELECT term_id FROM v_section WHERE section_id = ?", (sid,)).fetchone()
    if sec is None:
        abort(404)
    f = request.form
    try:
        args = [int(f[k]) for k in ("weekday", "start", "count", "ws", "we", "room")]
        with tx(conn):
            occ = timetable.Occupancy(conn, sec["term_id"])
            timetable.add_slot(conn, occ, sid, *args)
        flash("已添加上课时间", "ok")
    except timetable.ConflictError as e:
        for m in e.messages:
            flash(m, "error")
    except (ValueError, KeyError, DatabaseError) as e:
        flash(_fmt(e), "error")
    return redirect(url_for("academic.timetable_section", sid=sid))


@bp.route("/timetable/slot/<int:slot_id>/delete", methods=["POST"])
@require("timetable")
def slot_delete(slot_id):
    conn = get_db()
    row = conn.execute("SELECT section_id FROM schedule_slot WHERE id = ?", (slot_id,)).fetchone()
    if row is None:
        abort(404)
    timetable.delete_slot(conn, slot_id)
    flash("已删除", "ok")
    return redirect(url_for("academic.timetable_section", sid=row[0]))


@bp.route("/timetable/section/<int:sid>/auto", methods=["POST"])
@require("timetable")
def section_auto(sid):
    conn = get_db()
    sec = conn.execute("SELECT term_id FROM v_section WHERE section_id = ?", (sid,)).fetchone()
    if sec is None:
        abort(404)
    try:
        with tx(conn):
            timetable.clear_section(conn, sid)
            occ = timetable.Occupancy(conn, sec["term_id"])
            if not occ.sec_teachers.get(sid):
                raise ValueError("请先为该课头分配教师")
            if not timetable.auto_schedule_section(conn, occ, sid):
                raise ValueError("找不到满足约束的时间，原课表保持不变")
        flash("已自动排课", "ok")
    except (ValueError, DatabaseError) as e:
        flash(_fmt(e), "error")
    return redirect(url_for("academic.timetable_section", sid=sid))
