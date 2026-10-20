"""教学进程计划：根据培养方案 + 行政班级(专业、年级) 自动生成学期教学任务。
生成逻辑在数据库的存储过程 sp_generate_tasks 中完成（INSERT … SELECT … ON DUPLICATE KEY UPDATE），
Python 只负责调用。"""


def generate_tasks(conn, term_id):
    """幂等：已存在的任务更新学时/人数，不存在的新建；不会删除已有任务。
    返回 (新建数, 更新数)；学期不存在时数据库抛出错误。"""
    conn.execute("CALL sp_generate_tasks(?, @created, @updated)", (term_id,))
    row = conn.execute("SELECT @created AS created, @updated AS updated").fetchone()
    return int(row["created"]), int(row["updated"])
