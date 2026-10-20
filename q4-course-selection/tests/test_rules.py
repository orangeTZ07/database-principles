"""数据库层规则：约束、触发器、外键、存储过程。这些规则不依赖应用代码，手写 SQL 也绕不过。"""
from .support import CUR_TERM, DbCase, NOW_STR, TERM

CODE_SIGNAL, CODE_CHECK, CODE_DUP, CODE_FK_PARENT, CODE_FK_CHILD = 1644, 3819, 1062, 1451, 1452


class ConstraintTests(DbCase):
    def test_check_constraints(self):
        for sql in ("INSERT INTO classroom(area, building, room_no, floor, is_multimedia, capacity) VALUES ('东区','X楼','901',1,1,0)",
                    "INSERT INTO classroom(area, building, room_no, floor, is_multimedia, capacity) VALUES ('东区','X楼','902',1,2,50)",
                    "UPDATE section SET capacity = 0 WHERE id = (SELECT m FROM (SELECT MIN(id) AS m FROM section) x)",
                    "UPDATE curriculum_plan SET semester_no = 9 WHERE id = (SELECT m FROM (SELECT MIN(id) AS m FROM curriculum_plan) x)",
                    "UPDATE term SET weeks = 31 WHERE id = '2026-2027-1'"):
            self.assertEqual(self.db_error(sql)[0], CODE_CHECK, sql)

    def test_enum_rejects_unknown_value(self):
        code, _ = self.db_error("UPDATE teaching_task SET nature = '选修' WHERE id = (SELECT m FROM (SELECT MIN(id) AS m FROM teaching_task) x)")
        self.assertEqual(code, 1265)

    def test_foreign_keys(self):
        self.assertEqual(self.db_error("DELETE FROM course WHERE id = 'CS101'")[0], CODE_FK_PARENT)
        self.assertEqual(self.db_error("DELETE FROM major")[0], CODE_FK_PARENT)
        self.assertEqual(self.db_error("INSERT INTO student(id, name, gender, class_id) VALUES ('X1','测试','男',99999)")[0], CODE_FK_CHILD)

    def test_enrollment_must_match_task_and_section(self):
        """(section_id, task_id) 是指向 section(id, task_id) 的复合外键：选课记录里课头必须属于该教学任务。"""
        sec = self.one("SELECT id FROM section WHERE enrolled_count < capacity ORDER BY id LIMIT 1")
        task = self.one("SELECT id FROM teaching_task WHERE id <> (SELECT task_id FROM section WHERE id = ?) LIMIT 1", (sec,))
        stu = self.one("SELECT id FROM student ORDER BY id DESC LIMIT 1")
        self.assertEqual(self.db_error("INSERT INTO enrollment(student_id, task_id, section_id) VALUES (?,?,?)", (stu, task, sec))[0],
                         CODE_FK_CHILD)

    def test_section_enrolled_count_check(self):
        sec = self.one("SELECT id FROM section ORDER BY id DESC LIMIT 1")
        self.assertEqual(self.db_error("UPDATE section SET enrolled_count = capacity + 1 WHERE id = ?", (sec,))[0], CODE_CHECK)


