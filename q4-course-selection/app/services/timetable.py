"""编制课程表：冲突检测（教室/教师/班级/容量）+ 自动排课（尽量保持教室不变，必须变化时就近调整）。"""
import math
import re

from ..db import tx
from ..util import WEEKDAYS


class ConflictError(Exception):
    def __init__(self, messages):
        super().__init__("；".join(messages))
        self.messages = messages


# ---------------------------------------------------------------- 时间块
def split_blocks(ppw):
    """每周节数拆成连排块：1/2/3 节为单块，更多则拆成 2 节一块（余 3 时并成 3 节块）。"""
    if ppw <= 3:
        return [ppw]
    blocks, rem = [], ppw
    while rem > 0:
        if rem == 3:
            blocks.append(3)
            break
        blocks.append(2)
        rem -= 2
    return blocks


def block_starts(size):
    """自动排课候选起始节：上午(1-4)、下午(5-8)内连排，不跨午休，不排晚上；上午优先。"""
    out = []
    for lo, hi in ((1, 4), (5, 8)):
        out += [p for p in range(lo, hi - size + 2)]
    out.sort(key=lambda p: (0 if p in (1, 3, 5) else 1, p))
    return out


def room_distance(a, b):
    """教室「就近」距离：教学区不同 >> 教学楼不同 > 楼层差 > 房间号差。"""
    def num(r):
        m = re.search(r"\d+", r["room_no"])
        return int(m.group()) if m else 0
    return ((1000 if a["area"] != b["area"] else 0) + (100 if a["building"] != b["building"] else 0)
            + abs(a["floor"] - b["floor"]) * 10 + min(abs(num(a) - num(b)), 9))


def _overlap(x, wd, start, count, ws, we):
    return (x["weekday"] == wd and x["start"] < start + count and start < x["start"] + x["count"]
            and x["ws"] <= we and ws <= x["we"])


