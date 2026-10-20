-- =====================================================================
-- 选课管理系统 · 03 触发器
-- 触发器把无法用 CHECK / 外键表达的跨表规则放在数据库层，任何客户端（含手写 SQL）都绕不过
-- 本文件使用 mysql 客户端的 DELIMITER 语法（应用的加载器同样支持）
-- =====================================================================
SET NAMES utf8mb4;

DROP TRIGGER IF EXISTS trg_prereq_acyclic;
DROP TRIGGER IF EXISTS trg_section_teacher_qualified;
DROP TRIGGER IF EXISTS trg_slot_room_conflict;
DROP TRIGGER IF EXISTS trg_enroll_capacity;
DROP TRIGGER IF EXISTS trg_enroll_after_insert;
DROP TRIGGER IF EXISTS trg_enroll_after_delete;

DELIMITER $$

-- 先导课程：不能以自己为先导，也不能形成循环依赖（递归 CTE 沿“先导的先导”向上找）
CREATE TRIGGER trg_prereq_acyclic BEFORE INSERT ON course_prereq FOR EACH ROW
BEGIN
  DECLARE v_course VARCHAR(20) DEFAULT NEW.course_id;
  DECLARE v_prereq VARCHAR(20) DEFAULT NEW.prereq_id;
  DECLARE v_hit INT DEFAULT 0;
  IF v_course = v_prereq THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '课程不能以自己作为先导课程';
  END IF;
  WITH RECURSIVE ancestors (id) AS (
    SELECT v_prereq
    UNION
    SELECT p.prereq_id FROM course_prereq p JOIN ancestors a ON p.course_id = a.id
  )
  SELECT COUNT(*) INTO v_hit FROM ancestors WHERE id = v_course;
  IF v_hit > 0 THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '先导课程形成循环依赖';
  END IF;
END$$

-- 教师必须具备该课程的任课资格（teacher_course）
CREATE TRIGGER trg_section_teacher_qualified BEFORE INSERT ON section_teacher FOR EACH ROW
BEGIN
  IF NOT EXISTS (
       SELECT 1 FROM section s
         JOIN teaching_task t   ON t.id = s.task_id
         JOIN teacher_course tc ON tc.course_id = t.course_id
        WHERE s.id = NEW.section_id AND tc.teacher_id = NEW.teacher_id) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '该教师不具备本课程的任课资格';
  END IF;
END$$

-- 课程表：周次不得超过学期教学日历周数；同一学期内同一教室、同一星期、节次重叠且周次重叠视为冲突
CREATE TRIGGER trg_slot_room_conflict BEFORE INSERT ON schedule_slot FOR EACH ROW
BEGIN
  DECLARE v_term VARCHAR(12);
  DECLARE v_weeks INT;
  SELECT t.term_id, tm.weeks INTO v_term, v_weeks
    FROM section s JOIN teaching_task t ON t.id = s.task_id JOIN term tm ON tm.id = t.term_id
   WHERE s.id = NEW.section_id;
  IF NEW.week_end > v_weeks THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '结束周超过本学期教学日历周数';
  END IF;
  IF EXISTS (
       SELECT 1 FROM schedule_slot x
         JOIN section sx       ON sx.id = x.section_id
         JOIN teaching_task tx ON tx.id = sx.task_id
        WHERE tx.term_id = v_term AND x.classroom_id = NEW.classroom_id AND x.weekday = NEW.weekday
          AND x.start_period < NEW.start_period + NEW.period_count
          AND NEW.start_period < x.start_period + x.period_count
          AND x.week_start <= NEW.week_end AND NEW.week_start <= x.week_end) THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '教室时间冲突：该教室在此时段已被占用';
  END IF;
END$$

-- 选课：教学班人数上限。先对课头行加排他锁（FOR UPDATE），并发选课被串行化，保证不超员
CREATE TRIGGER trg_enroll_capacity BEFORE INSERT ON enrollment FOR EACH ROW
BEGIN
  DECLARE v_cap INT;
  DECLARE v_cnt INT;
  SELECT capacity, enrolled_count INTO v_cap, v_cnt FROM section WHERE id = NEW.section_id FOR UPDATE;
  IF v_cnt >= v_cap THEN
    SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT = '教学班人数已满，选课失败';
  END IF;
END$$

-- 选课成功：维护已选人数，写入流水
CREATE TRIGGER trg_enroll_after_insert AFTER INSERT ON enrollment FOR EACH ROW
BEGIN
  UPDATE section SET enrolled_count = enrolled_count + 1 WHERE id = NEW.section_id;
  INSERT INTO enrollment_log(student_id, section_id, action) VALUES (NEW.student_id, NEW.section_id, '选课');
END$$

-- 退选：维护已选人数，写入流水
CREATE TRIGGER trg_enroll_after_delete AFTER DELETE ON enrollment FOR EACH ROW
BEGIN
  UPDATE section SET enrolled_count = enrolled_count - 1 WHERE id = OLD.section_id;
  INSERT INTO enrollment_log(student_id, section_id, action) VALUES (OLD.student_id, OLD.section_id, '退选');
END$$

DELIMITER ;
