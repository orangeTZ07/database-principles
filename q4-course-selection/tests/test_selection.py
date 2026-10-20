"""学生选课 / 退选（存储过程 sp_enroll / sp_drop）与并发选课。"""
import threading

from app import db
from app.services import selection

from .support import DbCase, NOW_STR, TERM


class SelectionTests(DbCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # 任选课教学任务（每门课取第 1 个课头）；2023 级学生本学期没有必修课，适合做纯任选课测试
        cls.el = cls.conn.execute(
            """SELECT t.id AS task_id, MIN(s.id) AS section_id FROM teaching_task t JOIN section s ON s.task_id = t.id
                WHERE t.term_id = ? AND t.nature = '任选' GROUP BY t.id ORDER BY t.id""", (TERM,)).fetchall()
        cls.seniors = [r[0] for r in cls.conn.execute(
            "SELECT s.id FROM student s JOIN admin_class c ON c.id = s.class_id WHERE c.enroll_year = 2023 ORDER BY s.id")]
        cls.freshmen = [r[0] for r in cls.conn.execute(
            "SELECT s.id FROM student s JOIN admin_class c ON c.id = s.class_id WHERE c.enroll_year = 2026 ORDER BY s.id")]

    def count(self, section_id):
        return self.one("SELECT enrolled_count FROM section WHERE id = ?", (section_id,))

    def enroll_web(self, student_id, section_id):
        c = self.client(self.username_of(student_id))
        return self.text(self.post(c, "/enroll/join", follow=True, section_id=section_id, term=TERM))

    # ------------------------------------------------------------------
    def test_enroll_success_then_duplicate_then_drop(self):
        sec, task = self.el[0]["section_id"], self.el[0]["task_id"]
        stu = self.seniors[0]
        before = self.count(sec)
        self.assertIn("选课成功", self.enroll_web(stu, sec))
        self.assertEqual(self.count(sec), before + 1)
        self.assertEqual(self.one("SELECT section_id FROM enrollment WHERE student_id = ? AND task_id = ?", (stu, task)), sec)
        self.assertEqual(self.one("SELECT COUNT(*) FROM enrollment_log WHERE student_id = ? AND section_id = ? AND action = '选课'",
                                  (stu, sec)), 1)
        # 同一门课不能重复选（即使是别的课头）
        self.assertIn("已选择本课程", self.enroll_web(stu, sec))
        other = self.one("SELECT MAX(id) FROM section WHERE task_id = ?", (task,))
        if other != sec:
            self.assertIn("已选择本课程", self.enroll_web(stu, other))
        self.assertEqual(self.count(sec), before + 1)
        # 自由选课时段内可退选
        c = self.client(self.username_of(stu))
        r = self.post(c, "/enroll/drop", follow=True, task_id=task, term=TERM)
        self.assertIn("已退选", self.text(r))
        self.assertEqual(self.count(sec), before)
        self.assertIsNone(self.one("SELECT 1 FROM enrollment WHERE student_id = ? AND task_id = ?", (stu, task)))
        self.assertEqual(self.one("SELECT COUNT(*) FROM enrollment_log WHERE student_id = ? AND section_id = ? AND action = '退选'",
                                  (stu, sec)), 1)

    def test_full_section_rejects(self):
        sec = self.el[1]["section_id"]
        self.conn.execute("UPDATE section SET capacity = 1 WHERE id = ?", (sec,))
        self.assertIn("选课成功", self.enroll_web(self.seniors[1], sec))
        self.assertIn("已满", self.enroll_web(self.seniors[2], sec))
        self.assertEqual(self.count(sec), 1)
        self.assertIsNone(self.one("SELECT 1 FROM enrollment WHERE student_id = ? AND section_id = ?", (self.seniors[2], sec)))
        # 有人退选腾出空位后，别人可以选上
        task = self.el[1]["task_id"]
        self.post(self.client(self.username_of(self.seniors[1])), "/enroll/drop", task_id=task)
        self.assertIn("选课成功", self.enroll_web(self.seniors[2], sec))

    def test_time_conflict_rejected(self):
        a, b = self.el[2], self.el[3]
        slot = self.q("SELECT * FROM schedule_slot WHERE section_id = ? ORDER BY id LIMIT 1", (a["section_id"],))[0]
        room = self.one("SELECT id FROM classroom WHERE id NOT IN (SELECT classroom_id FROM schedule_slot) ORDER BY id LIMIT 1")
        # 把课头 b 改到和 a 完全相同的时间（教室另选一间空教室，避免触发教室冲突）
        self.conn.execute("DELETE FROM schedule_slot WHERE section_id = ?", (b["section_id"],))
        self.conn.execute(
            "INSERT INTO schedule_slot(section_id, weekday, start_period, period_count, week_start, week_end, classroom_id) "
            "VALUES (?,?,?,?,?,?,?)",
            (b["section_id"], slot["weekday"], slot["start_period"], slot["period_count"], slot["week_start"], slot["week_end"], room))
        stu = self.seniors[3]
        self.assertIn("选课成功", self.enroll_web(stu, a["section_id"]))
        msg = self.enroll_web(stu, b["section_id"])
        self.assertIn("上课时间冲突", msg)
        self.assertIsNone(self.one("SELECT 1 FROM enrollment WHERE student_id = ? AND task_id = ?", (stu, b["task_id"])))
        # 另一个学生没选 a，就能选 b
        self.assertIn("选课成功", self.enroll_web(self.seniors[4], b["section_id"]))

    def test_selection_window(self):
        sec, task = self.el[4]["section_id"], self.el[4]["task_id"]
        stu = self.seniors[5]
        for late in ("2026-10-18 23:59", "2026-11-19 18:01", "2027-03-01 09:00"):
            with self.assertRaises(selection.EnrollError) as cm:
                selection.enroll(self.conn, stu, sec, late)
            self.assertIn("选课时段", str(cm.exception))
        selection.enroll(self.conn, stu, sec, "2026-10-19 08:00")             # 窗口起点当刻可选
        with self.assertRaises(selection.EnrollError) as cm:                   # 窗口外不能退选
            selection.drop(self.conn, stu, task, "2026-12-01 00:00")
        self.assertIn("选课时段", str(cm.exception))
        selection.drop(self.conn, stu, task, "2026-11-19 18:00")               # 窗口终点当刻可退

    def test_required_course_only_for_own_class(self):
        stu = self.freshmen[0]
        cls = self.one("SELECT class_id FROM student WHERE id = ?", (stu,))
        own = self.one("""SELECT MIN(s.id) FROM section s JOIN teaching_task_class tc ON tc.task_id = s.task_id
                           JOIN teaching_task t ON t.id = s.task_id
                          WHERE tc.class_id = ? AND t.term_id = ? AND t.nature = '必修'""", (cls, TERM))
        self.assertIsNotNone(own)
        foreign = self.one("""SELECT MIN(s.id) FROM section s JOIN teaching_task t ON t.id = s.task_id
                               WHERE t.term_id = ? AND t.nature = '必修'
                                 AND NOT EXISTS (SELECT 1 FROM teaching_task_class tc WHERE tc.task_id = t.id AND tc.class_id = ?)""",
                           (TERM, cls))
        self.assertIsNotNone(foreign)
        self.assertIn("不是你所在班级", self.enroll_web(stu, foreign))
        self.assertIn("选课成功", self.enroll_web(stu, own))

    def test_non_students_cannot_enroll(self):
        sec = self.el[5]["section_id"]
        for who in ("admin", "academic", self.first_user("teacher")):
            c = self.client(who)
            self.assertEqual(self.post(c, "/enroll/join", section_id=sec).status_code, 403, who)
        self.assertEqual(self.client(self.username_of(self.seniors[6])).get("/enroll/course/999999").status_code, 404)

    def test_bad_requests_are_handled(self):
        c = self.client(self.username_of(self.seniors[7]))
        self.assertIn("教学班不存在", self.text(self.post(c, "/enroll/join", follow=True, section_id=987654)))
        self.assertIn("请求有误", self.text(self.post(c, "/enroll/join", follow=True, section_id="abc")))

    # ------------------------------------------------------------------ 并发
    def race(self, section_id, students):
        """所有学生同时（线程栅栏对齐）调用 sp_enroll，每个线程独立一条数据库连接。"""
        barrier, results, lock = threading.Barrier(len(students)), [], threading.Lock()

        def worker(sid):
            conn = db.connect(self.cfg)
            try:
                barrier.wait()
                res = db.call(conn, "sp_enroll", sid, section_id, NOW_STR)
            finally:
                conn.close()
            with lock:
                results.append(res)

        threads = [threading.Thread(target=worker, args=(s,)) for s in students]
        [t.start() for t in threads]
        [t.join(60) for t in threads]
        return results

    def check_race(self, section_id, capacity, students):
        self.conn.execute("UPDATE section SET capacity = ? WHERE id = ?", (capacity, section_id))
        results = self.race(section_id, students)
        self.assertEqual(len(results), len(students), "有线程没有返回")
        codes = sorted(c for c, _ in results)
        self.assertEqual(set(codes), {0, 6}, f"只应出现 成功(0) 和 已满(6)：{results}")
        self.assertEqual(codes.count(0), capacity)
        self.assertEqual(codes.count(6), len(students) - capacity)
        self.assertEqual(self.count(section_id), capacity)                                # 不超卖
        self.assertEqual(self.one("SELECT COUNT(*) FROM enrollment WHERE section_id = ?", (section_id,)), capacity)
        self.assertEqual(self.one("SELECT COUNT(*) FROM enrollment_log WHERE section_id = ? AND action = '选课'", (section_id,)),
                         capacity)

    def test_concurrent_enroll_never_oversells(self):
        self.check_race(self.el[5]["section_id"], 5, self.seniors[10:40])      # 30 人抢 5 个名额

    def test_concurrent_enroll_last_seat(self):
        self.check_race(self.el[6]["section_id"], 1, self.seniors[40:60])      # 20 人抢最后 1 个名额

    def test_concurrent_same_student_enrolls_once(self):
        sec = self.el[0]["section_id"]
        stu = self.seniors[70]
        results = self.race(sec, [stu] * 8)
        self.assertEqual(sorted(c for c, _ in results).count(0), 1, results)   # 同一学生并发重复提交只成功一次
        self.assertEqual(self.one("SELECT COUNT(*) FROM enrollment WHERE student_id = ? AND section_id = ?", (stu, sec)), 1)
