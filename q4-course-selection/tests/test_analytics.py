"""查询统计与数据挖掘：同比 / 环比、选课人数预测（Python 与 MySQL 存储函数交叉校验）、报表 SQL 脚本、全站页面巡检。"""
import os
import re

from app import db
from app.services import mining
from app.util import last_year_term_id, pct, prev_term_id
from app.views import stats

from .support import CUR_TERM, DbCase, NOW_STR, TERM

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class PureFunctionTests(DbCase):
    def test_linear_forecast(self):
        self.assertEqual(mining.linear_forecast([(2023, 10), (2024, 20), (2025, 30)], 2026)[:1], (40,))
        pred, method, slope, r2 = mining.linear_forecast([(2023, 10), (2024, 20), (2025, 30)], 2026)
        self.assertEqual((slope, r2), (10.0, 1.0))
        self.assertIn("线性回归", method)
        self.assertEqual(mining.linear_forecast([(2025, 30)], 2026)[0], 30)          # 只有 1 年：沿用上年
        self.assertEqual(mining.linear_forecast([(2024, 20), (2025, 30)], 2026)[0], 40)
        self.assertEqual(mining.linear_forecast([(1, 10), (2, 5), (3, 0)], 5)[0], 0)   # 预测为负时取 0
        self.assertIsNone(mining.linear_forecast([], 2026))

    def test_term_arithmetic_and_pct(self):
        self.assertEqual(prev_term_id("2026-2027-2"), "2026-2027-1")
        self.assertEqual(prev_term_id("2026-2027-1"), "2025-2026-2")
        self.assertEqual(last_year_term_id("2026-2027-2"), "2025-2026-2")
        self.assertEqual(last_year_term_id("2026-2027-1"), "2025-2026-1")
        self.assertEqual(pct(110, 100), 10.0)
        self.assertEqual(pct(50, 200), -75.0)
        self.assertIsNone(pct(5, 0))
        self.assertIsNone(pct(5, None))


class MiningTests(DbCase):
    def electives(self):
        return [r[0] for r in self.q("SELECT DISTINCT course_id FROM curriculum_plan WHERE nature = '任选' ORDER BY course_id")]

    def test_python_and_mysql_function_agree(self):
        """应用层线性回归与数据库内存储函数 fn_forecast_enroll（纯 SQL 聚合）结果一致。"""
        checked = 0
        for term in ("2024-2025-2", "2025-2026-2", TERM, "2025-2026-1", CUR_TERM):
            for cid in self.electives():
                f = mining.forecast(self.conn, cid, term)
                self.assertEqual(f["predicted"], f["sql_value"], f"{cid} {term}")
                checked += f["predicted"] is not None
        self.assertGreater(checked, 20)

    def test_forecast_grows_with_history(self):
        f = mining.forecast(self.conn, "CS403", TERM)
        self.assertEqual([h["term_id"] for h in f["history"]], ["2023-2024-2", "2024-2025-2", "2025-2026-2"])
        enrolled = [h["enrolled"] for h in f["history"]]
        self.assertEqual(enrolled, sorted(enrolled))
        self.assertGreater(f["predicted"], enrolled[-1])
        self.assertGreater(f["slope"], 0)
        self.assertIsNotNone(f["room"])
        self.assertGreaterEqual(f["n_sections"], 1)

    def test_no_history_gives_none(self):
        f = mining.forecast(self.conn, "CS403", "2023-2024-1")
        self.assertIsNone(f["predicted"])
        self.assertIsNone(f["sql_value"])

    def test_mining_page_and_adopt(self):
        c = self.client("academic")
        html = self.text(c.get(f"/mining/?term={TERM}&course=CS403"))
        self.assertIn("fn_forecast_enroll", html)
        self.assertIn("<svg", html)
        expected = mining.forecast(self.conn, "CS404", TERM)["predicted"]
        r = self.post(c, "/mining/adopt", follow=True, term=TERM, course="CS404", split=1)
        self.assertIn(f"预计 {expected} 人", self.text(r))
        self.assertEqual(self.one("SELECT predicted_count FROM course_forecast WHERE term_id = ? AND course_id = 'CS404'", (TERM,)), expected)
        self.assertEqual(self.one("SELECT expected_count FROM teaching_task WHERE term_id = ? AND course_id = 'CS404'", (TERM,)), expected)
        # 再采纳一次不会重复插入（ON DUPLICATE KEY UPDATE）
        self.post(c, "/mining/adopt", term=TERM, course="CS404")
        self.assertEqual(self.one("SELECT COUNT(*) FROM course_forecast WHERE term_id = ? AND course_id = 'CS404'", (TERM,)), 1)

    def test_only_academic_office_can_use_mining(self):
        for who in ("research", self.first_user("teacher"), self.first_user("student")):
            self.assertEqual(self.client(who).get("/mining/").status_code, 403)


