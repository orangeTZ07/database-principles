"""教学任务：课头（教学班）拆分、教师分配（多人合上、仅一个主讲）。"""
import math

from ..db import DatabaseError, tx
from ..util import friendly_db_error
from .common import get_setting


def section_limit(conn):
    return int(get_setting(conn, "section_limit", 60))


def max_room_capacity(conn):
    return conn.execute("SELECT COALESCE(MAX(capacity), 0) FROM classroom").fetchone()[0]


def basis_count(task):
    """拆分课头所依据的人数：必修取必修学生人数，任选取预计人数。"""
    return task["required_count"] if task["nature"] == "必修" else (task["expected_count"] or 0)


def split_task(conn, task_id, limit=None):
    """人数超过「教室最大容量」或「教学班人数限额」时拆成若干课头；返回 (课头数, 每班容量)。"""
    task = conn.execute("SELECT * FROM teaching_task WHERE id = ?", (task_id,)).fetchone()
    if task is None:
        raise ValueError("教学任务不存在")
    basis = basis_count(task)
    if basis <= 0:
        raise ValueError("该任务没有人数依据（任选课请先设置预计人数或运行数据挖掘）")
    cap_room = max_room_capacity(conn)
    if cap_room <= 0:
        raise ValueError("尚未维护教室信息")
    limit = min(limit or section_limit(conn), cap_room)
    n = math.ceil(basis / limit)
    cap = math.ceil(basis / n)
    if conn.execute("SELECT COUNT(*) FROM enrollment WHERE task_id = ?", (task_id,)).fetchone()[0]:
        raise ValueError("该任务已有学生选课，不能重新拆分课头")
    with tx(conn):
        conn.execute("DELETE FROM section WHERE task_id = ?", (task_id,))
        for i in range(1, n + 1):
            conn.execute("INSERT INTO section(task_id, section_no, capacity) VALUES (?,?,?)", (task_id, i, cap))
    return n, cap


def split_term(conn, term_id, only_missing=True):
    """批量拆分；返回 (成功任务数, [跳过原因])。"""
    tasks = conn.execute("SELECT t.*, c.name AS course_name, "
                         "(SELECT COUNT(*) FROM section s WHERE s.task_id = t.id) AS n_sec "
                         "FROM teaching_task t JOIN course c ON c.id = t.course_id WHERE t.term_id = ?",
                         (term_id,)).fetchall()
    done, skipped = 0, []
    for t in tasks:
        if only_missing and t["n_sec"] > 0:
            continue
        try:
            split_task(conn, t["id"])
            done += 1
        except ValueError as e:
            skipped.append(f"{t['course_name']}：{e}")
    return done, skipped


# ------------------------------------------------------------------ 教师
def _run(conn, fn):
    try:
        fn()
    except DatabaseError as e:
        raise ValueError(friendly_db_error(e))


def add_teacher(conn, section_id, teacher_id, is_lead=False):
    def go():
        with tx(conn):
            has_lead = conn.execute("SELECT 1 FROM section_teacher WHERE section_id = ? AND is_lead = 1",
                                    (section_id,)).fetchone()
            lead = 1 if (is_lead or not has_lead) else 0     # 第一位教师自动成为主讲
            if lead:
                conn.execute("UPDATE section_teacher SET is_lead = 0 WHERE section_id = ?", (section_id,))
            conn.execute("INSERT INTO section_teacher(section_id, teacher_id, is_lead) VALUES (?,?,?)",
                         (section_id, teacher_id, lead))
    _run(conn, go)


def set_lead(conn, section_id, teacher_id):
    with tx(conn):
        conn.execute("UPDATE section_teacher SET is_lead = 0 WHERE section_id = ?", (section_id,))
        cur = conn.execute("UPDATE section_teacher SET is_lead = 1 WHERE section_id = ? AND teacher_id = ?",
                           (section_id, teacher_id))
        if cur.rowcount == 0:
            raise ValueError("该教师未分配到此课头")


def remove_teacher(conn, section_id, teacher_id):
    with tx(conn):
        row = conn.execute("SELECT is_lead FROM section_teacher WHERE section_id = ? AND teacher_id = ?",
                           (section_id, teacher_id)).fetchone()
        conn.execute("DELETE FROM section_teacher WHERE section_id = ? AND teacher_id = ?", (section_id, teacher_id))
        if row and row["is_lead"]:   # 主讲被移除：剩余教师中第一位自动接任
            nxt = conn.execute("SELECT teacher_id FROM section_teacher WHERE section_id = ? ORDER BY teacher_id LIMIT 1",
                               (section_id,)).fetchone()
            if nxt:
                conn.execute("UPDATE section_teacher SET is_lead = 1 WHERE section_id = ? AND teacher_id = ?",
                             (section_id, nxt[0]))


def qualified_teachers(conn, course_id, term_id):
    """具备任课资格的教师，按本学期已承担课头数升序（负载均衡）。"""
    return conn.execute(
        """SELECT te.id, te.name, te.title,
                  (SELECT COUNT(*) FROM section_teacher st
                     JOIN section s ON s.id = st.section_id JOIN teaching_task t ON t.id = s.task_id
                    WHERE st.teacher_id = te.id AND t.term_id = ?) AS workload
             FROM teacher_course tc JOIN teacher te ON te.id = tc.teacher_id
            WHERE tc.course_id = ? ORDER BY workload, te.id""", (term_id, course_id)).fetchall()


def auto_assign_teachers(conn, term_id):
    """为没有教师的课头分配一名主讲（任课资格内负载最轻者）；返回 (已分配数, [无法分配的课头])。"""
    secs = conn.execute(
        """SELECT s.id, s.section_no, t.course_id, c.name AS course_name FROM section s
             JOIN teaching_task t ON t.id = s.task_id JOIN course c ON c.id = t.course_id
            WHERE t.term_id = ? AND NOT EXISTS (SELECT 1 FROM section_teacher st WHERE st.section_id = s.id)
            ORDER BY s.id""", (term_id,)).fetchall()
    done, failed = 0, []
    for s in secs:
        cands = qualified_teachers(conn, s["course_id"], term_id)
        if not cands:
            failed.append(f"{s['course_name']}第{s['section_no']}班：没有具备任课资格的教师")
            continue
        add_teacher(conn, s["id"], cands[0]["id"], True)
        done += 1
    return done, failed
