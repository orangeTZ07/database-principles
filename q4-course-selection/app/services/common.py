def semester_no(year_start, term_no, enroll_year):
    """某班级（入学年份 enroll_year）在学期 (year_start, term_no) 处于培养方案的第几学期。"""
    return (year_start - enroll_year) * 2 + term_no


def get_setting(conn, key, default=None):
    row = conn.execute("SELECT setting_value FROM setting WHERE setting_key = ?", (key,)).fetchone()
    return row[0] if row else default


def set_setting(conn, key, value):
    conn.execute("INSERT INTO setting(setting_key, setting_value) VALUES (?, ?) "
                 "ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)", (key, str(value)))
