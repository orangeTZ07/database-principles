"""教务科完整流程：录入学期 → 生成教学任务 → 数据挖掘采纳预测 → 拆分课头 → 分配教师 → 自动排课。
流程全部走 Web 接口，然后直接查数据库验证结果；另有课头拆分规则测试。"""
import math

from app.services import sections, timetable

from .support import CUR_TERM, DbCase, TERM

NEW = "2027-2028-2"


class AcademicFlowTests(DbCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        c = cls.client("academic")
        post = lambda url, **kw: cls.post(c, url, follow=True, **kw)
        cls.flash = [cls.text(post("/progress/term/add", year_start=2027, term_no=2, weeks=16, start_date="2028-02-21",
                                  select_start="2027-10-19T08:00", select_end="2027-11-19T18:00"))]
        cls.flash.append(cls.text(post(f"/progress/term/{NEW}/generate")))
        for (cid,) in cls.conn.execute("SELECT course_id FROM teaching_task WHERE term_id = ? AND nature = '任选'", (NEW,)).fetchall():
            cls.flash.append(cls.text(post("/mining/adopt", term=NEW, course=cid, split=1)))
        cls.flash.append(cls.text(post(f"/tasks/term/{NEW}/split")))
        cls.flash.append(cls.text(post(f"/tasks/term/{NEW}/auto-teachers")))
        cls.flash.append(cls.text(post(f"/timetable/term/{NEW}/auto")))

    def test_term_and_tasks_created(self):
        self.assertIn("已录入学期 2027-2028-2", self.flash[0])
        self.assertIn("教学任务已生成", self.flash[1])
        self.assertIsNotNone(self.one("SELECT select_start FROM term WHERE id = ?", (NEW,)))
        self.assertGreater(self.one("SELECT COUNT(*) FROM teaching_task WHERE term_id = ?", (NEW,)), 10)

    def test_electives_got_forecast_and_were_split(self):
        rows = self.q("""SELECT t.course_id, t.expected_count, f.predicted_count FROM teaching_task t
                           JOIN course_forecast f ON f.term_id = t.term_id AND f.course_id = t.course_id
                          WHERE t.term_id = ? AND t.nature = '任选'""", (NEW,))
        self.assertGreater(len(rows), 0)
        for r in rows:
            self.assertEqual(r["expected_count"], r["predicted_count"])
            self.assertGreater(r["predicted_count"], 0)

    def test_section_split_rules(self):
        limit, room_max = sections.section_limit(self.conn), sections.max_room_capacity(self.conn)
        per = min(limit, room_max)
        tasks = self.q("SELECT * FROM teaching_task WHERE term_id = ?", (NEW,))
        checked = 0
        for t in tasks:
            basis = sections.basis_count(t)
            secs = self.q("SELECT capacity FROM section WHERE task_id = ? ORDER BY section_no", (t["id"],))
            if basis <= 0:
                self.assertEqual(len(secs), 0, "没有人数依据的任务不应有课头")
                continue
            n = math.ceil(basis / per)
            self.assertEqual(len(secs), n, f"{t['course_id']} 人数 {basis}")
            self.assertLessEqual(max(s[0] for s in secs), per)               # 不超过班额限额，也不超过教室最大容量
            self.assertGreaterEqual(sum(s[0] for s in secs), basis)          # 所有课头容量足够容纳全部人数
            checked += 1
        self.assertGreater(checked, 10)
        # 必修课超过限额必须拆分：计科/软工/信计三个 40 人班合上 120 人 → 2 个 60 人课头
        row = self.q("SELECT id, required_count FROM teaching_task WHERE term_id = ? AND nature = '必修' AND required_count > ? LIMIT 1",
                     (NEW, limit))
        self.assertGreater(len(row), 0)
        self.assertGreaterEqual(self.one("SELECT COUNT(*) FROM section WHERE task_id = ?", (row[0]["id"],)), 2)

    def test_every_section_has_exactly_one_lead_and_qualified_teachers(self):
        secs = self.q("SELECT s.id, t.course_id FROM section s JOIN teaching_task t ON t.id = s.task_id WHERE t.term_id = ?", (NEW,))
        for s in secs:
            self.assertEqual(self.one("SELECT COUNT(*) FROM section_teacher WHERE section_id = ? AND is_lead = 1", (s["id"],)), 1)
            unqualified = self.q("""SELECT st.teacher_id FROM section_teacher st
                                     WHERE st.section_id = ? AND NOT EXISTS (SELECT 1 FROM teacher_course tc
                                              WHERE tc.teacher_id = st.teacher_id AND tc.course_id = ?)""", (s["id"], s["course_id"]))
            self.assertEqual(len(unqualified), 0)

    def test_timetable_complete_and_conflict_free(self):
        self.assertEqual(timetable.find_conflicts(self.conn, NEW), [])
        unscheduled = self.q("""SELECT s.id FROM section s JOIN teaching_task t ON t.id = s.task_id
                                 WHERE t.term_id = ? AND NOT EXISTS (SELECT 1 FROM schedule_slot x WHERE x.section_id = s.id)""", (NEW,))
        self.assertEqual(len(unscheduled), 0, "所有课头都应排上课")
        # 每个课头每周上课节数 × 周数 ≥ 学时
        short = self.q("""SELECT s.id FROM section s JOIN teaching_task t ON t.id = s.task_id JOIN schedule_slot x ON x.section_id = s.id
                           WHERE t.term_id = ? GROUP BY s.id, t.hours
                          HAVING SUM(x.period_count * (x.week_end - x.week_start + 1)) < t.hours""", (NEW,))
        self.assertEqual(len(short), 0)
        # 教室容量不小于教学班人数上限
        small = self.q("""SELECT x.id FROM schedule_slot x JOIN section s ON s.id = x.section_id JOIN teaching_task t ON t.id = s.task_id
                           JOIN classroom r ON r.id = x.classroom_id WHERE t.term_id = ? AND r.capacity < s.capacity""", (NEW,))
        self.assertEqual(len(small), 0)
        # 教室、教师双重 SQL 复核：同学期内没有重叠占用
        room_clash = self.q("""SELECT a.id FROM schedule_slot a JOIN schedule_slot b
                                 ON a.classroom_id = b.classroom_id AND a.weekday = b.weekday AND a.id < b.id
                                AND a.start_period < b.start_period + b.period_count AND b.start_period < a.start_period + a.period_count
                                AND a.week_start <= b.week_end AND b.week_start <= a.week_end
                                JOIN section sa ON sa.id = a.section_id JOIN teaching_task ta ON ta.id = sa.task_id
                                JOIN section sb ON sb.id = b.section_id JOIN teaching_task tb ON tb.id = sb.task_id
                               WHERE ta.term_id = ? AND tb.term_id = ?""", (NEW, NEW))
        self.assertEqual(len(room_clash), 0)


class SchedulerPolicyTests(DbCase):
    """排课策略：同一课头尽量保持教室不变；必须换教室时就近（教学区 → 教学楼 → 楼层）。只读，用种子数据。"""

    def test_same_section_keeps_the_same_classroom(self):
        rows = self.q("""SELECT x.section_id, COUNT(*) AS blocks, COUNT(DISTINCT x.classroom_id) AS rooms
                           FROM schedule_slot x GROUP BY x.section_id""")
        multi = [r for r in rows if r["blocks"] > 1]          # 每周上 4 节及以上的课拆成多个时间块
        self.assertGreater(len(multi), 5)
        same_room = sum(1 for r in multi if r["rooms"] == 1)
        self.assertGreaterEqual(same_room / len(multi), 0.9, "多数课头的各时间块应在同一间教室")
        self.assertTrue(all(r["rooms"] <= 2 for r in multi))

    def test_room_distance_prefers_same_area_then_building_then_floor(self):
        a = {"area": "东区", "building": "东一楼", "floor": 1, "room_no": "101"}
        same_floor = dict(a, room_no="104")
        other_floor = dict(a, floor=3, room_no="301")
        other_building = dict(a, building="东二楼")
        other_area = dict(a, area="西区", building="西一楼")
        d = timetable.room_distance
        self.assertLess(d(a, same_floor), d(a, other_floor))
        self.assertLess(d(a, other_floor), d(a, other_building))
        self.assertLess(d(a, other_building), d(a, other_area))

    def test_pick_room_with_anchor_is_nearest_free_room(self):
        occ = timetable.Occupancy(self.conn, TERM)
        sid = next(iter(occ.sec))
        busy = next(x for lst in occ.by_day.values() for x in lst)             # 随便一个已被占用的教室时间
        anchor = busy["room"]
        args = (sid, busy["weekday"], busy["start"], busy["count"], 1, 16)
        self.assertFalse(occ.room_free(anchor, *args[1:]))
        got = occ.pick_room(*args, anchor_id=anchor)
        self.assertIsNotNone(got)
        self.assertNotEqual(got, anchor)
        need = occ.sec[sid]["capacity"]
        cands = [r for r in occ.rooms.values() if r["capacity"] >= need and occ.room_free(r["id"], *args[1:])]
        best = min(timetable.room_distance(occ.rooms[anchor], r) for r in cands)
        self.assertEqual(timetable.room_distance(occ.rooms[anchor], occ.rooms[got]), best)
        self.assertTrue(occ.room_free(got, *args[1:]))
        self.assertGreaterEqual(occ.rooms[got]["capacity"], need)

    def test_seed_timetables_have_no_conflicts(self):
        for term in (TERM, CUR_TERM):
            self.assertEqual(timetable.find_conflicts(self.conn, term), [], term)


class SectionSplitTests(DbCase):
    """拆分课头：人数超过「教学班人数限额」或「教室最大容量」就要拆（限额在 setting 表可配置）。"""

    def task(self, course):
        return self.one("SELECT id FROM teaching_task WHERE term_id = ? AND course_id = ?", (TERM, course))

    def caps(self, task_id):
        return [r[0] for r in self.q("SELECT capacity FROM section WHERE task_id = ? ORDER BY section_no", (task_id,))]

    def test_split_by_limit(self):
        t = self.task("MA102")                       # 三个 40 人班合上，必修 120 人
        self.assertEqual(self.one("SELECT required_count FROM teaching_task WHERE id = ?", (t,)), 120)
        self.assertEqual(sections.split_task(self.conn, t), (2, 60))
        self.assertEqual(self.caps(t), [60, 60])
        self.assertEqual(sections.split_task(self.conn, t, limit=30), (4, 30))
        self.assertEqual(sections.split_task(self.conn, t, limit=50), (3, 40))
        self.assertEqual(self.caps(t), [40, 40, 40])
        self.assertEqual(sections.split_task(self.conn, t, limit=500), (1, 120))     # 限额再大也受教室最大容量 120 约束
        sections.split_task(self.conn, t, limit=1000)
        self.assertEqual(self.caps(t), [120])

    def test_split_uses_expected_count_for_electives(self):
        t = self.task("CS403")
        self.conn.execute("UPDATE teaching_task SET expected_count = 177 WHERE id = ?", (t,))
        self.assertEqual(sections.split_task(self.conn, t), (3, 59))
        self.conn.execute("UPDATE teaching_task SET expected_count = 0 WHERE id = ?", (t,))
        with self.assertRaises(ValueError):
            sections.split_task(self.conn, t)

    def test_configurable_limit_in_setting_table(self):
        t = self.task("EN102")
        self.conn.execute("UPDATE setting SET setting_value = '40' WHERE setting_key = 'section_limit'")
        try:
            self.assertEqual(sections.split_task(self.conn, t), (3, 40))
        finally:
            self.conn.execute("UPDATE setting SET setting_value = '60' WHERE setting_key = 'section_limit'")
        self.assertEqual(sections.split_task(self.conn, t), (2, 60))

    def test_cannot_resplit_after_students_enrolled(self):
        t = self.task("MA103")
        sections.split_task(self.conn, t)
        stu = self.one("""SELECT st.id FROM student st JOIN teaching_task_class tc ON tc.class_id = st.class_id
                           WHERE tc.task_id = ? ORDER BY st.id LIMIT 1""", (t,))
        sec = self.one("SELECT id FROM section WHERE task_id = ? ORDER BY section_no LIMIT 1", (t,))
        self.conn.execute("INSERT INTO enrollment(student_id, task_id, section_id) VALUES (?,?,?)", (stu, t, sec))
        with self.assertRaises(ValueError) as cm:
            sections.split_task(self.conn, t)
        self.assertIn("已有学生选课", str(cm.exception))
