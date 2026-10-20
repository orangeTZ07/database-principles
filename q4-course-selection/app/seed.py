"""演示数据：组织机构、专业、班级、学生、教师、课程、培养方案、教室、历年学期与选课记录，
并对「当前选课学期」自动走完 生成教学任务 → 数据挖掘 → 拆分课头 → 分配教师 → 自动排课。
初始密码统一为 123（账号 admin / research / academic 及教师、学生的拼音账号）。"""
import random
from datetime import datetime, timedelta

from werkzeug.security import generate_password_hash

from .db import tx
from .services import mining, progress, sections, timetable
from .services.common import semester_no

SURNAMES = [("张", "zhang"), ("王", "wang"), ("李", "li"), ("赵", "zhao"), ("陈", "chen"), ("刘", "liu"),
            ("杨", "yang"), ("黄", "huang"), ("周", "zhou"), ("吴", "wu"), ("徐", "xu"), ("孙", "sun"),
            ("马", "ma"), ("朱", "zhu"), ("胡", "hu"), ("郭", "guo"), ("何", "he"), ("林", "lin"),
            ("高", "gao"), ("罗", "luo")]
GIVEN = [("伟", "wei"), ("芳", "fang"), ("娜", "na"), ("敏", "min"), ("静", "jing"), ("磊", "lei"),
         ("洋", "yang"), ("勇", "yong"), ("艳", "yan"), ("杰", "jie"), ("涛", "tao"), ("明", "ming"),
         ("超", "chao"), ("霞", "xia"), ("平", "ping"), ("刚", "gang"), ("婷", "ting"), ("浩", "hao"),
         ("宇", "yu"), ("雪", "xue"), ("欣", "xin"), ("凯", "kai"), ("琳", "lin"), ("鹏", "peng"), ("颖", "ying")]

COLLEGES = {
    "信息工程学院": ["计算机科学系", "软件工程系"],
    "数理学院": ["数学系", "物理系"],
    "外国语学院": ["外语系"],
    "人文与艺术学院": ["通识教育系"],
}
TEACHERS_PER_DEPT = {"计算机科学系": 8, "软件工程系": 5, "数学系": 5, "物理系": 2, "外语系": 4, "通识教育系": 4}
MAJORS = [("计算机科学与技术", "信息工程学院", "计科"), ("软件工程", "信息工程学院", "软工"),
          ("信息与计算科学", "数理学院", "信计")]
ENROLL_YEARS = [2023, 2024, 2025, 2026]
STUDENTS_PER_CLASS = 40

