-- =====================================================================
-- 选课管理系统 · 02 视图
-- =====================================================================
SET NAMES utf8mb4;
DROP VIEW IF EXISTS v_class_term_stat, v_teacher_term_stat, v_course_term_enroll, v_class_term_plan,
  v_slot, v_section, v_student, v_class;

-- 班级（带专业、学院）
CREATE VIEW v_class AS
SELECT c.id, c.name, c.enroll_year, c.major_id, m.name AS major_name, m.college_id, o.name AS college_name
FROM admin_class c
JOIN major m    ON m.id = c.major_id
JOIN org_unit o ON o.id = m.college_id;

-- 学生（专业 / 入学年份 / 学院均由班级推导，保持 3NF）
CREATE VIEW v_student AS
SELECT s.id, s.name, s.gender, s.class_id, c.name AS class_name, c.enroll_year,
       c.major_id, c.major_name, c.college_name
FROM student s JOIN v_class c ON c.id = s.class_id;

-- 课头（带课程、学期、教师名单）
CREATE VIEW v_section AS
SELECT s.id AS section_id, s.section_no, s.capacity, s.enrolled_count,
       t.id AS task_id, t.term_id, t.course_id, c.name AS course_name, t.hours, t.nature,
       (SELECT GROUP_CONCAT(CONCAT(te.name, IF(st.is_lead = 1, '(主讲)', ''))
                            ORDER BY st.is_lead DESC, te.id SEPARATOR '、')
          FROM section_teacher st JOIN teacher te ON te.id = st.teacher_id
         WHERE st.section_id = s.id) AS teachers
FROM section s
JOIN teaching_task t ON t.id = s.task_id
JOIN course c        ON c.id = t.course_id;

-- 课表条目（课头 × 时间 × 教室）
CREATE VIEW v_slot AS
SELECT x.id AS slot_id, x.section_id, x.weekday, x.start_period, x.period_count,
       x.week_start, x.week_end, x.classroom_id,
       r.area, r.building, r.room_no, r.capacity AS room_capacity,
       v.term_id, v.task_id, v.course_id, v.course_name, v.section_no, v.teachers, v.capacity
FROM schedule_slot x
JOIN classroom r ON r.id = x.classroom_id
JOIN v_section v ON v.section_id = x.section_id;

-- 教学进程：某学期每个行政班按培养方案应修的课程
-- （培养方案学期 = (学年起始年 - 入学年份) × 2 + 学期序号，只取 1~8 学期在读班级）
CREATE VIEW v_class_term_plan AS
SELECT t.id AS term_id, ac.id AS class_id, ac.name AS class_name, cp.semester_no,
       cp.course_id, cp.hours, cp.credits, cp.nature,
       (SELECT COUNT(*) FROM student s WHERE s.class_id = ac.id) AS student_cnt
FROM term t
CROSS JOIN admin_class ac
JOIN curriculum_plan cp ON cp.major_id = ac.major_id
                       AND cp.semester_no = (t.year_start - ac.enroll_year) * 2 + t.term_no;

-- 每门课每学期的选课人数（数据挖掘的历史数据）
CREATE VIEW v_course_term_enroll AS
SELECT t.course_id, tm.id AS term_id, tm.year_start, tm.term_no, COUNT(e.student_id) AS enrolled
FROM teaching_task t
JOIN term tm ON tm.id = t.term_id
LEFT JOIN enrollment e ON e.task_id = t.id
GROUP BY t.id, t.course_id, tm.id, tm.year_start, tm.term_no;

-- 选课总况：班级维度（选课门次、学时）
CREATE VIEW v_class_term_stat AS
SELECT tm.id AS term_id, c.id AS class_id, c.name AS class_name, c.major_id, c.major_name,
       COUNT(DISTINCT s.id) AS students,
       COUNT(e.student_id)  AS selections,
       COALESCE(SUM(tk.hours), 0) AS hours
FROM term tm
JOIN v_class c ON (tm.year_start - c.enroll_year) * 2 + tm.term_no BETWEEN 1 AND 8
LEFT JOIN student s ON s.class_id = c.id
LEFT JOIN (enrollment e JOIN teaching_task tk ON tk.id = e.task_id) ON e.student_id = s.id AND tk.term_id = tm.id
GROUP BY tm.id, c.id, c.name, c.major_id, c.major_name;

-- 选课总况：教师教学任务（课头数、主讲课头数、学时）
CREATE VIEW v_teacher_term_stat AS
SELECT t.term_id, te.id AS teacher_id, te.name, te.title,
       COUNT(DISTINCT s.id) AS sections,
       COALESCE(SUM(st.is_lead), 0) AS lead_sections,
       COALESCE(SUM(t.hours), 0) AS hours
FROM teacher te
JOIN section_teacher st ON st.teacher_id = te.id
JOIN section s ON s.id = st.section_id
JOIN teaching_task t ON t.id = s.task_id
GROUP BY t.term_id, te.id, te.name, te.title;
