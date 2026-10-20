"""MySQL 连接、事务与 SQL 脚本加载。

业务代码统一使用 conn.execute(sql, params)：
  * 占位符写 ?（内部转换成 PyMySQL 的 %s）；
  * 查询结果是 Row，既可按列名 row["name"]，也可按位置 row[0]（兼容 sqlite3.Row 的用法），模板里可写 row.name；
  * DECIMAL 自动转 int / float，DATE / DATETIME 以字符串返回（'YYYY-MM-DD' / 'YYYY-MM-DD HH:MM'）。
"""
import os
from contextlib import contextmanager
from decimal import Decimal

import pymysql
from flask import current_app, g
from pymysql.constants import FIELD_TYPE
from pymysql.cursors import Cursor, DictCursorMixin

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQL_DIR = os.path.join(ROOT, "sql")
SQL_FILES = ["01_schema.sql", "02_views.sql", "03_triggers.sql", "04_routines.sql", "05_reference_data.sql"]

DatabaseError = pymysql.MySQLError          # 含触发器 SIGNAL、CHECK 违反等所有服务器端错误
IntegrityError = pymysql.IntegrityError     # 仅含唯一 / 外键 / 非空约束错误


def default_config():
    """数据库连接参数，可用环境变量覆盖。"""
    return dict(
        host=os.environ.get("MYSQL_HOST", "127.0.0.1"),
        port=int(os.environ.get("MYSQL_PORT", "3306")),
        user=os.environ.get("MYSQL_USER", "root"),
        password=os.environ.get("MYSQL_PASSWORD", ""),
        database=os.environ.get("MYSQL_DATABASE", "course_selection"),
    )


class Row:
    """查询结果行：支持 row["col"]、row[0]、row.col、dict(row)、遍历得到各列的值。"""
    __slots__ = ("_names", "_values")

    def __init__(self, pairs):
        pairs = list(pairs)
        self._names = tuple(p[0] for p in pairs)
        self._values = tuple(p[1] for p in pairs)

    def __getitem__(self, key):
        if isinstance(key, (int, slice)):
            return self._values[key]
        try:
            return self._values[self._names.index(key)]
        except ValueError:
            raise KeyError(key) from None

    def keys(self):
        return list(self._names)

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def __repr__(self):
        return "Row(%s)" % ", ".join(f"{k}={v!r}" for k, v in zip(self._names, self._values))


class RowCursor(DictCursorMixin, Cursor):
    dict_type = Row


def _decimal(s):
    d = Decimal(s)
    return int(d) if d == d.to_integral_value() else float(d)


def _conversions():
    conv = dict(pymysql.converters.conversions)
    conv[FIELD_TYPE.NEWDECIMAL] = conv[FIELD_TYPE.DECIMAL] = _decimal
    conv[FIELD_TYPE.DATE] = lambda s: s
    conv[FIELD_TYPE.DATETIME] = conv[FIELD_TYPE.TIMESTAMP] = lambda s: s[:16]
    return conv


class Conn:
    """PyMySQL 连接的薄封装（自动提交模式；需要事务时用 tx()）。"""

    def __init__(self, raw):
        self.raw = raw

    def execute(self, sql, params=()):
        cur = self.raw.cursor()
        cur.execute(sql.replace("%", "%%").replace("?", "%s"), tuple(params))
        return cur

    def executemany(self, sql, seq):
        cur = self.raw.cursor()
        cur.executemany(sql.replace("%", "%%").replace("?", "%s"), list(seq))
        return cur

    def close(self):
        self.raw.close()


def connect(cfg=None, use_database=True):
    cfg = dict(cfg or default_config())
    if not use_database:
        cfg.pop("database", None)
    raw = pymysql.connect(charset="utf8mb4", autocommit=True, cursorclass=RowCursor, conv=_conversions(),
                          connect_timeout=10, **cfg)
    return Conn(raw)


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DB"])
    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


@contextmanager
def tx(conn):
    """显式事务：InnoDB 行级锁 + 提交 / 回滚。"""
    conn.raw.begin()
    try:
        yield conn
        conn.raw.commit()
    except BaseException:
        conn.raw.rollback()
        raise


def call(conn, proc, *args):
    """调用带 (OUT code, OUT msg) 两个输出参数的存储过程，返回 (code, msg)。"""
    marks = ",".join(["?"] * len(args))
    conn.execute(f"CALL {proc}({marks}{',' if args else ''} @code, @msg)", args)
    row = conn.execute("SELECT @code AS code, @msg AS msg").fetchone()
    return row["code"], row["msg"]


# ---------------------------------------------------------------- 脚本加载（支持 mysql 客户端的 DELIMITER 语法）
def _strip_comment(line):
    quote = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "'\"`":
            quote = ch
        elif ch == "-" and line[i:i + 3] == "-- ":
            return line[:i]
        elif ch == "#":
            return line[:i]
    return line


def split_statements(text):
    delim, buf, out = ";", [], []
    for raw in text.splitlines():
        line = _strip_comment(raw).rstrip()
        if not line.strip():
            continue
        if line.strip().upper().startswith("DELIMITER "):
            delim = line.strip().split(None, 1)[1]
            continue
        buf.append(line)
        if line.endswith(delim):
            stmt = "\n".join(buf)
            out.append(stmt[: -len(delim)].rstrip())
            buf = []
    if buf:
        out.append("\n".join(buf))
    return out


def run_script(conn, path):
    with open(path, encoding="utf-8") as f:
        statements = split_statements(f.read())
    cur = conn.raw.cursor()
    for stmt in statements:
        cur.execute(stmt)
    return len(statements)


def create_schema(conn):
    """按顺序执行 sql/ 下的建表、视图、触发器、存储过程、参照数据脚本。"""
    for name in SQL_FILES:
        run_script(conn, os.path.join(SQL_DIR, name))


def recreate_database(cfg=None):
    """删除并重建数据库（慎用），返回已连接到新库的 Conn。"""
    cfg = dict(cfg or default_config())
    name = cfg["database"]
    admin = connect(cfg, use_database=False)
    admin.execute(f"DROP DATABASE IF EXISTS `{name}`")
    admin.execute(f"CREATE DATABASE `{name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci")
    admin.close()
    conn = connect(cfg)
    create_schema(conn)
    return conn


def database_ready(cfg=None):
    """库和 app_user 表是否已存在（用于首次运行时自动初始化）。"""
    cfg = dict(cfg or default_config())
    try:
        conn = connect(cfg)
    except pymysql.err.OperationalError as e:
        if e.args[0] == 1049:      # Unknown database
            return False
        raise
    try:
        return conn.execute("SHOW TABLES LIKE 'app_user'").fetchone() is not None
    finally:
        conn.close()


def init_app(app):
    app.teardown_appcontext(close_db)
