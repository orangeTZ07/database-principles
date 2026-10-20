"""登录、角色权限矩阵、CSRF、改密码、用户管理。"""
from .support import DbCase


class AuthTests(DbCase):
    def test_login_and_logout(self):
        c = self.client()
        r = self.post(c, "/login", username="admin", password="wrong")
        self.assertEqual(r.status_code, 200)
        self.assertIn("账号或密码错误", self.text(r))
        r = self.post(c, "/login", username="admin", password="123")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(c.get("/").status_code, 200)
        self.post(c, "/logout")
        self.assertEqual(c.get("/").status_code, 302)          # 退出后回到登录页

    def test_anonymous_redirected_to_login(self):
        r = self.app.test_client().get("/stats/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

    def test_permission_matrix(self):
        users = {"admin": "admin", "research": "research", "academic": "academic",
                 "teacher": self.first_user("teacher"), "student": self.first_user("student")}
        clients = {role: self.client(name) for role, name in users.items()}
        # 路径 → 允许访问的角色（允许时 200，入口是重定向的页面为 302；其余 403）
        matrix = {
            "/plan/": ({"admin", "research"}, 302),
            "/basic/": ({"admin"}, 302),
            "/progress/": ({"admin", "academic"}, 200),
            "/tasks/": ({"admin", "academic"}, 200),
            "/timetable/": ({"admin", "academic"}, 200),
            "/stats/": ({"admin", "academic"}, 200),
            "/mining/": ({"admin", "academic"}, 200),
            "/enroll/": ({"admin", "student"}, 200),
            "/query/": ({"admin", "research", "academic", "teacher", "student"}, 200),
            "/users/": ({"admin"}, 200),
        }
        for path, (allowed, ok_status) in matrix.items():
            for role, c in clients.items():
                want = ok_status if role in allowed else 403
                self.assertEqual(c.get(path).status_code, want, f"{role} 访问 {path}")

    def test_role_permission_table_drives_menu(self):
        funcs = {r[0] for r in self.q("SELECT func FROM role_permission WHERE role = 'student'")}
        self.assertEqual(funcs, {"enroll", "query"})
        html = self.text(self.client(self.first_user("student")).get("/"))
        self.assertIn("选课", html)
        self.assertNotIn("用户管理", html)

    def test_csrf_required_on_post(self):
        c = self.client("admin")
        r = c.post("/users/add", data={"username": "nocsrf", "role": "research"})
        self.assertEqual(r.status_code, 400)
        self.assertIsNone(self.one("SELECT 1 FROM app_user WHERE username = 'nocsrf'"))

    def test_change_password_requires_old_password(self):
        user = "research"
        c = self.client(user)
        r = self.post(c, "/password", follow=True, old="bad", new="newpass1", new2="newpass1")
        self.assertIn("原密码不正确", self.text(r))
        r = self.post(c, "/password", follow=True, old="123", new="abc", new2="abc")
        self.assertIn("至少 6 位", self.text(r))
        r = self.post(c, "/password", follow=True, old="123", new="newpass1", new2="newpass2")
        self.assertIn("不一致", self.text(r))
        r = self.post(c, "/password", follow=True, old="123", new="newpass1", new2="newpass1")
        self.assertIn("密码已修改", self.text(r))
        # 旧密码失效，新密码可用
        self.assertEqual(self.post(self.app.test_client(), "/login", username=user, password="123").status_code, 200)
        self.assertEqual(self.post(self.app.test_client(), "/login", username=user, password="newpass1").status_code, 302)
        # 管理员复位后恢复 123
        admin = self.client("admin")
        self.post(admin, f"/users/{user}/reset")
        self.assertEqual(self.post(self.app.test_client(), "/login", username=user, password="123").status_code, 302)


class UserManagementTests(DbCase):
    def add(self, c, username, role="research", pid=""):
        return self.post(c, "/users/add", follow=True, username=username, role=role, person_id=pid)

    def test_add_user_rules(self):
        c = self.client("admin")
        self.assertIn("初始密码 123", self.text(self.add(c, "Newbie")))
        self.assertEqual(self.post(self.app.test_client(), "/login", username="Newbie", password="123").status_code, 302)
        # 账号不区分大小写，不得重复
        self.assertIn("账号已存在", self.text(self.add(c, "newbie")))
        self.assertIn("账号已存在", self.text(self.add(c, "ADMIN")))
        # 账号必须全英文
        for bad in ("张三", "abc1", "a b", "user_x", ""):
            self.assertIn("英文字母", self.text(self.add(c, bad)), bad)
            self.assertIsNone(self.one("SELECT 1 FROM app_user WHERE username = ?", (bad,)))
        # 学生 / 教师账号必须关联学号 / 工号
        self.assertIn("必须关联", self.text(self.add(c, "nostu", "student", "")))
        self.assertIn("已绑定账号，或不存在", self.text(self.add(c, "dupstu", "student", "202301001")))   # 种子数据中该学生已有账号
        self.assertIn("已绑定账号，或不存在", self.text(self.add(c, "ghost", "student", "999999999")))

    def test_database_itself_rejects_non_english_username(self):
        code, _ = self.db_error("INSERT INTO app_user(username, password_hash, role) VALUES ('张三', 'x', 'research')")
        self.assertEqual(code, 3819)         # CHECK 约束
        code, _ = self.db_error("INSERT INTO app_user(username, password_hash, role) VALUES ('ADMIN', 'x', 'research')")
        self.assertEqual(code, 1062)         # utf8mb4_0900_ai_ci 主键：不区分大小写

    def test_disable_enable_reset(self):
        c = self.client("admin")
        self.add(c, "Temp")
        self.post(c, "/users/Temp/toggle")
        r = self.post(self.app.test_client(), "/login", username="temp", password="123")
        self.assertIn("已被停用", self.text(r))
        self.post(c, "/users/Temp/toggle")                       # 重新启用
        self.assertEqual(self.post(self.app.test_client(), "/login", username="Temp", password="123").status_code, 302)
        live = self.client("Temp")
        self.assertEqual(live.get("/query/").status_code, 200)
        self.post(c, "/users/Temp/toggle")                       # 已登录的用户被停用后，下一次请求即失效
        self.assertEqual(live.get("/query/").status_code, 302)

    def test_admin_cannot_be_disabled(self):
        c = self.client("admin")
        r = self.post(c, "/users/admin/toggle", follow=True)
        self.assertIn("不能停用", self.text(r))
        self.assertEqual(self.one("SELECT is_active FROM app_user WHERE username = 'admin'"), 1)

    def test_only_admin_can_manage_users(self):
        for who in ("research", "academic", self.first_user("teacher"), self.first_user("student")):
            c = self.client(who)
            self.assertEqual(self.post(c, "/users/add", username="hack", role="research").status_code, 403)
            self.assertEqual(self.post(c, "/users/admin/reset").status_code, 403)
        self.assertIsNone(self.one("SELECT 1 FROM app_user WHERE username = 'hack'"))

    def test_reset_password_to_123(self):
        c = self.client("admin")
        stu = self.first_user("student")
        sc = self.client(stu)
        self.post(sc, "/password", old="123", new="secret99", new2="secret99")
        self.assertEqual(self.post(self.app.test_client(), "/login", username=stu, password="123").status_code, 200)
        self.post(c, f"/users/{stu}/reset")
        self.assertEqual(self.post(self.app.test_client(), "/login", username=stu, password="123").status_code, 302)
