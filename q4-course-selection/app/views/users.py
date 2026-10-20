"""用户管理（系统管理员）：添加账号、停用/启用、复位密码（初始密码 123）。"""
import re

from flask import Blueprint, flash, redirect, render_template, request, url_for
from werkzeug.security import generate_password_hash

from ..auth import require
from ..db import DatabaseError, get_db
from ..util import ROLE_NAMES, friendly_db_error

bp = Blueprint("users", __name__, url_prefix="/users")
INITIAL_PASSWORD = "123"
CREATABLE = ("research", "academic", "teacher", "student")


@bp.route("/")
@require("users")
def index():
    conn = get_db()
    q = (request.args.get("q") or "").strip()
    role = request.args.get("role", "")
    where, params = [], []
    if q:
        where.append("(u.username LIKE ? OR s.name LIKE ? OR t.name LIKE ?)")
        params += [f"%{q}%"] * 3
    if role:
        where.append("u.role = ?")
        params.append(role)
    rows = conn.execute(
        """SELECT u.username, u.role, u.is_active, u.created_at, COALESCE(s.name, t.name) AS person,
                  COALESCE(u.student_id, u.teacher_id) AS person_id
             FROM app_user u LEFT JOIN student s ON s.id = u.student_id LEFT JOIN teacher t ON t.id = u.teacher_id"""
        + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY u.role, u.username LIMIT 300", params).fetchall()
    return render_template("users.html", rows=rows, q=q, role=role, creatable=CREATABLE)


@bp.route("/add", methods=["POST"])
@require("users")
def add():
    conn = get_db()
    f = request.form
    username, role, pid = (f.get("username") or "").strip(), f.get("role"), (f.get("person_id") or "").strip()
    if not re.fullmatch(r"[A-Za-z]{1,32}", username):
        flash("账号必须全部由英文字母组成（1-32 位）", "error")
    elif role not in CREATABLE:
        flash("角色不正确", "error")
    elif role in ("teacher", "student") and not pid:
        flash("教师/学生账号必须关联工号/学号", "error")
    else:
        try:
            conn.execute(
                "INSERT INTO app_user(username, password_hash, role, student_id, teacher_id) VALUES (?,?,?,?,?)",
                (username, generate_password_hash(INITIAL_PASSWORD), role,
                 pid if role == "student" else None, pid if role == "teacher" else None))
            flash(f"已添加 {ROLE_NAMES[role]} 账号 {username}，初始密码 {INITIAL_PASSWORD}", "ok")
        except DatabaseError as e:
            code, m = e.args[0], str(e.args[1])
            flash("账号已存在（账号不区分大小写，不能重复）" if code == 1062 and "PRIMARY" in m
                  else "该学号/工号已绑定账号，或不存在" if code in (1062, 1452)
                  else friendly_db_error(e), "error")
    return redirect(url_for("users.index"))


@bp.route("/<username>/toggle", methods=["POST"])
@require("users")
def toggle(username):
    if username.lower() == "admin":
        flash("系统管理员账号 admin 不能停用", "error")
    else:
        get_db().execute("UPDATE app_user SET is_active = 1 - is_active WHERE username = ?", (username,))
        flash(f"已切换 {username} 的启用状态", "ok")
    return redirect(request.referrer or url_for("users.index"))


@bp.route("/<username>/reset", methods=["POST"])
@require("users")
def reset(username):
    get_db().execute("UPDATE app_user SET password_hash = ? WHERE username = ?",
                     (generate_password_hash(INITIAL_PASSWORD), username))
    flash(f"已将 {username} 的密码复位为 {INITIAL_PASSWORD}", "ok")
    return redirect(request.referrer or url_for("users.index"))
