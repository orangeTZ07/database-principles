"""测试公共部分：每个测试类各建一个独立的 MySQL 测试库（course_selection_test_*），用完删除。

连接参数取自环境变量 MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD；该账号需要有创建 / 删除
`course_selection_test_%` 库的权限（用 root 或参见 README 的授权语句）。MySQL 连不上时整组测试跳过。
"""
import os
import re
import unittest
from datetime import datetime

import pymysql

import run
from app import create_app, db

NOW = datetime(2026, 10, 20, 12, 0)          # 固定「当前时间」：2026-2027-2 学期的选课窗口为 10-19 08:00 ~ 11-19 18:00
NOW_STR = NOW.strftime("%Y-%m-%d %H:%M")
TERM = "2026-2027-2"                         # 开放选课的学期
CUR_TERM = "2026-2027-1"                     # 正在上课的学期（已排课表、已有选课记录）


class DbCase(unittest.TestCase):
    cfg = None
    conn = None
    app = None

    @classmethod
    def setUpClass(cls):
        name = f"course_selection_test_{os.getpid()}_{cls.__name__.lower()}"[:60]
        cfg = dict(db.default_config(), database=name)
        try:
            run.init_database(cfg, now=NOW)
        except pymysql.err.OperationalError as e:
            raise unittest.SkipTest(f"无法连接 / 初始化 MySQL：{e}")
        cls.cfg = cfg
        cls.conn = db.connect(cfg)
        cls.app = create_app({"DB": cfg, "NOW": lambda: NOW, "TESTING": True})

    @classmethod
    def tearDownClass(cls):
        if cls.conn is not None:
            cls.conn.close()
        if cls.cfg:
            admin = db.connect(cls.cfg, use_database=False)
            admin.execute(f"DROP DATABASE IF EXISTS `{cls.cfg['database']}`")
            admin.close()

    # ------------------------------------------------------------ 查询小工具
    def q(self, sql, params=()):
        return self.conn.execute(sql, params).fetchall()

    def one(self, sql, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return None if row is None else row[0]

    def username_of(self, person_id):
        return self.one("SELECT username FROM app_user WHERE student_id = ? OR teacher_id = ?", (person_id, person_id))

    def first_user(self, role):
        return self.one("SELECT username FROM app_user WHERE role = ? ORDER BY username LIMIT 1", (role,))

    def db_error(self, sql, params=()):
        """执行一条应当失败的 SQL，返回 MySQL 错误号（成功则让测试失败）。"""
        try:
            self.conn.execute(sql, params)
        except db.DatabaseError as e:
            return e.args[0], str(e.args[1])
        self.fail("该语句本应被数据库拒绝：" + sql)

    # ------------------------------------------------------------ Web 小工具
    @classmethod
    def client(cls, user=None, password="123"):
        c = cls.app.test_client()
        if user:
            r = cls.post(c, "/login", username=user, password=password)
            assert r.status_code == 302, f"{user} 登录失败"
        return c

    @staticmethod
    def token(c):
        with c.session_transaction() as s:
            tok = s.get("_csrf")
        if not tok:
            c.get("/login")
            with c.session_transaction() as s:
                tok = s["_csrf"]
        return tok

    @classmethod
    def post(cls, c, url, follow=False, **data):
        data["_csrf"] = cls.token(c)
        return c.post(url, data=data, follow_redirects=follow)

    @staticmethod
    def text(r):
        return re.sub(r"\s+", " ", r.get_data(as_text=True))
