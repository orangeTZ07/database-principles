-- =====================================================================
-- 选课管理系统 · 04 存储过程 / 存储函数
--   sp_generate_tasks  教学进程计划 → 自动生成教学任务（INSERT … SELECT … ON DUPLICATE KEY UPDATE，幂等）
--   sp_enroll          学生选课：时段、必修资格、重复、时间冲突、满员，全部在一个事务内完成
--   sp_drop            学生退选
--   fn_forecast_enroll 数据挖掘：用 SQL 聚合函数做一元线性回归，预测任选课选课人数
-- =====================================================================
SET NAMES utf8mb4;

DROP PROCEDURE IF EXISTS sp_generate_tasks;
DROP PROCEDURE IF EXISTS sp_enroll;
DROP PROCEDURE IF EXISTS sp_drop;
DROP FUNCTION IF EXISTS fn_forecast_enroll;

DELIMITER $$

-- 根据「培养方案 + 行政班（专业、年级）」生成某学期的教学任务和涉及的班级
-- 必修人数 = 把该课程列为必修的各班级人数之和；只要有一个专业列为必修，该任务性质即为必修
CREATE PROCEDURE sp_generate_tasks(IN p_term VARCHAR(12), OUT p_created INT, OUT p_updated INT)
BEGIN
  DECLARE v_total INT DEFAULT 0;
  DECLARE v_existing INT DEFAULT 0;
  DECLARE v_found INT DEFAULT 0;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    ROLLBACK;
    RESIGNAL;
  END;

  SELECT COUNT(*) INTO v_found FROM term WHERE id = p_term;
  IF v_found = 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '学期不存在';
  END IF;

  SELECT COUNT(DISTINCT course_id) INTO v_total FROM v_class_term_plan WHERE term_id = p_term;
  SELECT COUNT(DISTINCT p.course_id) INTO v_existing
    FROM v_class_term_plan p
    JOIN teaching_task t ON t.term_id = p.term_id AND t.course_id = p.course_id
   WHERE p.term_id = p_term;

  START TRANSACTION;
  INSERT INTO teaching_task (term_id, course_id, hours, nature, required_count)
  SELECT term_id, course_id, MAX(hours),
         IF(SUM(nature = '必修') > 0, '必修', '任选'),
         COALESCE(SUM(IF(nature = '必修', student_cnt, 0)), 0)
    FROM v_class_term_plan
   WHERE term_id = p_term
   GROUP BY term_id, course_id
  ON DUPLICATE KEY UPDATE hours = VALUES(hours), nature = VALUES(nature), required_count = VALUES(required_count);

  DELETE ttc FROM teaching_task_class ttc JOIN teaching_task t ON t.id = ttc.task_id WHERE t.term_id = p_term;
  INSERT INTO teaching_task_class (task_id, class_id, student_count)
  SELECT t.id, p.class_id, p.student_cnt
    FROM v_class_term_plan p
    JOIN teaching_task t ON t.term_id = p.term_id AND t.course_id = p.course_id
   WHERE p.term_id = p_term AND p.nature = '必修';
  COMMIT;

  SET p_created = v_total - v_existing, p_updated = v_existing;
END$$

-- 选课：返回码 0 成功；1 教学班不存在；2 不在选课时段；3 非本班必修；4 已选该课程；5 时间冲突；6 人数已满；9 其他数据库错误
CREATE PROCEDURE sp_enroll(IN p_student VARCHAR(20), IN p_section INT, IN p_now DATETIME,
                           OUT p_code INT, OUT p_msg VARCHAR(255))
proc: BEGIN
  DECLARE v_task INT;
  DECLARE v_term VARCHAR(12);
  DECLARE v_nature VARCHAR(4);
  DECLARE v_from DATETIME;
  DECLARE v_to DATETIME;
  DECLARE v_cap INT;
  DECLARE v_cnt INT;
  DECLARE v_conflict VARCHAR(200);
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    GET DIAGNOSTICS CONDITION 1 p_msg = MESSAGE_TEXT;
    ROLLBACK;
    SET p_code = 9;
  END;

  SET p_code = 0, p_msg = '';
  SELECT s.task_id, t.term_id, t.nature, tm.select_start, tm.select_end
    INTO v_task, v_term, v_nature, v_from, v_to
    FROM section s JOIN teaching_task t ON t.id = s.task_id JOIN term tm ON tm.id = t.term_id
   WHERE s.id = p_section;
  IF v_task IS NULL THEN
    SET p_code = 1, p_msg = '教学班不存在';
    LEAVE proc;
  END IF;
  IF v_from IS NULL OR v_to IS NULL OR p_now < v_from OR p_now > v_to THEN
    SET p_code = 2, p_msg = CONCAT(v_term, ' 学期当前不在选课时段');
    LEAVE proc;
  END IF;
  IF v_nature = '必修' AND NOT EXISTS (
       SELECT 1 FROM student st JOIN teaching_task_class tc ON tc.class_id = st.class_id
        WHERE st.id = p_student AND tc.task_id = v_task) THEN
    SET p_code = 3, p_msg = '该课程不是你所在班级本学期的必修课';
    LEAVE proc;
  END IF;

  START TRANSACTION;
  -- 行锁：同一教学班的并发选课在此排队，随后读到的 enrolled_count 一定是最新值，不会超卖
  SELECT capacity, enrolled_count INTO v_cap, v_cnt FROM section WHERE id = p_section FOR UPDATE;

  IF EXISTS (SELECT 1 FROM enrollment WHERE student_id = p_student AND task_id = v_task) THEN
    ROLLBACK;
    SET p_code = 4, p_msg = '你已选择本课程的教学班，如需更换请先退选';
    LEAVE proc;
  END IF;

  -- 与本学期已选课程的上课时间（星期、节次、周次三者同时重叠）冲突
  SELECT CONCAT('与已选课程《', c.name, '》上课时间冲突（',
                ELT(n.weekday, '周一','周二','周三','周四','周五','周六','周日'), '第', n.start_period, '节）')
    INTO v_conflict
    FROM schedule_slot n
    JOIN enrollment e      ON e.student_id = p_student
    JOIN teaching_task t2  ON t2.id = e.task_id AND t2.term_id = v_term
    JOIN schedule_slot o   ON o.section_id = e.section_id
    JOIN course c          ON c.id = t2.course_id
   WHERE n.section_id = p_section
     AND o.weekday = n.weekday
     AND o.start_period < n.start_period + n.period_count AND n.start_period < o.start_period + o.period_count
     AND o.week_start <= n.week_end AND n.week_start <= o.week_end
   LIMIT 1;
  IF v_conflict IS NOT NULL THEN
    ROLLBACK;
    SET p_code = 5, p_msg = v_conflict;
    LEAVE proc;
  END IF;

  IF v_cnt >= v_cap THEN
    ROLLBACK;
    SET p_code = 6, p_msg = '教学班人数已满，选课失败';
    LEAVE proc;
  END IF;

  INSERT INTO enrollment (student_id, task_id, section_id, selected_at) VALUES (p_student, v_task, p_section, p_now);
  COMMIT;