# ---------------------------------------------------------------- 占用情况
class Occupancy:
    """一个学期内所有排课的内存索引；排课与冲突检测共用，避免重复 SQL。"""

    def __init__(self, conn, term_id):
        self.term_id = term_id
        self.term = conn.execute("SELECT * FROM term WHERE id = ?", (term_id,)).fetchone()
        self.rooms = {r["id"]: r for r in conn.execute("SELECT * FROM classroom")}
        self.teacher_names = {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM teacher")}
        self.sec = {}
        for r in conn.execute(
                """SELECT s.id, s.task_id, s.capacity, s.section_no, c.name AS course_name, t.hours
                     FROM section s JOIN teaching_task t ON t.id = s.task_id JOIN course c ON c.id = t.course_id
                    WHERE t.term_id = ?""", (term_id,)):
            self.sec[r["id"]] = dict(r)
        self.sec_teachers = {}
        for r in conn.execute(
                """SELECT st.section_id, st.teacher_id FROM section_teacher st
                     JOIN section s ON s.id = st.section_id JOIN teaching_task t ON t.id = s.task_id
                    WHERE t.term_id = ?""", (term_id,)):
            self.sec_teachers.setdefault(r["section_id"], set()).add(r["teacher_id"])
        self.task_classes = {}
        for r in conn.execute(
                """SELECT tc.task_id, tc.class_id FROM teaching_task_class tc
                     JOIN teaching_task t ON t.id = tc.task_id WHERE t.term_id = ?""", (term_id,)):
            self.task_classes.setdefault(r["task_id"], set()).add(r["class_id"])
        self.by_day = {d: [] for d in range(1, 8)}
        for r in conn.execute(
                """SELECT x.id, x.section_id, x.weekday, x.start_period, x.period_count, x.week_start,
                          x.week_end, x.classroom_id FROM schedule_slot x
                     JOIN section s ON s.id = x.section_id JOIN teaching_task t ON t.id = s.task_id
                    WHERE t.term_id = ?""", (term_id,)):
            self._index(r["id"], r["section_id"], r["weekday"], r["start_period"], r["period_count"],
                        r["week_start"], r["week_end"], r["classroom_id"])

    def _index(self, slot_id, sid, wd, start, count, ws, we, room):
        self.by_day[wd].append(dict(id=slot_id, sid=sid, weekday=wd, start=start, count=count, ws=ws, we=we, room=room))

    def describe(self, x):
        o = self.sec[x["sid"]]
        r = self.rooms[x["room"]]
        return (f"{o['course_name']}第{o['section_no']}班 {WEEKDAYS[x['weekday']]}"
                f"第{x['start']}-{x['start'] + x['count'] - 1}节 {r['building']}{r['room_no']}")

    # ---- 单项检查
    def room_free(self, room_id, wd, start, count, ws, we, exclude=None):
        return not any(x["room"] == room_id and x["id"] != exclude and _overlap(x, wd, start, count, ws, we)
                       for x in self.by_day[wd])

    def people_conflicts(self, sid, wd, start, count, ws, we, exclude=None):
        sec = self.sec[sid]
        mine_t = self.sec_teachers.get(sid, set())
        mine_c = self.task_classes.get(sec["task_id"], set())
        msgs = []
        for x in self.by_day[wd]:
            if x["id"] == exclude or not _overlap(x, wd, start, count, ws, we):
                continue
            other = self.sec[x["sid"]]
            shared = mine_t & self.sec_teachers.get(x["sid"], set())
            if shared:
                names = "、".join(self.teacher_names[t] for t in sorted(shared))
                msgs.append(f"教师冲突：{names} 同时在上 {self.describe(x)}")
            if other["task_id"] != sec["task_id"] and mine_c & self.task_classes.get(other["task_id"], set()):
                msgs.append(f"班级冲突：与 {self.describe(x)} 涉及相同行政班")
        return msgs

    def conflicts(self, sid, wd, start, count, ws, we, room_id, exclude=None):
        sec, room = self.sec[sid], self.rooms[room_id]
        msgs = []
        if we > self.term["weeks"]:
            msgs.append(f"周次超出本学期教学周数（{self.term['weeks']} 周）")
        if room["capacity"] < sec["capacity"]:
            msgs.append(f"教室容量不足：{room['building']}{room['room_no']} 容量 {room['capacity']} < 教学班人数上限 {sec['capacity']}")
        for x in self.by_day[wd]:
            if x["id"] != exclude and x["room"] == room_id and _overlap(x, wd, start, count, ws, we):
                msgs.append(f"教室冲突：{room['building']}{room['room_no']} 该时段已被 {self.describe(x)} 占用")
            if x["id"] != exclude and x["sid"] == sid and _overlap(x, wd, start, count, ws, we):
                msgs.append(f"同一课头时间重叠：{self.describe(x)}")
        msgs += self.people_conflicts(sid, wd, start, count, ws, we, exclude)
        return msgs

    def sibling_times(self, sid, size):
        """同一教学任务其他课头已排的时间块：面向同一批班级的课头优先并行，节省班级时间。"""
        task = self.sec[sid]["task_id"]
        return sorted({(x["weekday"], x["start"]) for lst in self.by_day.values() for x in lst
                       if x["sid"] != sid and x["count"] == size and self.sec[x["sid"]]["task_id"] == task})

    def pick_room(self, sid, wd, start, count, ws, we, anchor_id=None):
        """无锚点：容量最合适（最小满足）的教室；有锚点：距离锚点教室最近的空闲教室。"""
        need = self.sec[sid]["capacity"]
        cands = [r for r in self.rooms.values()
                 if r["capacity"] >= need and self.room_free(r["id"], wd, start, count, ws, we)]
        if not cands:
            return None
        if anchor_id:
            a = self.rooms[anchor_id]
            cands.sort(key=lambda r: (room_distance(a, r), 0 if r["is_multimedia"] else 1, r["capacity"], r["id"]))
        else:
            cands.sort(key=lambda r: (0 if r["is_multimedia"] else 1, r["capacity"], r["id"]))
        return cands[0]["id"]


# ---------------------------------------------------------------- 手工排课
def add_slot(conn, occ, sid, wd, start, count, ws, we, room_id):
    msgs = occ.conflicts(sid, wd, start, count, ws, we, room_id)
    if msgs:
        raise ConflictError(msgs)
    cur = conn.execute(
        """INSERT INTO schedule_slot(section_id, weekday, start_period, period_count, week_start, week_end, classroom_id)
           VALUES (?,?,?,?,?,?,?)""", (sid, wd, start, count, ws, we, room_id))
    occ._index(cur.lastrowid, sid, wd, start, count, ws, we, room_id)
    return cur.lastrowid


def delete_slot(conn, slot_id):
    conn.execute("DELETE FROM schedule_slot WHERE id = ?", (slot_id,))


def clear_section(conn, sid):
    conn.execute("DELETE FROM schedule_slot WHERE section_id = ?", (sid,))


# ---------------------------------------------------------------- 自动排课
def _schedule_block(occ, sid, size, anchor, used_days, shift):
    weeks = occ.term["weeks"]
    days = [((d - 1 + shift) % 5) + 1 for d in range(1, 6)]        # 周一至周五，按课头错开起点以均衡分布
    sibling = occ.sibling_times(sid, size)

    def candidates(allow_same_day):
        for wd, start in sibling:
            if allow_same_day or wd not in used_days:
                yield wd, start
        for wd in days:
            if allow_same_day or wd not in used_days:
                for start in block_starts(size):
                    yield wd, start

    for allow_same_day in (False, True):
        fallback = None
        for wd, start in candidates(allow_same_day):
            if occ.people_conflicts(sid, wd, start, size, 1, weeks):
                continue
            if anchor is None:                                     # 第一块：选容量最合适的空闲教室
                room = occ.pick_room(sid, wd, start, size, 1, weeks)
                if room:
                    return wd, start, room
                continue
            if occ.room_free(anchor, wd, start, size, 1, weeks):  # 优先保持教室不变
                return wd, start, anchor
            if fallback is None:
                fallback = (wd, start)
        if fallback:                                               # 否则按就近原则换教室
            wd, start = fallback
            room = occ.pick_room(sid, wd, start, size, 1, weeks, anchor)
            if room:
                return wd, start, room
    return None


def auto_schedule_section(conn, occ, sid):
    sec = occ.sec[sid]
    weeks = occ.term["weeks"]
    blocks = split_blocks(max(1, math.ceil(sec["hours"] / weeks)))
    anchor, used, added = None, set(), []
    for i, size in enumerate(blocks):
        found = _schedule_block(occ, sid, size, anchor, used, sid + i * 2)
        if not found:
            for slot_id in added:                                      # 本课头排不下：整体回退
                conn.execute("DELETE FROM schedule_slot WHERE id = ?", (slot_id,))
                for lst in occ.by_day.values():
                    lst[:] = [x for x in lst if x["id"] != slot_id]
            return False
        wd, start, room = found
        added.append(add_slot(conn, occ, sid, wd, start, size, 1, weeks, room))
        used.add(wd)
        anchor = anchor or room
    return True


def auto_schedule_term(conn, term_id):
    """为本学期「已分配教师、尚无课表」的课头自动排课；返回 (成功数, [未排课原因])。"""
    occ = Occupancy(conn, term_id)
    scheduled = {x["sid"] for lst in occ.by_day.values() for x in lst}
    todo = sorted((s for s in occ.sec if s not in scheduled), key=lambda s: (-occ.sec[s]["capacity"], s))
    ok, skipped = 0, []
    with tx(conn):
        for sid in todo:
            o = occ.sec[sid]
            name = f"{o['course_name']}第{o['section_no']}班"
            if not occ.sec_teachers.get(sid):
                skipped.append(f"{name}：尚未分配教师")
                continue
            if auto_schedule_section(conn, occ, sid):
                ok += 1
            else:
                skipped.append(f"{name}：找不到满足教师/班级/教室约束的时间，请手工调整")
    return ok, skipped


def clear_term(conn, term_id):
    conn.execute("DELETE FROM schedule_slot WHERE section_id IN (SELECT s.id FROM section s "
                 "JOIN teaching_task t ON t.id = s.task_id WHERE t.term_id = ?)", (term_id,))


# ---------------------------------------------------------------- 全学期冲突检查
def find_conflicts(conn, term_id):
    occ = Occupancy(conn, term_id)
    out = []
    for wd, lst in occ.by_day.items():
        for i, x in enumerate(lst):
            sec = occ.sec[x["sid"]]
            if occ.rooms[x["room"]]["capacity"] < sec["capacity"]:
                out.append(("教室容量", f"{occ.describe(x)} 教室容量小于教学班人数上限"))
            for y in lst[i + 1:]:
                if not _overlap(x, y["weekday"], y["start"], y["count"], y["ws"], y["we"]):
                    continue
                if x["room"] == y["room"]:
                    out.append(("教室", f"{occ.describe(x)}  ✕  {occ.describe(y)}"))
                shared = occ.sec_teachers.get(x["sid"], set()) & occ.sec_teachers.get(y["sid"], set())
                if shared:
                    out.append(("教师", f"{'、'.join(occ.teacher_names[t] for t in shared)}：{occ.describe(x)}  ✕  {occ.describe(y)}"))
                tx_, ty = occ.sec[x["sid"]]["task_id"], occ.sec[y["sid"]]["task_id"]
                if tx_ != ty and occ.task_classes.get(tx_, set()) & occ.task_classes.get(ty, set()):
                    out.append(("班级", f"{occ.describe(x)}  ✕  {occ.describe(y)}"))
    return out
