-- ============================================================
-- 选课管理系统：演示 / 报表查询（对应题目「查询统计」「数据挖掘」「选课」等需求）
-- 用法（需先建库并导入演示数据）：
--     mysql -u course -p course_selection < sql/06_report_queries.sql
-- 除第 10 节（选课后立即退选，恢复现场）外都是只读查询，可反复执行；tests/ 中会逐条执行以确保语句有效。
-- ============================================================
SET @term = '2026-2027-2';        -- 当前开放选课的学期（下学期，选课尚未开始）
SET @stat_term = '2026-2027-1';   -- 正在进行的学期（选课已结束，用于课表 / 统计演示）

-- ------------------------------------------------------------
-- 1. 培养方案查询：某专业各学期计划开课（学时 / 学分 / 开课学院 / 先导课程）
-- ------------------------------------------------------------
SELECT m.name AS 专业, p.semester_no AS 学期, p.course_id AS 课程号, c.name AS 课程名, p.nature AS 性质,
       p.hours AS 学时, p.credits AS 学分, o.name AS 开课学院,
       (SELECT GROUP_CONCAT(pr.prereq_id ORDER BY pr.prereq_id SEPARATOR '、')
          FROM course_prereq pr WHERE pr.course_id = p.course_id) AS 先导课程
FROM curriculum_plan p
JOIN major m ON m.id = p.major_id
JOIN course c ON c.id = p.course_id
JOIN org_unit o ON o.id = p.college_id
WHERE m.name = '计算机科学与技术'
ORDER BY p.semester_no, p.course_id;

-- 1b. 每个专业每学期的学分小计（GROUP BY + WITH ROLLUP 同时得到专业合计与总计）
SELECT m.name AS 专业, p.semester_no AS 学期, SUM(p.credits) AS 学分合计, COUNT(*) AS 门数
FROM curriculum_plan p JOIN major m ON m.id = p.major_id
GROUP BY m.name, p.semester_no WITH ROLLUP;

-- ------------------------------------------------------------
-- 2. 先导课程链：递归 CTE 展开某课程直接 + 间接的全部先修课（如 操作系统 CS301）
-- ------------------------------------------------------------
WITH RECURSIVE chain AS (
  SELECT prereq_id, 1 AS depth FROM course_prereq WHERE course_id = 'CS301'
  UNION ALL
  SELECT p.prereq_id, chain.depth + 1
    FROM chain JOIN course_prereq p ON p.course_id = chain.prereq_id
)
SELECT c.id AS 课程号, c.name AS 课程名, MIN(chain.depth) AS 先修层级
FROM chain JOIN course c ON c.id = chain.prereq_id
GROUP BY c.id, c.name
ORDER BY 先修层级, c.id;

-- ------------------------------------------------------------
-- 3. 教学进程：各班在指定学期应修课程（教学任务由 sp_generate_tasks 据此汇总生成）
-- ------------------------------------------------------------
SELECT class_name AS 班级, semester_no AS 学期序号, course_id AS 课程号, nature AS 性质, hours AS 学时, student_cnt AS 人数
FROM v_class_term_plan
WHERE term_id = @term
ORDER BY class_id, course_id;

-- ------------------------------------------------------------
-- 4. 教学任务与课头：课头数、容量、已选人数、授课教师（课头拆分、教师分配结果）
-- ------------------------------------------------------------
SELECT course_id AS 课程号, course_name AS 课程名, nature AS 性质, section_no AS 课头, capacity AS 容量,
       enrolled_count AS 已选, teachers AS 教师
FROM v_section
WHERE term_id = @term
ORDER BY course_id, section_no;

-- 4b. 必修课按行政班拆分后的课头人数分布：每课程的课头数与最大 / 最小班额
SELECT course_id AS 课程号, COUNT(*) AS 课头数, MAX(capacity) AS 最大班额, MIN(capacity) AS 最小班额
FROM v_section
WHERE term_id = @term
GROUP BY course_id
HAVING COUNT(*) > 1
ORDER BY 课头数 DESC, course_id;