class TriggerTests(DbCase):
    def test_prereq_cannot_be_self_or_cyclic(self):
        # CS201 ← CS102 ← CS101：再让 CS101 以 CS201 为先导就成环
        code, msg = self.db_error("INSERT INTO course_prereq(course_id, prereq_id) VALUES ('CS101', 'CS201')")
        self.assertEqual(code, CODE_SIGNAL)
        self.assertIn("循环", msg)
        code, msg = self.db_error("INSERT INTO course_prereq(course_id, prereq_id) VALUES ('CS101', 'CS301')")   # 间接环（CS301←CS201←CS102←CS101）
        self.assertEqual(code, CODE_SIGNAL)
        code, msg = self.db_error("INSERT INTO course_prereq(course_id, prereq_id) VALUES ('MA101', 'MA101')")
        self.assertEqual(code, CODE_SIGNAL)
        self.assertIn("自己", msg)
        # 不成环的先导可以加
        self.conn.execute("INSERT INTO course_prereq(course_id, prereq_id) VALUES ('CS404', 'MA101')")
        self.conn.execute("DELETE FROM course_prereq WHERE course_id = 'CS404' AND prereq_id = 'MA101'")

    def test_prereq_cycle_rejected_in_web_form(self):
        c = self.client("research")
        # 给 CS101 勾选 CS201 作先导 → 环；页面提示且数据库不变
        before = self.one("SELECT COUNT(*) FROM course_prereq")
        r = self.post(c, "/plan/courses/CS101/prereq", follow=True, item="CS201")
        self.assertIn("循环依赖", self.text(r))
        self.assertEqual(self.one("SELECT COUNT(*) FROM course_prereq"), before)

    def test_teacher_must_be_qualified(self):
        sec, teacher = self.q(
            """SELECT s.id, te.id FROM section s JOIN teaching_task t ON t.id = s.task_id CROSS JOIN teacher te
                WHERE t.term_id = ? AND NOT EXISTS (SELECT 1 FROM teacher_course tc
                                                     WHERE tc.teacher_id = te.id AND tc.course_id = t.course_id)
                ORDER BY s.id, te.id LIMIT 1""", (TERM,))[0]
        code, msg = self.db_error("INSERT INTO section_teacher(section_id, teacher_id, is_lead) VALUES (?,?,0)", (sec, teacher))
        self.assertEqual(code, CODE_SIGNAL)
        self.assertIn("任课资格", msg)

    def test_one_lead_teacher_per_section_but_many_teachers_allowed(self):
        sec, lead_teacher = self.q(
            "SELECT st.section_id, st.teacher_id FROM section_teacher st WHERE st.is_lead = 1 ORDER BY st.section_id LIMIT 1")[0]
        qualified = self.one(
            """SELECT tc.teacher_id FROM section s JOIN teaching_task t ON t.id = s.task_id
                 JOIN teacher_course tc ON tc.course_id = t.course_id
                WHERE s.id = ? AND tc.teacher_id NOT IN (SELECT teacher_id FROM section_teacher WHERE section_id = ?)
                ORDER BY tc.teacher_id LIMIT 1""", (sec, sec))
        self.assertIsNotNone(qualified, "种子数据里每门课至少有 3 位可上课教师")
        # 第二位主讲：违反生成列 lead_key 上的唯一索引
        self.assertEqual(self.db_error("INSERT INTO section_teacher(section_id, teacher_id, is_lead) VALUES (?,?,1)", (sec, qualified))[0],
                         CODE_DUP)
        # 非主讲（多教师合上）可以
        self.conn.execute("INSERT INTO section_teacher(section_id, teacher_id, is_lead) VALUES (?,?,0)", (sec, qualified))
        self.assertEqual(self.one("SELECT COUNT(*) FROM section_teacher WHERE section_id = ?", (sec,)), 2)
        self.assertEqual(self.one("SELECT COUNT(*) FROM section_teacher WHERE section_id = ? AND is_lead = 1", (sec,)), 1)
        self.conn.execute("DELETE FROM section_teacher WHERE section_id = ? AND teacher_id = ?", (sec, qualified))

    def test_classroom_time_conflict(self):
        a = self.q("""SELECT x.* FROM schedule_slot x JOIN section s ON s.id = x.section_id JOIN teaching_task t ON t.id = s.task_id
                       WHERE t.term_id = ? ORDER BY x.id LIMIT 1""", (CUR_TERM,))[0]
        other = self.one("""SELECT s.id FROM section s JOIN teaching_task t ON t.id = s.task_id
                             WHERE t.term_id = ? AND s.id <> ? ORDER BY s.id LIMIT 1""", (CUR_TERM, a["section_id"]))
        ins = ("INSERT INTO schedule_slot(section_id, weekday, start_period, period_count, week_start, week_end, classroom_id) "
               "VALUES (?,?,?,?,?,?,?)")
        # 同教室、同星期、节次重叠（只重叠最后 1 节）、周次重叠 → 冲突
        start = max(1, a["start_period"] + a["period_count"] - 1)
        code, msg = self.db_error(ins, (other, a["weekday"], start, 1, a["week_start"], a["week_start"], a["classroom_id"]))
        self.assertEqual(code, CODE_SIGNAL)
        self.assertIn("教室时间冲突", msg)
        # 同一时间换一间没人用的教室就不冲突
        free = self.one("SELECT id FROM classroom WHERE id NOT IN (SELECT classroom_id FROM schedule_slot) ORDER BY id LIMIT 1")
        s = self.conn.execute(ins, (other, a["weekday"], a["start_period"], a["period_count"], a["week_start"], a["week_end"], free))
        self.conn.execute("DELETE FROM schedule_slot WHERE id = ?", (s.lastrowid,))

    def test_slot_weeks_cannot_exceed_term_calendar(self):
        sec = self.one("""SELECT s.id FROM section s JOIN teaching_task t ON t.id = s.task_id WHERE t.term_id = ?
                           ORDER BY s.id LIMIT 1""", (TERM,))
        room = self.one("SELECT id FROM classroom ORDER BY id DESC LIMIT 1")
        code, msg = self.db_error(
            "INSERT INTO schedule_slot(section_id, weekday, start_period, period_count, week_start, week_end, classroom_id) "
            "VALUES (?,?,?,?,?,?,?)", (sec, 1, 1, 2, 1, 17, room))     # 学期只有 16 周
        self.assertEqual(code, CODE_SIGNAL)
        self.assertIn("教学日历周数", msg)

    def test_enrollment_capacity_trigger_guards_direct_inserts(self):
        """绕开存储过程直接 INSERT 选课记录，BEFORE INSERT 触发器仍会按容量拒绝。"""
        row = self.q("""SELECT s.id, s.task_id FROM section s JOIN teaching_task t ON t.id = s.task_id
                         WHERE t.term_id = ? AND t.nature = '任选' ORDER BY s.id LIMIT 1""", (TERM,))[0]
        sec, task = row["id"], row["task_id"]
        a, b = [r[0] for r in self.q("SELECT id FROM student ORDER BY id DESC LIMIT 2")]
        self.conn.execute("UPDATE section SET capacity = 1 WHERE id = ?", (sec,))
        ins = "INSERT INTO enrollment(student_id, task_id, section_id) VALUES (?,?,?)"
        self.conn.execute(ins, (a, task, sec))
        code, msg = self.db_error(ins, (b, task, sec))
        self.assertEqual((code, "已满" in msg), (CODE_SIGNAL, True))
        self.assertEqual(self.one("SELECT enrolled_count FROM section WHERE id = ?", (sec,)), 1)
        self.conn.execute("DELETE FROM enrollment WHERE student_id = ? AND task_id = ?", (a, task))
        self.assertEqual(self.one("SELECT enrolled_count FROM section WHERE id = ?", (sec,)), 0)   # 触发器随退选回减
        self.assertEqual(self.one("SELECT COUNT(*) FROM enrollment_log WHERE student_id = ? AND section_id = ?", (a, sec)), 2)

    def test_counts_and_log_consistent_with_enrollment(self):
        bad = self.q("""SELECT s.id FROM section s LEFT JOIN enrollment e ON e.section_id = s.id
                         GROUP BY s.id, s.enrolled_count HAVING s.enrolled_count <> COUNT(e.student_id)""")
        self.assertEqual(len(bad), 0)


