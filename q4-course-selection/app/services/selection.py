"""学生选课 / 退选，以及选课页面需要的查询。
选课与退选由 MySQL 存储过程 sp_enroll / sp_drop 在事务内完成（行锁防超卖、时间冲突检查），
「一门课只能选一个教学班」由 enrollment 主键保证，人数上限另有触发器兜底。"""
from ..db import call
from ..util import WEEKDAYS


class EnrollError(Exception):
    pass


def window_open(term, now):
    return bool(term["select_start"] and term["select_end"] and term["select_start"] <= now <= term["select_end"])


def open_terms(conn, now):
    return conn.execute("SELECT * FROM term WHERE select_start <= ? AND select_end >= ? ORDER BY id",
                        (now, now)).fetchall()


def required_tasks(conn, student_id, term_id):
    """学生所在班级在该学期的必修课程（即「下学期必修课程列表」）。"""
    return conn.execute(
        """SELECT t.id AS task_id, t.course_id, c.name AS course_name, t.hours, t.nature,
                  (SELECT COUNT(*) FROM section s WHERE s.task_id = t.id) AS n_sections,
                  (SELECT GROUP_CONCAT(p.prereq_id ORDER BY p.prereq_id SEPARATOR ',') FROM course_prereq p WHERE p.course_id = t.course_id) AS prereqs
             FROM student st JOIN teaching_task_class tc ON tc.class_id = st.class_id
             JOIN teaching_task t ON t.id = tc.task_id JOIN course c ON c.id = t.course_id
            WHERE st.id = ? AND t.term_id = ? ORDER BY t.course_id""", (student_id, term_id)).fetchall()


def elective_tasks(conn, term_id):
    return conn.execute(
        """SELECT t.id AS task_id, t.course_id, c.name AS course_name, t.hours, t.nature,
                  (SELECT COUNT(*) FROM section s WHERE s.task_id = t.id) AS n_sections,
                  (SELECT GROUP_CONCAT(p.prereq_id ORDER BY p.prereq_id SEPARATOR ',') FROM course_prereq p WHERE p.course_id = t.course_id) AS prereqs
             FROM teaching_task t JOIN course c ON c.id = t.course_id
            WHERE t.term_id = ? AND t.nature = '任选' AND EXISTS (SELECT 1 FROM section s WHERE s.task_id = t.id)
            ORDER BY t.course_id""", (term_id,)).fetchall()


def slot_texts(conn, section_ids):
    """section_id → 课表文字列表，如 ['周一 第1-2节 1-16周 教学楼A101']。"""
    res = {sid: [] for sid in section_ids}
    if not section_ids:
        return res
    q = ",".join("?" * len(section_ids))
    for r in conn.execute(f"SELECT * FROM v_slot WHERE section_id IN ({q}) ORDER BY weekday, start_period",
                          list(section_ids)):
        res[r["section_id"]].append(
            f"{WEEKDAYS[r['weekday']]} 第{r['start_period']}-{r['start_period'] + r['period_count'] - 1}节 "
            f"{r['week_start']}-{r['week_end']}周 {r['building']}{r['room_no']}")
    return res


def task_sections(conn, task_id):
    rows = conn.execute("SELECT * FROM v_section WHERE task_id = ? ORDER BY section_no", (task_id,)).fetchall()
    texts = slot_texts(conn, [r["section_id"] for r in rows])
    return [dict(r, slots=texts[r["section_id"]], remaining=r["capacity"] - r["enrolled_count"]) for r in rows]


def my_enrollments(conn, student_id, term_id):
    rows = conn.execute(
        """SELECT e.task_id, e.section_id, e.selected_at, v.course_id, v.course_name, v.hours, v.nature,
                  v.section_no, v.teachers
             FROM enrollment e JOIN v_section v ON v.section_id = e.section_id
            WHERE e.student_id = ? AND v.term_id = ? ORDER BY v.course_id""", (student_id, term_id)).fetchall()
    texts = slot_texts(conn, [r["section_id"] for r in rows])
    return [dict(r, slots=texts[r["section_id"]]) for r in rows]


def _student_slots(conn, student_id, term_id):
    return conn.execute(
        """SELECT x.*, v.course_name FROM enrollment e JOIN v_slot x ON x.section_id = e.section_id
             JOIN v_section v ON v.section_id = e.section_id
            WHERE e.student_id = ? AND v.term_id = ?""", (student_id, term_id)).fetchall()


def enroll(conn, student_id, section_id, now):
    """选课。全部校验与写入都在存储过程 sp_enroll 的一个事务里完成：
    选课时段 → 必修资格 → 对教学班行加 FOR UPDATE 锁 → 重复 → 时间冲突 → 满员 → 写入选课库。
    返回被选教学班的简要信息；失败抛 EnrollError（消息来自数据库）。"""
    sec = conn.execute(
        """SELECT s.id, s.task_id, t.term_id, t.nature, c.name AS course_name
             FROM section s JOIN teaching_task t ON t.id = s.task_id JOIN course c ON c.id = t.course_id
            WHERE s.id = ?""", (section_id,)).fetchone()
    if sec is None:
        raise EnrollError("教学班不存在")
    code, msg = call(conn, "sp_enroll", student_id, section_id, now)
    if code != 0:
        raise EnrollError(msg)
    return sec


def drop(conn, student_id, task_id, now):
    """退选（存储过程 sp_drop）：只能在自由选课时段内。"""
    code, msg = call(conn, "sp_drop", student_id, task_id, now)
    if code != 0:
        raise EnrollError(msg)