# 课程号, 名称, 学时, 学分, 开课学院, 先导课程
COURSES = [
    ("MA101", "高等数学A(一)", 64, 4.0, "数理学院", []),
    ("MA102", "高等数学A(二)", 64, 4.0, "数理学院", ["MA101"]),
    ("MA103", "线性代数", 48, 3.0, "数理学院", []),
    ("MA201", "概率论与数理统计", 48, 3.0, "数理学院", ["MA102"]),
    ("MA301", "数值分析", 32, 2.0, "数理学院", ["MA103"]),
    ("MA302", "数学建模", 32, 2.0, "数理学院", ["MA201"]),
    ("PH101", "大学物理", 48, 3.0, "数理学院", ["MA101"]),
    ("EN101", "大学英语(一)", 48, 3.0, "外国语学院", []),
    ("EN102", "大学英语(二)", 48, 3.0, "外国语学院", ["EN101"]),
    ("EN201", "大学英语(三)", 32, 2.0, "外国语学院", ["EN102"]),
    ("CS101", "程序设计基础", 64, 4.0, "信息工程学院", []),
    ("CS102", "面向对象程序设计", 48, 3.0, "信息工程学院", ["CS101"]),
    ("CS201", "数据结构", 64, 4.0, "信息工程学院", ["CS102"]),
    ("CS202", "离散数学", 48, 3.0, "信息工程学院", ["MA101"]),
    ("CS203", "计算机组成原理", 48, 3.0, "信息工程学院", ["CS101"]),
    ("CS301", "操作系统", 64, 4.0, "信息工程学院", ["CS201", "CS203"]),
    ("CS302", "计算机网络", 48, 3.0, "信息工程学院", ["CS203"]),
    ("CS303", "数据库原理", 48, 3.0, "信息工程学院", ["CS201"]),
    ("CS304", "算法设计与分析", 48, 3.0, "信息工程学院", ["CS201"]),
    ("CS305", "编译原理", 48, 3.0, "信息工程学院", ["CS201", "CS202"]),
    ("SE301", "软件工程", 48, 3.0, "信息工程学院", ["CS102"]),
    ("SE302", "软件测试", 32, 2.0, "信息工程学院", ["SE301"]),
    ("SE401", "软件项目管理", 32, 2.0, "信息工程学院", ["SE301"]),
    ("CS401", "人工智能导论", 32, 2.0, "信息工程学院", ["CS201"]),
    ("CS402", "机器学习导论", 32, 2.0, "信息工程学院", ["MA201"]),
    ("CS403", "Python数据分析", 32, 2.0, "信息工程学院", ["CS101"]),
    ("CS404", "Web开发技术", 32, 2.0, "信息工程学院", ["CS102"]),
    ("EL101", "艺术鉴赏", 32, 2.0, "人文与艺术学院", []),
    ("EL102", "摄影基础", 32, 2.0, "人文与艺术学院", []),
    ("EL103", "心理健康教育", 32, 2.0, "人文与艺术学院", []),
]
ELECTIVES = {"MA302": 0.12, "CS402": 0.22, "CS403": 0.35, "CS404": 0.25, "EL101": 0.30, "EL102": 0.27, "EL103": 0.20}
EL_ALL = ["CS402", "CS403", "CS404", "EL101", "EL102", "EL103"]

# 培养方案：专业 → 学期 → [(课程号, 性质)]
R = lambda *ids: [(i, "必修") for i in ids]
E = lambda *ids: [(i, "任选") for i in ids]
PLAN = {
    "计算机科学与技术": {
        1: R("MA101", "EN101", "CS101"), 2: R("MA102", "MA103", "EN102", "CS102", "PH101"),
        3: R("MA201", "CS201", "CS202", "CS203", "EN201"), 4: R("CS301", "CS302", "CS304"),
        5: R("CS303", "CS305", "SE301"), 6: R("CS401") + E(*EL_ALL), 7: R("SE401")},
    "软件工程": {
        1: R("MA101", "EN101", "CS101"), 2: R("MA102", "MA103", "EN102", "CS102"),
        3: R("MA201", "CS201", "CS202", "EN201"), 4: R("CS203", "CS302", "SE301", "CS304"),
        5: R("CS301", "CS303", "SE302"), 6: R("CS401", "SE401") + E("CS403", "CS404", "EL101", "EL102", "EL103")},
    "信息与计算科学": {
        1: R("MA101", "EN101", "CS101"), 2: R("MA102", "MA103", "EN102", "CS102"),
        3: R("MA201", "CS201", "PH101", "EN201"), 4: R("CS202", "MA301", "CS304"),
        5: R("CS303"), 6: R("CS401") + E("MA302", "CS403", "EL101", "EL102", "EL103")},
}
HISTORY = [(2023, 1), (2023, 2), (2024, 1), (2024, 2), (2025, 1), (2025, 2), (2026, 1)]
TARGET = (2026, 2)       # 当前正在开放选课的「下学期」


def term_id(y, n):
    return f"{y}-{y + 1}-{n}"


def letters(k):
    s = ""
    while k:
        k, r = divmod(k - 1, 26)
        s = chr(97 + r) + s
    return s


class Names:
    def __init__(self, rng):
        self.rng, self.used = rng, {"admin", "research", "academic"}

    def person(self):
        r = self.rng
        while True:
            s, g1 = r.choice(SURNAMES), r.choice(GIVEN)
            g2 = r.choice(GIVEN) if r.random() < 0.6 else None
            name = s[0] + g1[0] + (g2[0] if g2 else "")
            base = s[1] + g1[1] + (g2[1] if g2 else "")
            acct, k = base, 0
            while acct in self.used:
                k += 1
                acct = base + letters(k)
            self.used.add(acct)
            return name, acct


