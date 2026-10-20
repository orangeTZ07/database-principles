"""基础信息维护（系统管理员）：学院/系、专业、行政班级、学生、教师（含可上课程）、教室。"""
from flask import Blueprint, flash, redirect, render_template, request, url_for, abort

from ..auth import require
from ..db import get_db, tx
from .crud import Entity, Field, register

bp = Blueprint("basic", __name__, url_prefix="/basic")

COLLEGES = "SELECT id, name FROM org_unit WHERE kind = '学院' ORDER BY id"

ENTITIES = [
    Entity("org", "学院/系", "org_unit", "id",
           [Field("name", "名称"), Field("kind", "类别", options=[("学院", "学院"), ("系", "系")]),
            Field("parent_id", "所属学院", options=COLLEGES, required=False, help="「系」必须选择所属学院；学院留空")],
           "SELECT o.id AS _pk, o.name, o.kind, p.name AS parent FROM org_unit o LEFT JOIN org_unit p ON p.id = o.parent_id",
           [("name", "名称"), ("kind", "类别"), ("parent", "所属学院")], "basic", search=("name",), order="_pk"),
    Entity("major", "专业", "major", "id",
           [Field("name", "专业名称"), Field("college_id", "所属学院", options=COLLEGES)],
           "SELECT m.id AS _pk, m.name, o.name AS college FROM major m JOIN org_unit o ON o.id = m.college_id",
           [("name", "专业"), ("college", "所属学院")], "basic", search=("name",), order="_pk"),
    Entity("class", "行政班级", "admin_class", "id",
           [Field("name", "班级名称"), Field("major_id", "所属专业", options="SELECT id, name FROM major ORDER BY id"),
            Field("enroll_year", "入学年份", "int")],
           "SELECT c.id AS _pk, c.name, c.major_name, c.college_name, c.enroll_year, "
           "(SELECT COUNT(*) FROM student s WHERE s.class_id = c.id) AS n, c.major_id FROM v_class c",
           [("name", "班级"), ("major_name", "专业"), ("college_name", "学院"), ("enroll_year", "入学年份"), ("n", "人数")],
           "basic", search=("name", "major_name"), order="enroll_year DESC, name",
           filters=[("major_id", "专业", "SELECT id, name FROM major ORDER BY id")]),
    Entity("student", "学生", "student", "id",
           [Field("id", "学号", readonly_on_edit=True), Field("name", "姓名"),
            Field("gender", "性别", options=[("男", "男"), ("女", "女")], required=False),
            Field("class_id", "所在行政班", options="SELECT id, name FROM admin_class ORDER BY enroll_year DESC, name")],
           "SELECT id AS _pk, id, name, gender, class_name, major_name, enroll_year, class_id FROM v_student",
           [("id", "学号"), ("name", "姓名"), ("gender", "性别"), ("class_name", "行政班"), ("major_name", "专业"),
            ("enroll_year", "入学年份")],
           "basic", search=("id", "name"), order="id",
           filters=[("class_id", "行政班", "SELECT id, name FROM admin_class ORDER BY enroll_year DESC, name")],
           intro="专业、入学年份由所在行政班推导（避免数据冗余）。"),
    Entity("teacher", "教师", "teacher", "id",
           [Field("id", "工号", readonly_on_edit=True), Field("name", "姓名"),
            Field("title", "职称", options=[(t, t) for t in ("助教", "讲师", "副教授", "教授")]),
            Field("dept_id", "所在系", options="SELECT id, name FROM org_unit WHERE kind = '系' ORDER BY id")],
           "SELECT t.id AS _pk, t.id, t.name, t.title, o.name AS dept, "
           "(SELECT GROUP_CONCAT(c.name ORDER BY c.id SEPARATOR '、') FROM teacher_course tc JOIN course c ON c.id = tc.course_id "
           "WHERE tc.teacher_id = t.id) AS courses FROM teacher t JOIN org_unit o ON o.id = t.dept_id",
           [("id", "工号"), ("name", "姓名"), ("title", "职称"), ("dept", "所在系"), ("courses", "可上课程")],
           "basic", search=("id", "name"), order="id", row_links=[("可上课程", "basic.teacher_courses", "tid")]),
    Entity("classroom", "教室", "classroom", "id",
           [Field("area", "教学区"), Field("building", "教学楼"), Field("room_no", "房间号"),
            Field("floor", "楼层", "int"), Field("is_multimedia", "多媒体", "bool"),
            Field("capacity", "容量", "int")],
           "SELECT id AS _pk, area, building, room_no, floor, CASE is_multimedia WHEN 1 THEN '是' ELSE '否' END AS mm, "
           "capacity FROM classroom",
           [("area", "教学区"), ("building", "教学楼"), ("room_no", "房间号"), ("floor", "楼层"),
            ("mm", "多媒体"), ("capacity", "容量")],
           "basic", search=("building", "room_no", "area"), order="area, building, room_no"),
]


def tabs(active):
    return [(e.title, url_for(f"basic.{e.key}_list"), e.key == active) for e in ENTITIES]


for _e in ENTITIES:
    register(bp, _e, tabs)


@bp.route("/")
@require("basic")
def index():
    return redirect(url_for("basic.org_list"))


@bp.route("/teacher/<tid>/courses", methods=["GET", "POST"])
@require("basic")
def teacher_courses(tid):
    conn = get_db()
    t = conn.execute("SELECT * FROM teacher WHERE id = ?", (tid,)).fetchone()
    if t is None:
        abort(404)
    if request.method == "POST":
        chosen = request.form.getlist("item")
        with tx(conn):
            conn.execute("DELETE FROM teacher_course WHERE teacher_id = ?", (tid,))
            for c in chosen:
                conn.execute("INSERT INTO teacher_course(teacher_id, course_id) VALUES (?,?)", (tid, c))
        flash("可上课程已更新", "ok")
        return redirect(url_for("basic.teacher_list"))
    have = {r[0] for r in conn.execute("SELECT course_id FROM teacher_course WHERE teacher_id = ?", (tid,))}
    items = [(r["id"], f"{r['id']} {r['name']}", r["id"] in have) for r in conn.execute("SELECT * FROM course ORDER BY id")]
    return render_template("checklist.html", title=f"{t['name']}（{t['id']}）可上课程", items=items,
                           back=url_for("basic.teacher_list"), hint="勾选该教师具备任课资格的课程；分配课头教师时只能从这些教师中选择。")