class StatisticsTests(DbCase):
    def view_value(self, view, key_col, key, term, col):
        return self.one(f"SELECT {col} FROM {view} WHERE term_id = ? AND {key_col} = ?", (term, key))

    def test_class_stats_qoq_and_yoy_match_view(self):
        rows = stats.class_stats(self.conn, CUR_TERM)
        self.assertGreater(len(rows), 8)
        prev, yoy = prev_term_id(CUR_TERM), last_year_term_id(CUR_TERM)
        saw_prev = saw_yoy = False
        for r in rows:
            p = self.view_value("v_class_term_stat", "class_id", r["class_id"], prev, "selections")
            y = self.view_value("v_class_term_stat", "class_id", r["class_id"], yoy, "selections")
            self.assertEqual(r["selections_prev"], p, r["class_name"])
            self.assertEqual(r["selections_yoy"], y, r["class_name"])
            self.assertEqual(r["selections_qoq"], pct(r["selections"], p))
            self.assertEqual(r["selections_yoy_pct"], pct(r["selections"], y))
            saw_prev |= p is not None
            saw_yoy |= y is not None
        self.assertTrue(saw_prev and saw_yoy)
        # 新入学的班级（2026 级第一个学期）没有环比 / 同比数据
        fresh = [r for r in rows if r["class_name"].endswith("2601班") or "26" in r["class_name"]]
        self.assertTrue(fresh and all(r["selections_prev"] is None for r in fresh))

    def test_teacher_stats_qoq_matches_view(self):
        rows = stats.teacher_stats(self.conn, CUR_TERM)
        self.assertGreater(len(rows), 5)
        prev = prev_term_id(CUR_TERM)
        for r in rows:
            p = self.view_value("v_teacher_term_stat", "teacher_id", r["teacher_id"], prev, "sections")
            self.assertEqual(r["sections_prev"], p, r["name"])
        self.assertEqual([r["sections"] for r in rows], sorted((r["sections"] for r in rows), reverse=True))

    def test_stats_page_has_chart_and_table(self):
        c = self.client("academic")
        html = self.text(c.get(f"/stats/?term={CUR_TERM}"))
        self.assertIn("<svg", html)
        self.assertIn("环比", html)
        self.assertIn("同比", html)
        table = self.text(c.get(f"/stats/?term={CUR_TERM}&view=table"))
        self.assertIn("<table", table)
        self.assertEqual(c.get(f"/stats/?term={CUR_TERM}&class_id=1").status_code, 200)
        self.assertEqual(c.get("/stats/?term=1999-2000-1").status_code, 404)

    def test_stats_totals_equal_enrollment_rows(self):
        total = self.one("SELECT SUM(selections) FROM v_class_term_stat WHERE term_id = ?", (CUR_TERM,))
        actual = self.one("SELECT COUNT(*) FROM enrollment e JOIN teaching_task t ON t.id = e.task_id WHERE t.term_id = ?", (CUR_TERM,))
        self.assertEqual(total, actual)


class ReportScriptTests(DbCase):
    def statements(self):
        with open(os.path.join(ROOT, "sql", "06_report_queries.sql"), encoding="utf-8") as f:
            return db.split_statements(f.read())

    def test_every_statement_runs(self):
        stmts = self.statements()
        self.assertGreater(len(stmts), 20)
        cur = self.conn.raw.cursor()
        for s in stmts:
            cur.execute(s)
            while cur.nextset():
                pass

    def test_consistency_queries_return_no_rows(self):
        cur = self.conn.raw.cursor()
        for s in self.statements():
            if "AS 排课1" in s or "AS 课头2" in s or "HAVING s.enrolled_count <>" in s:
                cur.execute(s)
                self.assertEqual(len(cur.fetchall()), 0, s[:80])

    def test_demo_enroll_restores_state(self):
        before = self.one("SELECT SUM(enrolled_count) FROM section")
        cur = self.conn.raw.cursor()
        for s in self.statements():
            cur.execute(s)
        self.assertEqual(self.one("SELECT SUM(enrolled_count) FROM section"), before)


class CrawlTests(DbCase):
    """各角色登录后顺着页面链接把能点到的 GET 页面全部访问一遍，不允许出现 4xx/5xx（重定向除外）。"""

    def crawl(self, user, limit=250):
        c = self.client(user)
        seen, todo, bad = set(), ["/"], []
        while todo and len(seen) < limit:
            url = todo.pop()
            if url in seen:
                continue
            seen.add(url)
            r = c.get(url)
            body = r.get_data(as_text=True) if r.mimetype == "text/html" else ""
            r.close()
            if r.status_code in (301, 302):
                todo.append(r.headers["Location"])
                continue
            if r.status_code != 200:
                bad.append((url, r.status_code))
                continue
            for h in re.findall(r'href="(/[^"#]*)"', body):
                h = h.replace("&amp;", "&")
                if h not in seen and "logout" not in h:
                    todo.append(h)
        return seen, bad

    def test_all_roles_crawl_clean(self):
        users = ["admin", "research", "academic", self.first_user("teacher"), self.first_user("student")]
        sizes = {}
        for u in users:
            seen, bad = self.crawl(u)
            self.assertEqual(bad, [], u)
            sizes[u] = len(seen)
        self.assertGreater(sizes["admin"], 100)
        self.assertGreater(sizes["academic"], 100)