class StoredRoutineTests(DbCase):
    def generate(self, term):
        self.conn.execute("CALL sp_generate_tasks(?, @created, @updated)", (term,))
        row = self.conn.execute("SELECT @created AS c, @updated AS u").fetchone()
        return row["c"], row["u"]

    def test_generate_tasks_is_idempotent(self):
        before = self.q("SELECT id, course_id, nature, required_count, expected_count FROM teaching_task WHERE term_id = ? ORDER BY id", (TERM,))
        created, updated = self.generate(TERM)
        self.assertEqual((created, updated), (0, len(before)))
        after = self.q("SELECT id, course_id, nature, required_count, expected_count FROM teaching_task WHERE term_id = ? ORDER BY id", (TERM,))
        self.assertEqual([tuple(r) for r in before], [tuple(r) for r in after])     # 任务 id、必修人数、任选课预计人数都不变

    def test_generate_tasks_unknown_term(self):
        code, msg = self.db_error("CALL sp_generate_tasks('2099-2100-1', @c, @u)")
        self.assertEqual(code, CODE_SIGNAL)
        self.assertIn("学期不存在", msg)

    def test_generate_tasks_matches_curriculum_plan(self):
        """用 Python 独立重算「培养方案 × 班级人数 → 教学任务」，与存储过程的结果逐项对比。"""
        self.conn.execute("INSERT INTO term(id, year_start, term_no, weeks, start_date) VALUES ('2027-2028-2', 2027, 2, 16, '2028-02-21')")
        created, updated = self.generate("2027-2028-2")
        expected = {}
        classes = self.q("""SELECT c.id, c.major_id, c.enroll_year, (SELECT COUNT(*) FROM student s WHERE s.class_id = c.id) AS n
                              FROM admin_class c""")
        for cl in classes:
            sem = (2027 - cl["enroll_year"]) * 2 + 2
            for p in self.q("SELECT course_id, hours, nature FROM curriculum_plan WHERE major_id = ? AND semester_no = ?",
                            (cl["major_id"], sem)):
                e = expected.setdefault(p["course_id"], {"hours": p["hours"], "required": 0, "has_required": False, "classes": set()})
                if p["nature"] == "必修":
                    e["required"] += cl["n"]
                    e["has_required"] = True
                    e["classes"].add(cl["id"])
        self.assertEqual((created, updated), (len(expected), 0))
        for t in self.q("SELECT * FROM teaching_task WHERE term_id = '2027-2028-2'"):
            e = expected[t["course_id"]]
            self.assertEqual(t["nature"], "必修" if e["has_required"] else "任选", t["course_id"])
            self.assertEqual(t["required_count"], e["required"], t["course_id"])
            got = {r[0] for r in self.q("SELECT class_id FROM teaching_task_class WHERE task_id = ?", (t["id"],))}
            self.assertEqual(got, e["classes"], t["course_id"])
        self.assertEqual(self.one("SELECT COUNT(*) FROM teaching_task WHERE term_id = '2027-2028-2'"), len(expected))