def seed(conn, now=None):
    now = now or datetime.now()
    rng = random.Random(20261020)
    names = Names(rng)
    pwd = generate_password_hash("123", method="pbkdf2:sha256:100000")   # 同一初始密码，复用哈希以加快初始化

    with tx(conn):
        # ---- 组织机构、专业、班级、学生
        ids = {}
        for college, depts in COLLEGES.items():
            cid = conn.execute("INSERT INTO org_unit(name, kind) VALUES (?, '学院')", (college,)).lastrowid
            ids[college] = cid
            for d in depts:
                ids[d] = conn.execute("INSERT INTO org_unit(name, kind, parent_id) VALUES (?, '系', ?)", (d, cid)).lastrowid
        major_id = {}
        for i, (m, college, short) in enumerate(MAJORS, 1):
            major_id[m] = conn.execute("INSERT INTO major(name, college_id) VALUES (?,?)", (m, ids[college])).lastrowid
            for y in ENROLL_YEARS:
                cls = conn.execute("INSERT INTO admin_class(name, major_id, enroll_year) VALUES (?,?,?)",
                                   (f"{short}{str(y)[2:]}01班", major_id[m], y)).lastrowid
                for seq in range(1, STUDENTS_PER_CLASS + 1):
                    name, acct = names.person()
                    sid = f"{y}{i:02d}{seq:03d}"
                    conn.execute("INSERT INTO student(id, name, gender, class_id) VALUES (?,?,?,?)",
                                 (sid, name, rng.choice(["男", "女"]), cls))
                    conn.execute("INSERT INTO app_user(username, password_hash, role, student_id) VALUES (?,?,?,?)",
                                 (acct, pwd, "student", sid))
        # ---- 管理类账号
        for u, role in (("admin", "admin"), ("research", "research"), ("academic", "academic")):
            conn.execute("INSERT INTO app_user(username, password_hash, role) VALUES (?,?,?)", (u, pwd, role))
        # ---- 教师
        teachers_by_college = {c: [] for c in COLLEGES}
        n = 0
        for college, depts in COLLEGES.items():
            for d in depts:
                for _ in range(TEACHERS_PER_DEPT[d]):
                    n += 1
                    name, acct = names.person()
                    tid = f"T{n:03d}"
                    conn.execute("INSERT INTO teacher(id, name, title, dept_id) VALUES (?,?,?,?)",
                                 (tid, name, rng.choice(["助教", "讲师", "讲师", "副教授", "教授"]), ids[d]))
                    conn.execute("INSERT INTO app_user(username, password_hash, role, teacher_id) VALUES (?,?,?,?)",
                                 (acct, pwd, "teacher", tid))
                    teachers_by_college[college].append(tid)
        # ---- 课程、先导课程、可上课程
        per_college_idx = {c: 0 for c in COLLEGES}
        for cid, cname, _h, _c, college, _pre in COURSES:
            conn.execute("INSERT INTO course(id, name) VALUES (?,?)", (cid, cname))
            lst = teachers_by_college[college]
            for k in range(3):
                conn.execute("INSERT INTO teacher_course(teacher_id, course_id) VALUES (?,?)",
                             (lst[(per_college_idx[college] + k) % len(lst)], cid))
            per_college_idx[college] += 1
        for cid, *_rest, pre in COURSES:
            for p in pre:
                conn.execute("INSERT INTO course_prereq(course_id, prereq_id) VALUES (?,?)", (cid, p))
        info = {c[0]: c for c in COURSES}
        for m, sems in PLAN.items():
            for sem, items in sems.items():
                for cid, nature in items:
                    _, _, hours, credits, college, _ = info[cid]
                    conn.execute(
                        "INSERT INTO curriculum_plan(major_id, semester_no, course_id, hours, credits, college_id, nature) "
                        "VALUES (?,?,?,?,?,?,?)", (major_id[m], sem, cid, hours, credits, ids[college], nature))
        # ---- 教室：2 个教学区 × 2 栋楼 × 3 层 × 6 间
        for area, buildings in (("东区", ["东一楼", "东二楼"]), ("西区", ["西一楼", "西二楼"])):
            for b in buildings:
                for floor in (1, 2, 3):
                    for k in range(1, 7):
                        cap = (120 if k <= 2 else 90 if k <= 4 else 60) if floor == 1 else (60 if k <= 3 else 40)
                        conn.execute("INSERT INTO classroom(area, building, room_no, floor, is_multimedia, capacity) "
                                     "VALUES (?,?,?,?,?,?)", (area, b, f"{floor}0{k}", floor, 0 if k % 3 == 0 else 1, cap))
        # ---- 学期
        for y, nn in HISTORY + [TARGET]:
            start = f"{y}-09-01" if nn == 1 else f"{y + 1}-02-22"
            sd = datetime.strptime(start, "%Y-%m-%d")
            if (y, nn) == TARGET:
                s_from, s_to = (now - timedelta(days=1)).strftime("%Y-%m-%d 08:00"), (now + timedelta(days=30)).strftime("%Y-%m-%d 18:00")
            else:
                s_from, s_to = (sd - timedelta(days=30)).strftime("%Y-%m-%d 08:00"), (sd - timedelta(days=5)).strftime("%Y-%m-%d 18:00")
            conn.execute("INSERT INTO term(id, year_start, term_no, weeks, start_date, select_start, select_end) "
                         "VALUES (?,?,?,?,?,?,?)", (term_id(y, nn), y, nn, 16, start, s_from, s_to))

    for y, nn in HISTORY:
        _seed_history_term(conn, rng, y, nn)
        if (y, nn) == HISTORY[-1]:                      # 当前在上的学期也排好课表
            timetable.auto_schedule_term(conn, term_id(y, nn))

    # ---- 目标学期：走完教务科的完整流程（选课学生尚未选课）
    t = term_id(*TARGET)
    progress.generate_tasks(conn, t)
    for (course_id,) in conn.execute("SELECT course_id FROM teaching_task WHERE term_id = ? AND nature = '任选'", (t,)).fetchall():
        with tx(conn):
            mining.adopt(conn, t, course_id)
    sections.split_term(conn, t)
    sections.auto_assign_teachers(conn, t)
    timetable.auto_schedule_term(conn, t)


