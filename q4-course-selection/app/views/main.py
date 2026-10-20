from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

from ..auth import require
from ..db import get_db
from ..services import selection
from ..util import now_str

bp = Blueprint("main", __name__)


@bp.route("/")
@require(None)
def home():
    conn = get_db()
    info = {}
    if g.user["role"] == "student":
        info["student"] = conn.execute("SELECT * FROM v_student WHERE id = ?", (g.user["student_id"],)).fetchone()
        info["open_terms"] = selection.open_terms(conn, now_str())
    elif g.user["role"] == "teacher":
        info["teacher"] = conn.execute(
            "SELECT t.*, o.name AS dept FROM teacher t JOIN org_unit o ON o.id = t.dept_id WHERE t.id = ?",
            (g.user["teacher_id"],)).fetchone()
    return render_template("home.html", **info)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        u = (request.form.get("username") or "").strip()
        row = get_db().execute("SELECT * FROM app_user WHERE username = ?", (u,)).fetchone()
        if row is None or not check_password_hash(row["password_hash"], request.form.get("password") or ""):
            flash("账号或密码错误", "error")
        elif not row["is_active"]:
            flash("该账号已被停用，请联系系统管理员", "error")
        else:
            session.clear()
            session["user"] = row["username"]
            nxt = request.args.get("next") or url_for("main.home")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("main.home"))
    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("main.login"))


@bp.route("/password", methods=["GET", "POST"])
@require(None)
def password():
    if request.method == "POST":
        old, new, new2 = (request.form.get(k) or "" for k in ("old", "new", "new2"))
        if not check_password_hash(g.user["password_hash"], old):
            flash("原密码不正确（修改密码前必须提供原始密码）", "error")
        elif len(new) < 6:
            flash("新密码至少 6 位", "error")
        elif new != new2:
            flash("两次输入的新密码不一致", "error")
        elif new == old:
            flash("新密码不能与原密码相同", "error")
        else:
            get_db().execute("UPDATE app_user SET password_hash = ? WHERE username = ?",
                             (generate_password_hash(new), g.user["username"]))
            flash("密码已修改", "ok")
            return redirect(url_for("main.home"))
    return render_template("password.html")
