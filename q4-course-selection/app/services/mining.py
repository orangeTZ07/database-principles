"""数据挖掘：基于任选课历年选课人数的趋势预测当年选课人数，并按预测人数推荐教室（容量决定）。"""
import math

from .common import get_setting
from .sections import max_room_capacity


def history(conn, course_id, term_id):
    """同一学期序号（上学期/下学期）历年的选课人数，按学年升序（来自视图 v_course_term_enroll）。"""
    y, _, n = term_id.split("-")
    return [dict(r) for r in conn.execute(
        """SELECT term_id, year_start, enrolled FROM v_course_term_enroll
            WHERE course_id = ? AND term_no = ? AND year_start < ? ORDER BY year_start""", (course_id, int(n), int(y)))]


def linear_forecast(points, x_target):
    """最小二乘一元线性回归。返回 (预测值, 方法说明, 斜率, R²)；无历史数据返回 None。"""
    n = len(points)
    if n == 0:
        return None
    xs, ys = [p[0] for p in points], [p[1] for p in points]
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if n == 1 or sxx == 0:
        return max(0, math.floor(my + 0.5)), "仅 1 年历史数据：沿用上年人数", 0.0, None
    slope = sum((x - mx) * (y - my) for x, y in points) / sxx
    pred = max(0, math.floor(my + slope * (x_target - mx) + 0.5))
    sst = sum((y - my) ** 2 for y in ys)
    sse = sum((y - (my + slope * (x - mx))) ** 2 for x, y in points)
    r2 = 1.0 if sst == 0 else round(1 - sse / sst, 3)
    return pred, (f"线性回归（{n} 年数据）" if n >= 3 else "两点线性外推"), round(slope, 2), r2


def recommend(conn, predicted):
    """按预测人数指导教室安排：先按班额限额 / 最大教室容量算出课头数，再为「每个课头的人数」
    推荐容量 ≥ 该人数的最小教室（多媒体优先）。返回 (教室, 课头数, 每班限额, 每课头人数)。"""
    limit = min(int(get_setting(conn, "section_limit", 60)), max_room_capacity(conn) or 0)
    n_sections = math.ceil(predicted / limit) if limit and predicted else 0
    per_section = math.ceil(predicted / n_sections) if n_sections else 0
    room = conn.execute(
        """SELECT * FROM classroom WHERE capacity >= ?
            ORDER BY capacity, is_multimedia DESC, id LIMIT 1""", (per_section,)).fetchone() if per_section else None
    return room, n_sections, limit, per_section


def sql_forecast(conn, course_id, term_id):
    """用 MySQL 存储函数 fn_forecast_enroll（纯 SQL 聚合实现的线性回归）算出预测值，用来和 Python 结果交叉校验。"""
    return conn.execute("SELECT fn_forecast_enroll(?, ?)", (course_id, term_id)).fetchone()[0]


def forecast(conn, course_id, term_id):
    hist = history(conn, course_id, term_id)
    target_year = int(term_id.split("-")[0])
    res = linear_forecast([(h["year_start"], h["enrolled"]) for h in hist], target_year)
    out = {"course_id": course_id, "term_id": term_id, "history": hist, "predicted": None,
           "method": "无历史数据", "slope": None, "r2": None, "room": None, "n_sections": 0, "limit": None,
           "per_section": 0}
    if res:
        out["predicted"], out["method"], out["slope"], out["r2"] = res
        out["room"], out["n_sections"], out["limit"], out["per_section"] = recommend(conn, out["predicted"])
    out["sql_value"] = sql_forecast(conn, course_id, term_id)
    return out


def adopt(conn, term_id, course_id):
    """采纳预测：写入 course_forecast，并把预计人数同步到该学期的教学任务（用于拆分课头）。"""
    f = forecast(conn, course_id, term_id)
    if f["predicted"] is None:
        raise ValueError("没有历史数据，无法预测")
    task = conn.execute("SELECT id FROM teaching_task WHERE term_id = ? AND course_id = ?",
                        (term_id, course_id)).fetchone()
    if task is None:
        raise ValueError("该学期还没有此课程的教学任务（请先生成教学任务）")
    conn.execute(
        """INSERT INTO course_forecast(term_id, course_id, predicted_count, method, sample_size, recommended_classroom_id)
           VALUES (?,?,?,?,?,?)
           ON DUPLICATE KEY UPDATE predicted_count = VALUES(predicted_count), method = VALUES(method),
             sample_size = VALUES(sample_size), recommended_classroom_id = VALUES(recommended_classroom_id),
             created_at = NOW()""",
        (term_id, course_id, f["predicted"], f["method"], len(f["history"]), f["room"]["id"] if f["room"] else None))
    conn.execute("UPDATE teaching_task SET expected_count = ? WHERE id = ?", (f["predicted"], task["id"]))
    return f