def _seed_history_term(conn, rng, y, nn):
    t = term_id(y, nn)
    progress.generate_tasks(conn, t)
    pool = [r[0] for r in conn.execute(
        "SELECT s.id FROM student s JOIN admin_class c ON c.id = s.class_id "
        "WHERE (? - c.enroll_year) * 2 + ? BETWEEN 1 AND 8 ORDER BY s.id", (y, nn))]
    samples = {}
    with tx(conn):
        for cid, p in ELECTIVES.items():
            count = round(len(pool) * p * rng.uniform(0.92, 1.08))
            conn.execute("INSERT IGNORE INTO teaching_task(term_id, course_id, hours, nature, required_count) "
                         "VALUES (?,?,32,'任选',0)", (t, cid))
            conn.execute("UPDATE teaching_task SET expected_count = ? WHERE term_id = ? AND course_id = ?", (count, t, cid))
            samples[cid] = rng.sample(pool, count)
    sections.split_term(conn, t)
    sections.auto_assign_teachers(conn, t)
    with tx(conn):
        for task in conn.execute("SELECT id, course_id, nature FROM teaching_task WHERE term_id = ?", (t,)).fetchall():
            secs = [r[0] for r in conn.execute("SELECT id FROM section WHERE task_id = ? ORDER BY section_no", (task["id"],))]
            if not secs:
                continue
            if task["nature"] == "必修":
                studs = [r[0] for r in conn.execute(
                    "SELECT s.id FROM student s JOIN teaching_task_class tc ON tc.class_id = s.class_id "
                    "WHERE tc.task_id = ? ORDER BY s.id", (task["id"],))]
            else:
                studs = samples[task["course_id"]]
            for i, sid in enumerate(studs):
                conn.execute("INSERT INTO enrollment(student_id, task_id, section_id) VALUES (?,?,?)",
                             (sid, task["id"], secs[i % len(secs)]))