-- ------------------------------------------------------------
-- 5. 课程表：某学期某学生的周课表（打印课表）
-- ------------------------------------------------------------
SELECT st.id AS 学号, st.name AS 姓名, sl.weekday AS 星期, sl.start_period AS 起始节, sl.period_count AS 节数,
       sl.course_name AS 课程, CONCAT(sl.area, sl.building, sl.room_no) AS 教室, sl.teachers AS 教师,
       CONCAT(sl.week_start, '-', sl.week_end, '周') AS 周次
FROM v_student st
JOIN enrollment e ON e.student_id = st.id
JOIN v_slot sl ON sl.section_id = e.section_id
WHERE sl.term_id = @stat_term AND st.id = (SELECT MIN(student_id) FROM enrollment WHERE section_id IN (SELECT section_id FROM v_slot WHERE term_id = @stat_term))
ORDER BY sl.weekday, sl.start_period;

-- 5b. 某教室一周的使用情况
SELECT CONCAT(area, building, room_no) AS 教室, weekday AS 星期, start_period AS 起始节, period_count AS 节数,
       course_name AS 课程, section_no AS 课头
FROM v_slot
WHERE term_id = @stat_term AND classroom_id = (SELECT classroom_id FROM v_slot WHERE term_id = @stat_term ORDER BY slot_id LIMIT 1)
ORDER BY weekday, start_period;

-- ------------------------------------------------------------
-- 6. 冲突校验（应当返回 0 行）：同一教室同一时间被两个课头占用
--    （数据库中由触发器 trg_slot_room_conflict 保证；这里再用 SQL 验证一遍）
-- ------------------------------------------------------------
SELECT a.id AS 排课1, b.id AS 排课2, a.classroom_id AS 教室, a.weekday AS 星期
FROM schedule_slot a
JOIN schedule_slot b ON a.classroom_id = b.classroom_id AND a.weekday = b.weekday AND a.id < b.id
 AND a.start_period < b.start_period + b.period_count AND b.start_period < a.start_period + a.period_count
 AND a.week_start <= b.week_end AND b.week_start <= a.week_end
JOIN section sa ON sa.id = a.section_id
JOIN section sb ON sb.id = b.section_id
JOIN teaching_task ta ON ta.id = sa.task_id
JOIN teaching_task tb ON tb.id = sb.task_id AND tb.term_id = ta.term_id;

-- 6b. 教师时间冲突校验（应当返回 0 行）：同一教师同一学期同一时间在两个不同课头上课
SELECT x.teacher_id AS 教师, a.weekday AS 星期, a.start_period AS 节次, a.section_id AS 课头1, b.section_id AS 课头2
FROM schedule_slot a
JOIN schedule_slot b ON a.weekday = b.weekday AND a.id < b.id AND a.section_id <> b.section_id
 AND a.start_period < b.start_period + b.period_count AND b.start_period < a.start_period + a.period_count
 AND a.week_start <= b.week_end AND b.week_start <= a.week_end
JOIN section_teacher x ON x.section_id = a.section_id
JOIN section_teacher y ON y.section_id = b.section_id AND y.teacher_id = x.teacher_id
JOIN section sa ON sa.id = a.section_id JOIN teaching_task ta ON ta.id = sa.task_id
JOIN section sb ON sb.id = b.section_id JOIN teaching_task tb ON tb.id = sb.task_id AND tb.term_id = ta.term_id;

-- ------------------------------------------------------------
-- 7. 选课：名单、满员 / 空位
-- ------------------------------------------------------------
-- 7a. 空位最少的 10 个课头（enrolled_count 由触发器维护，capacity - enrolled_count 为剩余名额）
SELECT course_id AS 课程号, course_name AS 课程名, section_no AS 课头, capacity AS 容量, enrolled_count AS 已选,
       capacity - enrolled_count AS 空位,
       CASE WHEN enrolled_count >= capacity THEN '已满' ELSE '可选' END AS 状态
