"""通用「列表 + 新增 + 编辑 + 删除」页面生成器，用于基础信息、课程、培养方案等维护页。"""

from flask import flash, redirect, render_template, request, url_for

from ..auth import require
from ..db import DatabaseError, get_db
from ..util import friendly_db_error


class Field:
    def __init__(self, name, label, kind="text", options=None, required=True, help="", readonly_on_edit=False):
        self.name, self.label, self.kind, self.options = name, label, kind, options
        self.required, self.help, self.readonly_on_edit = required, help, readonly_on_edit

    def choices(self, conn):
        o = self.options
        if o is None:
            return None
        if isinstance(o, str):
            return [(r[0], r[1]) for r in conn.execute(o)]
        return list(o)


class Entity:
    def __init__(self, key, title, table, pk, fields, list_sql, list_cols, perm, search=(), order="1",
                 filters=(), row_links=(), intro="", auto_pk=True):
        self.key, self.title, self.table, self.pk, self.fields = key, title, table, pk, fields
        self.list_sql, self.list_cols, self.perm, self.search, self.order = list_sql, list_cols, perm, search, order
        self.filters, self.row_links, self.intro, self.auto_pk = filters, row_links, intro, auto_pk


def _parse(ent, form, editing):
    vals, errors = {}, []
    for f in ent.fields:
        if editing and f.readonly_on_edit:
            continue
        raw = (form.get(f.name) or "").strip()
        if f.kind == "bool":
            vals[f.name] = 1 if form.get(f.name) else 0
            continue
        if raw == "":
            if f.required:
                errors.append(f"「{f.label}」不能为空")
            vals[f.name] = None
            continue
        try:
            vals[f.name] = int(raw) if f.kind == "int" else float(raw) if f.kind == "float" else raw
        except ValueError:
            errors.append(f"「{f.label}」格式不正确")
    return vals, errors


def register(bp, ent, tabs):
    def page_ctx(**extra):
        return dict(ent=ent, tabs=tabs(ent.key), **extra)

    @require(ent.perm)
    def list_view():
        conn = get_db()
        where, params = [], []
        q = (request.args.get("q") or "").strip()
        if q and ent.search:
            where.append("(" + " OR ".join(f"CAST({c} AS TEXT) LIKE ?" for c in ent.search) + ")")
            params += [f"%{q}%"] * len(ent.search)
        active = {}
        for col, _label, _opts in ent.filters:
            v = request.args.get(col, "")
            active[col] = v
            if v != "":
                where.append(f"{col} = ?")
                params.append(v)
        sql = f"SELECT * FROM ({ent.list_sql}) AS t" + (" WHERE " + " AND ".join(where) if where else "") \
              + f" ORDER BY {ent.order} LIMIT 300"
        rows = conn.execute(sql, params).fetchall()
        flt = []
        for col, label, opts in ent.filters:
            ch = [(r[0], r[1]) for r in conn.execute(opts)] if isinstance(opts, str) else list(opts)
            flt.append((col, label, ch))
        fields = [(f, f.choices(conn)) for f in ent.fields]
        return render_template("crud_list.html", rows=rows, q=q, filters=flt, active=active, fields=fields,
                               **page_ctx())

    @require(ent.perm)
    def add_view():
        conn = get_db()
        vals, errors = _parse(ent, request.form, False)
        if errors:
            for e in errors:
                flash(e, "error")
            return redirect(url_for(f"{bp.name}.{ent.key}_list"))
        cols = ", ".join(vals)
        try:
            conn.execute(f"INSERT INTO {ent.table}({cols}) VALUES ({', '.join('?' * len(vals))})", list(vals.values()))
            flash(f"已新增{ent.title}", "ok")
        except DatabaseError as e:
            flash(friendly_db_error(e), "error")
        return redirect(url_for(f"{bp.name}.{ent.key}_list"))

    @require(ent.perm)
    def edit_view(pk):
        conn = get_db()
        row = conn.execute(f"SELECT * FROM {ent.table} WHERE {ent.pk} = ?", (pk,)).fetchone()
        if row is None:
            from flask import abort
            abort(404)
        if request.method == "POST":
            vals, errors = _parse(ent, request.form, True)
            if errors:
                for e in errors:
                    flash(e, "error")
            else:
                try:
                    sets = ", ".join(f"{c} = ?" for c in vals)
                    conn.execute(f"UPDATE {ent.table} SET {sets} WHERE {ent.pk} = ?", list(vals.values()) + [pk])
                    flash(f"{ent.title}已更新", "ok")
                    return redirect(url_for(f"{bp.name}.{ent.key}_list"))
                except DatabaseError as e:
                    flash(friendly_db_error(e), "error")
        fields = [(f, f.choices(conn)) for f in ent.fields]
        return render_template("crud_form.html", row=row, fields=fields, **page_ctx())

    @require(ent.perm)
    def delete_view(pk):
        try:
            get_db().execute(f"DELETE FROM {ent.table} WHERE {ent.pk} = ?", (pk,))
            flash(f"已删除{ent.title}", "ok")
        except DatabaseError as e:
            flash("无法删除：" + friendly_db_error(e), "error")
        return redirect(url_for(f"{bp.name}.{ent.key}_list"))

    k = ent.key
    base = f"/{k}"
    bp.add_url_rule(base, f"{k}_list", list_view)
    bp.add_url_rule(base + "/add", f"{k}_add", add_view, methods=["POST"])
    bp.add_url_rule(base + "/<pk>/edit", f"{k}_edit", edit_view, methods=["GET", "POST"])
    bp.add_url_rule(base + "/<pk>/delete", f"{k}_delete", delete_view, methods=["POST"])