END$$

-- 退选：只能在自由选课时段内
CREATE PROCEDURE sp_drop(IN p_student VARCHAR(20), IN p_task INT, IN p_now DATETIME,
                         OUT p_code INT, OUT p_msg VARCHAR(255))
proc: BEGIN
  DECLARE v_from DATETIME;
  DECLARE v_to DATETIME;
  DECLARE v_found INT DEFAULT 0;
  DECLARE v_section INT;
  DECLARE EXIT HANDLER FOR SQLEXCEPTION
  BEGIN
    GET DIAGNOSTICS CONDITION 1 p_msg = MESSAGE_TEXT;
    ROLLBACK;
    SET p_code = 9;
  END;

  SET p_code = 0, p_msg = '';
  SELECT COUNT(*), MAX(tm.select_start), MAX(tm.select_end) INTO v_found, v_from, v_to
    FROM teaching_task t JOIN term tm ON tm.id = t.term_id WHERE t.id = p_task;
  IF v_found = 0 THEN
    SET p_code = 1, p_msg = '课程不存在';
    LEAVE proc;
  END IF;
  IF v_from IS NULL OR v_to IS NULL OR p_now < v_from OR p_now > v_to THEN
    SET p_code = 2, p_msg = '当前不在自由选课时段，不能退选';
    LEAVE proc;
  END IF;

  START TRANSACTION;
  SELECT section_id INTO v_section FROM enrollment WHERE student_id = p_student AND task_id = p_task;
  IF v_section IS NULL THEN
    ROLLBACK;
    SET p_code = 4, p_msg = '你没有选择该课程';
    LEAVE proc;
  END IF;
  SELECT id INTO v_section FROM section WHERE id = v_section FOR UPDATE;   -- 与选课同一把行锁
  DELETE FROM enrollment WHERE student_id = p_student AND task_id = p_task;
  COMMIT;
END$$

-- 线性回归预测：x = 学年起始年，y = 该课程同一学期序号（上 / 下学期）的历年选课人数
--   斜率 b = (n·Σxy − Σx·Σy) / (n·Σx² − (Σx)²)，截距 a = (Σy − b·Σx) / n，预测 = a + b·x_target
--   只有 1 年数据时沿用上年人数；没有历史数据返回 NULL
CREATE FUNCTION fn_forecast_enroll(p_course VARCHAR(20), p_term VARCHAR(12)) RETURNS INT
READS SQL DATA
BEGIN
  DECLARE v_year INT DEFAULT CAST(SUBSTRING_INDEX(p_term, '-', 1) AS UNSIGNED);
  DECLARE v_no INT DEFAULT CAST(SUBSTRING_INDEX(p_term, '-', -1) AS UNSIGNED);
  DECLARE v_n INT;
  DECLARE v_sx DOUBLE; DECLARE v_sy DOUBLE; DECLARE v_sxy DOUBLE; DECLARE v_sxx DOUBLE;
  DECLARE v_b DOUBLE; DECLARE v_a DOUBLE;

  SELECT COUNT(*), SUM(year_start), SUM(enrolled), SUM(year_start * enrolled), SUM(year_start * year_start)
    INTO v_n, v_sx, v_sy, v_sxy, v_sxx
    FROM v_course_term_enroll
   WHERE course_id = p_course AND term_no = v_no AND year_start < v_year;
  IF v_n = 0 THEN
    RETURN NULL;
  END IF;
  IF v_n = 1 THEN
    RETURN GREATEST(0, FLOOR(v_sy + 0.5));
  END IF;
  SET v_b = (v_n * v_sxy - v_sx * v_sy) / (v_n * v_sxx - v_sx * v_sx);
  SET v_a = (v_sy - v_b * v_sx) / v_n;
  RETURN GREATEST(0, FLOOR(v_a + v_b * v_year + 0.5));
END$$

DELIMITER ;