FROM v_section
WHERE term_id = @term AND nature = '任选'
ORDER BY 空位, course_id, section_no
LIMIT 10;

-- 7b. 某课头选课名单
SELECT e.section_id AS 课头号, st.id AS 学号, st.name AS 姓名, st.class_name AS 班级, e.selected_at AS 选课时间
FROM enrollment e JOIN v_student st ON st.id = e.student_id
WHERE e.section_id = (SELECT MIN(id) FROM section WHERE enrolled_count > 0)
ORDER BY st.id
LIMIT 20;

-- 7c. 数据一致性校验（应当返回 0 行）：课头的 enrolled_count 必须等于 enrollment 实际行数
SELECT s.id AS 课头号, s.enrolled_count AS 记录值, COUNT(e.student_id) AS 实际行数
FROM section s LEFT JOIN enrollment e ON e.section_id = s.id
GROUP BY s.id, s.enrolled_count
HAVING s.enrolled_count <> COUNT(e.student_id);

-- ------------------------------------------------------------
-- 8. 选课总况统计：班级维度，环比（上学期）/ 同比（去年同期，即往前数 2 个学期），窗口函数 LAG() 取上期值
--    注意：先在派生表里对全部学期开窗，再筛选目标学期，否则 LAG 会取到被 WHERE 过滤后的相邻行
-- ------------------------------------------------------------
SELECT class_name AS 班级, selections AS 本期选课门次,
       prev1 AS 上学期, ROUND((selections - prev1) * 100 / prev1, 1) AS 环比百分比,
       prev2 AS 去年同期, ROUND((selections - prev2) * 100 / prev2, 1) AS 同比百分比
FROM (SELECT term_id, class_id, class_name, selections,
             LAG(selections, 1) OVER w AS prev1, LAG(selections, 2) OVER w AS prev2
        FROM v_class_term_stat
      WINDOW w AS (PARTITION BY class_id ORDER BY term_id)) x
WHERE term_id = @stat_term
ORDER BY class_id;

-- 8b. 全校各学期选课门次合计及环比（窗口函数 + 派生表）
SELECT term_id AS 学期, total AS 选课门次, prev_total AS 上学期,
       ROUND((total - prev_total) * 100 / prev_total, 1) AS 环比百分比
FROM (SELECT term_id, SUM(selections) AS total, LAG(SUM(selections)) OVER (ORDER BY term_id) AS prev_total
        FROM v_class_term_stat GROUP BY term_id) t
ORDER BY term_id;

-- 8c. 教师教学任务统计：课头数 / 主讲数 / 学时，并用 RANK() 排名
SELECT name AS 教师, title AS 职称, sections AS 课头数, lead_sections AS 主讲课头, hours AS 学时,
       RANK() OVER (ORDER BY sections DESC) AS 工作量排名
FROM v_teacher_term_stat
WHERE term_id = @stat_term
ORDER BY 工作量排名, teacher_id;

-- 8d. 各专业任选课选课人数历年趋势（图表数据源）
SELECT course_id AS 课程号, term_id AS 学期, enrolled AS 选课人数
FROM v_course_term_enroll
WHERE term_no = 2
ORDER BY course_id, year_start;

-- ------------------------------------------------------------
-- 9. 数据挖掘：历年人数趋势 → 预测 → 推荐教室
-- ------------------------------------------------------------
-- 9a. 存储函数 fn_forecast_enroll：在数据库内用 SQL 聚合做一元线性回归，预测各任选课下一学期人数
SELECT c.id AS 课程号, c.name AS 课程名,
       (SELECT GROUP_CONCAT(v.enrolled ORDER BY v.year_start SEPARATOR ' → ')
          FROM v_course_term_enroll v WHERE v.course_id = c.id AND v.term_no = 2) AS 历年人数,
       fn_forecast_enroll(c.id, @term) AS 预测人数
