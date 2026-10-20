"""登录、角色权限（角色→功能映射存放在 role_permission 表）与 CSRF 防护。"""
import secrets
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for
from markupsafe import Markup

from .db import get_db
from .util import ROLE_NAMES, now_str

# 功能键 → (菜单名, 入口端点)
FUNCS = [
    ("plan", "培养方案", "plan.index"),
    ("basic", "基础信息", "basic.index"),
    ("progress", "教学进程", "academic.progress_home"),
    ("tasks", "教学任务", "academic.tasks"),
    ("timetable", "课程表", "academic.timetable_home"),
    ("enroll", "选课", "student.enroll_home"),
    ("query", "数据查询", "query.index"),
    ("stats", "选课统计", "stats.index"),
    ("mining", "数据挖掘", "stats.mining_home"),
    ("users", "用户管理", "users.index"),
]


def init_app(app):
    @app.before_request
    def load_user():
        g.user, g.perms = None, set()
        name = session.get("user")
        if name:
            row = get_db().execute("SELECT * FROM app_user WHERE username = ?", (name,)).fetchone()
            if row and row["is_active"]:
                g.user = row
                g.perms = {r[0] for r in get_db().execute(
                    "SELECT func FROM role_permission WHERE role = ?", (row["role"],))}
            else:
                session.pop("user", None)
        if request.method == "POST":
            token = session.get("_csrf")
            if not token or token != request.form.get("_csrf"):
                abort(400, "CSRF 校验失败，请刷新页面后重试")

    @app.context_processor
    def inject():
        if "_csrf" not in session:
            session["_csrf"] = secrets.token_hex(16)
        nav = [(label, endpoint) for key, label, endpoint in FUNCS if key in g.perms]
        return dict(user=g.user, nav=nav, role_names=ROLE_NAMES, csrf=Markup('<input type="hidden" name="_csrf" value="%s">' % session["_csrf"]),
                    now=now_str(), can=lambda k: k in g.perms)

    @app.errorhandler(403)
    def forbidden(e):
        from flask import render_template
        return render_template("error.html", code=403, msg="当前角色无权使用该功能"), 403

    @app.errorhandler(400)
    def bad(e):
        from flask import render_template
        return render_template("error.html", code=400, msg=getattr(e, "description", "请求有误")), 400

    @app.errorhandler(404)
    def missing(e):
        from flask import render_template
        return render_template("error.html", code=404, msg="页面或记录不存在"), 404


def require(func):
    """要求登录且角色拥有指定功能权限。func=None 表示登录即可。"""
    def deco(view):
        @wraps(view)
        def wrapper(*a, **kw):
            if g.user is None:
                flash("请先登录", "warn")
                return redirect(url_for("main.login", next=request.path))
            if func is not None and func not in g.perms:
                abort(403)
            return view(*a, **kw)
        return wrapper
    return deco