FROM course c
WHERE c.id IN (SELECT course_id FROM curriculum_plan WHERE nature = '任选')
ORDER BY c.id;

-- 9b. 预测人数指导教室安排：先按每课头上限（setting.section_limit）算建议课头数，
--     再为「每个课头的人数」推荐容量 ≥ 该人数的最小教室（多媒体优先）
SELECT g.course_id AS 课程号, g.predicted AS 预测人数, g.n_sections AS 建议课头数, g.per_section AS 每课头人数,
       COALESCE((SELECT CONCAT(r.area, r.building, r.room_no, '（容量', r.capacity, '）')
                   FROM classroom r WHERE r.capacity >= g.per_section
                  ORDER BY r.capacity, r.is_multimedia DESC, r.id LIMIT 1), '无足够大的教室') AS 推荐教室
FROM (SELECT f.course_id, f.predicted, CEIL(f.predicted / lim.v) AS n_sections,
             CEIL(f.predicted / CEIL(f.predicted / lim.v)) AS per_section
        FROM (SELECT c.id AS course_id, fn_forecast_enroll(c.id, @term) AS predicted
                FROM course c WHERE c.id IN (SELECT course_id FROM curriculum_plan WHERE nature = '任选')) f
        CROSS JOIN (SELECT CAST(setting_value AS UNSIGNED) AS v FROM setting WHERE setting_key = 'section_limit') lim
       WHERE f.predicted > 0) g
ORDER BY g.course_id;

-- ------------------------------------------------------------
-- 10. 存储过程演示：sp_enroll 选课 → 查看结果 → sp_drop 退选恢复现场
--     sp_enroll 在过程内部 START TRANSACTION，并对课头行加 FOR UPDATE 行锁，
--     保证「有空位才成功」在并发下也成立（过程内自行 COMMIT，所以不能再包一层外部事务回滚）
-- ------------------------------------------------------------
SET @stu = (SELECT s.id FROM student s
             WHERE s.id NOT IN (SELECT e.student_id FROM enrollment e JOIN teaching_task t ON t.id = e.task_id WHERE t.term_id = @term)
             ORDER BY s.id LIMIT 1);
SET @sec = (SELECT s.id FROM section s JOIN teaching_task t ON t.id = s.task_id
             WHERE t.term_id = @term AND t.nature = '任选' AND s.enrolled_count < s.capacity ORDER BY s.id LIMIT 1);
SET @task = (SELECT task_id FROM section WHERE id = @sec);
SET @when = (SELECT DATE_ADD(select_start, INTERVAL 1 HOUR) FROM term WHERE id = @term);

CALL sp_enroll(@stu, @sec, @when, @code, @msg);
SELECT @stu AS 学号, @sec AS 课头, @code AS 结果码, @msg AS 提示,
       (SELECT enrolled_count FROM section WHERE id = @sec) AS 选课后人数;

-- 同一学生再选同一门课：返回 code=4「已选」
CALL sp_enroll(@stu, @sec, @when, @code, @msg);
SELECT @code AS 重复选课结果码, @msg AS 提示;

-- 退选（只能在选课时段内），恢复现场；选课流水 enrollment_log 会留下选课 / 退选两条记录
CALL sp_drop(@stu, @task, @when, @code, @msg);
SELECT @code AS 退选结果码, (SELECT enrolled_count FROM section WHERE id = @sec) AS 退选后人数;

-- 并发演示（两个 mysql 会话，各自先 START TRANSACTION）：
--   会话 A：CALL sp_enroll('学生甲', <只剩 1 个空位的课头>, NOW(), @c, @m);   -- 未提交，持有该课头行锁
--   会话 B：CALL sp_enroll('学生乙', <同一课头>,            NOW(), @c, @m);   -- 阻塞等待
--   会话 A：COMMIT;  → 会话 B 继续执行，看到课头已满，返回 code=6「已满」。
