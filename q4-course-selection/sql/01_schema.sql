-- =====================================================================
-- 选课管理系统 · 01 建表脚本（MySQL 8.0+，InnoDB，utf8mb4）
--
-- 设计要点
--   1. 满足 3NF：学生的专业 / 入学年份 / 学院由所属行政班推导（见视图 v_student），不冗余存储
--   2. 业务规则尽量下沉到数据库，由 DBMS 保证数据一致性：
--        PRIMARY KEY / UNIQUE / FOREIGN KEY / CHECK / ENUM / 生成列唯一索引 / 触发器 / 存储过程
--      - 账号全英文且不区分大小写唯一         （CHECK + utf8mb4_0900_ai_ci 唯一主键）
--      - 每个课头只有一个主讲教师             （虚拟生成列 + 唯一索引，等价于“部分唯一索引”）
--      - 同一学期同一课程只能选一个教学班     （enrollment 主键 (student_id, task_id)）
--      - 选课人数不超过教学班容量             （存储过程行锁 + 触发器 + CHECK 三重保证）
--   3. 本脚本可重复执行（先删后建）
-- =====================================================================
SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;
DROP TABLE IF EXISTS enrollment_log, enrollment, course_forecast, schedule_slot, section_teacher, section,
  teaching_task_class, teaching_task, term, curriculum_plan, teacher_course, course_prereq, course,
  app_user, role_permission, setting, classroom, teacher, student, admin_class, major, org_unit;
SET FOREIGN_KEY_CHECKS = 1;

-- ---------------------------------------------------------------- 基础信息
CREATE TABLE org_unit (
  id        INT          NOT NULL AUTO_INCREMENT COMMENT '主键',
  name      VARCHAR(60)  NOT NULL COMMENT '学院 / 系名称',
  kind      ENUM('学院','系') NOT NULL COMMENT '类型',
  parent_id INT          NULL COMMENT '系所属学院',
  PRIMARY KEY (id),
  UNIQUE KEY uk_org_name (name),
  CONSTRAINT fk_org_parent FOREIGN KEY (parent_id) REFERENCES org_unit (id),
  CONSTRAINT ck_org_kind CHECK ((kind = '学院' AND parent_id IS NULL) OR (kind = '系' AND parent_id IS NOT NULL))
) ENGINE=InnoDB COMMENT='学院 / 系';

CREATE TABLE major (
  id         INT         NOT NULL AUTO_INCREMENT,
  name       VARCHAR(60) NOT NULL,
  college_id INT         NOT NULL COMMENT '所属学院',
  PRIMARY KEY (id),
  UNIQUE KEY uk_major_name (name),
  CONSTRAINT fk_major_college FOREIGN KEY (college_id) REFERENCES org_unit (id)
) ENGINE=InnoDB COMMENT='专业';

CREATE TABLE admin_class (
  id          INT         NOT NULL AUTO_INCREMENT,
  name        VARCHAR(60) NOT NULL,
  major_id    INT         NOT NULL COMMENT '所属专业',
  enroll_year SMALLINT    NOT NULL COMMENT '入学年份（年级）',
  PRIMARY KEY (id),
  UNIQUE KEY uk_class_name (name),
  KEY ix_class_major_year (major_id, enroll_year),
  CONSTRAINT fk_class_major FOREIGN KEY (major_id) REFERENCES major (id),
  CONSTRAINT ck_class_year CHECK (enroll_year BETWEEN 2000 AND 2100)
) ENGINE=InnoDB COMMENT='行政班级';

CREATE TABLE student (
  id       VARCHAR(20) NOT NULL COMMENT '学号',
  name     VARCHAR(30) NOT NULL,
  gender   ENUM('男','女') NULL,
  class_id INT         NOT NULL COMMENT '所属行政班（专业、入学年份由班级推导）',
  PRIMARY KEY (id),
  KEY ix_student_class (class_id),
  CONSTRAINT fk_student_class FOREIGN KEY (class_id) REFERENCES admin_class (id)
) ENGINE=InnoDB COMMENT='学生';

CREATE TABLE teacher (
  id      VARCHAR(20) NOT NULL COMMENT '工号',
  name    VARCHAR(30) NOT NULL,
  title   ENUM('助教','讲师','副教授','教授') NOT NULL COMMENT '职称',
  dept_id INT         NOT NULL COMMENT '所在系',
  PRIMARY KEY (id),
  KEY ix_teacher_dept (dept_id),
  CONSTRAINT fk_teacher_dept FOREIGN KEY (dept_id) REFERENCES org_unit (id)
) ENGINE=InnoDB COMMENT='教师';

CREATE TABLE classroom (
  id            INT         NOT NULL AUTO_INCREMENT,
  area          VARCHAR(30) NOT NULL COMMENT '教学区',
  building      VARCHAR(30) NOT NULL COMMENT '教学楼',
  room_no       VARCHAR(20) NOT NULL COMMENT '房间号',
  floor         TINYINT     NOT NULL COMMENT '楼层',
  is_multimedia TINYINT(1)  NOT NULL DEFAULT 0 COMMENT '是否多媒体教室',
  capacity      SMALLINT    NOT NULL COMMENT '容量（座位数）',
  PRIMARY KEY (id),
  UNIQUE KEY uk_room (building, room_no),
  KEY ix_room_capacity (capacity),
  CONSTRAINT ck_room_floor CHECK (floor BETWEEN 1 AND 30),
  CONSTRAINT ck_room_mm CHECK (is_multimedia IN (0,1)),
  CONSTRAINT ck_room_cap CHECK (capacity > 0)
) ENGINE=InnoDB COMMENT='教室';

-- ---------------------------------------------------------------- 课程与培养方案（教研科）
CREATE TABLE course (
  id    VARCHAR(20)  NOT NULL COMMENT '课程号',
  name  VARCHAR(60)  NOT NULL,
  intro VARCHAR(500) NULL COMMENT '课程简介',
  PRIMARY KEY (id)
) ENGINE=InnoDB COMMENT='课程';

CREATE TABLE course_prereq (
  course_id VARCHAR(20) NOT NULL,
  prereq_id VARCHAR(20) NOT NULL COMMENT '先导课程',
  PRIMARY KEY (course_id, prereq_id),
  KEY ix_prereq_prereq (prereq_id),
  CONSTRAINT fk_prereq_course FOREIGN KEY (course_id) REFERENCES course (id) ON DELETE CASCADE,
  CONSTRAINT fk_prereq_prereq FOREIGN KEY (prereq_id) REFERENCES course (id)
) ENGINE=InnoDB COMMENT='先导课程（自身不能为先导、不得循环依赖：见触发器 trg_prereq_acyclic）';

CREATE TABLE teacher_course (
  teacher_id VARCHAR(20) NOT NULL,
  course_id  VARCHAR(20) NOT NULL,
  PRIMARY KEY (teacher_id, course_id),
  KEY ix_tc_course (course_id),
  CONSTRAINT fk_tc_teacher FOREIGN KEY (teacher_id) REFERENCES teacher (id) ON DELETE CASCADE,
  CONSTRAINT fk_tc_course FOREIGN KEY (course_id) REFERENCES course (id) ON DELETE CASCADE
) ENGINE=InnoDB COMMENT='教师可上课程（任课资格）';

CREATE TABLE curriculum_plan (
  id          INT          NOT NULL AUTO_INCREMENT,
  major_id    INT          NOT NULL,
  semester_no TINYINT      NOT NULL COMMENT '计划开课学期（四年制 1~8）',
  course_id   VARCHAR(20)  NOT NULL,
  hours       SMALLINT     NOT NULL COMMENT '学时',
  credits     DECIMAL(3,1) NOT NULL COMMENT '学分',
  college_id  INT          NOT NULL COMMENT '开课学院',
  nature      ENUM('必修','任选') NOT NULL DEFAULT '必修',
  PRIMARY KEY (id),
  UNIQUE KEY uk_plan_major_course (major_id, course_id),
  KEY ix_plan_major_sem (major_id, semester_no),
  KEY ix_plan_course (course_id),
  CONSTRAINT fk_plan_major FOREIGN KEY (major_id) REFERENCES major (id),
  CONSTRAINT fk_plan_course FOREIGN KEY (course_id) REFERENCES course (id),
  CONSTRAINT fk_plan_college FOREIGN KEY (college_id) REFERENCES org_unit (id),
  CONSTRAINT ck_plan_sem CHECK (semester_no BETWEEN 1 AND 8),
  CONSTRAINT ck_plan_hours CHECK (hours > 0),
  CONSTRAINT ck_plan_credits CHECK (credits > 0)
) ENGINE=InnoDB COMMENT='专业培养方案：专业 × 学期 × 课程';

-- ---------------------------------------------------------------- 学期、教学进程、教学任务、课头
CREATE TABLE term (
  id           VARCHAR(12) NOT NULL COMMENT '学期，如 2026-2027-2',
  year_start   SMALLINT    NOT NULL COMMENT '学年起始年',
  term_no      TINYINT     NOT NULL COMMENT '1=上学期 2=下学期',
  weeks        TINYINT     NOT NULL DEFAULT 16 COMMENT '教学日历周数',
  start_date   DATE        NOT NULL COMMENT '开学日期',
  select_start DATETIME    NULL COMMENT '自由选课开始',
  select_end   DATETIME    NULL COMMENT '自由选课截止',
  PRIMARY KEY (id),
  UNIQUE KEY uk_term_year_no (year_start, term_no),
  CONSTRAINT ck_term_no CHECK (term_no IN (1,2)),
  CONSTRAINT ck_term_weeks CHECK (weeks BETWEEN 1 AND 30),
  CONSTRAINT ck_term_id CHECK (id = CONCAT(year_start, '-', year_start + 1, '-', term_no)),
  CONSTRAINT ck_term_select CHECK (select_start IS NULL OR select_end IS NULL OR select_start <= select_end)
) ENGINE=InnoDB COMMENT='学期（教学进程计划）';

CREATE TABLE teaching_task (
  id             INT         NOT NULL AUTO_INCREMENT,
  term_id        VARCHAR(12) NOT NULL COMMENT '开课学期',
  course_id      VARCHAR(20) NOT NULL,
  hours          SMALLINT    NOT NULL COMMENT '学时',
  nature         ENUM('必修','任选') NOT NULL,
  required_count INT         NOT NULL DEFAULT 0 COMMENT '必修学生人数',
  expected_count INT         NULL COMMENT '任选课预计人数（数据挖掘预测或手工填写）',
  PRIMARY KEY (id),
  UNIQUE KEY uk_task_term_course (term_id, course_id),
  KEY ix_task_course (course_id),
  CONSTRAINT fk_task_term FOREIGN KEY (term_id) REFERENCES term (id) ON DELETE CASCADE,
  CONSTRAINT fk_task_course FOREIGN KEY (course_id) REFERENCES course (id),
  CONSTRAINT ck_task_hours CHECK (hours > 0),
  CONSTRAINT ck_task_req CHECK (required_count >= 0),
  CONSTRAINT ck_task_exp CHECK (expected_count IS NULL OR expected_count >= 0)
) ENGINE=InnoDB COMMENT='教学任务（由教学进程计划 + 培养方案自动生成）';

CREATE TABLE teaching_task_class (
  task_id       INT NOT NULL,
  class_id      INT NOT NULL,
  student_count INT NOT NULL COMMENT '该班必修人数',
  PRIMARY KEY (task_id, class_id),
  KEY ix_ttc_class (class_id),
  CONSTRAINT fk_ttc_task FOREIGN KEY (task_id) REFERENCES teaching_task (id) ON DELETE CASCADE,
  CONSTRAINT fk_ttc_class FOREIGN KEY (class_id) REFERENCES admin_class (id),
  CONSTRAINT ck_ttc_cnt CHECK (student_count >= 0)
) ENGINE=InnoDB COMMENT='教学任务涉及的行政班（必修）';

CREATE TABLE section (
  id             INT      NOT NULL AUTO_INCREMENT,
  task_id        INT      NOT NULL,
  section_no     SMALLINT NOT NULL COMMENT '课头序号',
  capacity       SMALLINT NOT NULL COMMENT '教学班人数上限',
  enrolled_count SMALLINT NOT NULL DEFAULT 0 COMMENT '已选人数（由触发器维护）',
  PRIMARY KEY (id),
  UNIQUE KEY uk_section_no (task_id, section_no),
  UNIQUE KEY uk_section_id_task (id, task_id),
  CONSTRAINT fk_section_task FOREIGN KEY (task_id) REFERENCES teaching_task (id) ON DELETE CASCADE,
  CONSTRAINT ck_section_no CHECK (section_no > 0),
  CONSTRAINT ck_section_cap CHECK (capacity > 0),
  CONSTRAINT ck_section_enrolled CHECK (enrolled_count BETWEEN 0 AND capacity)
) ENGINE=InnoDB COMMENT='课头（教学班）';

CREATE TABLE section_teacher (
  section_id INT         NOT NULL,
  teacher_id VARCHAR(20) NOT NULL,
  is_lead    TINYINT(1)  NOT NULL DEFAULT 0 COMMENT '是否主讲',
  -- 虚拟生成列：主讲时等于 section_id，否则为 NULL；对它建唯一索引，
  -- 即“同一课头最多一个主讲”（MySQL 没有部分索引，用生成列 + 唯一索引实现）
  lead_key   INT GENERATED ALWAYS AS (IF(is_lead = 1, section_id, NULL)) VIRTUAL,
  PRIMARY KEY (section_id, teacher_id),
  UNIQUE KEY ux_section_one_lead (lead_key),
  KEY ix_st_teacher (teacher_id),
  CONSTRAINT fk_st_section FOREIGN KEY (section_id) REFERENCES section (id) ON DELETE CASCADE,
  CONSTRAINT fk_st_teacher FOREIGN KEY (teacher_id) REFERENCES teacher (id),
  CONSTRAINT ck_st_lead CHECK (is_lead IN (0,1))
) ENGINE=InnoDB COMMENT='课头教师（可多人合上，仅一个主讲）';

-- ---------------------------------------------------------------- 课程表
CREATE TABLE schedule_slot (
  id           INT      NOT NULL AUTO_INCREMENT,
  section_id   INT      NOT NULL,
  weekday      TINYINT  NOT NULL COMMENT '星期 1~7',
  start_period TINYINT  NOT NULL COMMENT '起始节次',
  period_count TINYINT  NOT NULL COMMENT '连续节数',
  week_start   TINYINT  NOT NULL COMMENT '起始周',
  week_end     TINYINT  NOT NULL COMMENT '结束周',
  classroom_id INT      NOT NULL,
  PRIMARY KEY (id),
  KEY ix_slot_room (classroom_id, weekday),
  KEY ix_slot_section (section_id),
  CONSTRAINT fk_slot_section FOREIGN KEY (section_id) REFERENCES section (id) ON DELETE CASCADE,
  CONSTRAINT fk_slot_room FOREIGN KEY (classroom_id) REFERENCES classroom (id),
  CONSTRAINT ck_slot_weekday CHECK (weekday BETWEEN 1 AND 7),
  CONSTRAINT ck_slot_start CHECK (start_period BETWEEN 1 AND 12),
  CONSTRAINT ck_slot_count CHECK (period_count BETWEEN 1 AND 4),
  CONSTRAINT ck_slot_end CHECK (start_period + period_count - 1 <= 12),
  CONSTRAINT ck_slot_weeks CHECK (week_start >= 1 AND week_start <= week_end)
) ENGINE=InnoDB COMMENT='课程表：课头在某星期某节次某周次范围占用的教室（教室冲突见触发器）';

-- ---------------------------------------------------------------- 选课
CREATE TABLE enrollment (
  student_id  VARCHAR(20) NOT NULL,
  task_id     INT         NOT NULL,
  section_id  INT         NOT NULL,
  selected_at DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (student_id, task_id),            -- 同一学期同一课程只能选一个教学班
  KEY ix_enroll_section (section_id, task_id),
  KEY ix_enroll_task (task_id),
  CONSTRAINT fk_enroll_student FOREIGN KEY (student_id) REFERENCES student (id),
  CONSTRAINT fk_enroll_section FOREIGN KEY (section_id, task_id) REFERENCES section (id, task_id)
) ENGINE=InnoDB COMMENT='选课库（已选列表）';

CREATE TABLE enrollment_log (
  id         BIGINT      NOT NULL AUTO_INCREMENT,
  student_id VARCHAR(20) NOT NULL,
  section_id INT         NOT NULL,
  action     ENUM('选课','退选') NOT NULL,
  acted_at   DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  KEY ix_log_student (student_id, acted_at),
  KEY ix_log_section (section_id)
) ENGINE=InnoDB COMMENT='选课 / 退选流水（由触发器写入，不设外键以便保留历史）';

-- ---------------------------------------------------------------- 数据挖掘结果
CREATE TABLE course_forecast (
  id                       INT         NOT NULL AUTO_INCREMENT,
  term_id                  VARCHAR(12) NOT NULL,
  course_id                VARCHAR(20) NOT NULL,
  predicted_count          INT         NOT NULL COMMENT '预测选课人数',
  method                   VARCHAR(60) NOT NULL COMMENT '预测方法',
  sample_size              INT         NOT NULL COMMENT '历史样本数（年）',
  recommended_classroom_id INT         NULL COMMENT '按预测人数推荐的教室（容量决定）',
  created_at               DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_forecast (term_id, course_id),
  KEY ix_forecast_course (course_id),
  KEY ix_forecast_room (recommended_classroom_id),
  CONSTRAINT fk_forecast_term FOREIGN KEY (term_id) REFERENCES term (id) ON DELETE CASCADE,
  CONSTRAINT fk_forecast_course FOREIGN KEY (course_id) REFERENCES course (id),
  CONSTRAINT fk_forecast_room FOREIGN KEY (recommended_classroom_id) REFERENCES classroom (id),
  CONSTRAINT ck_forecast_cnt CHECK (predicted_count >= 0)
) ENGINE=InnoDB COMMENT='任选课人数预测结果';

-- ---------------------------------------------------------------- 用户与权限
CREATE TABLE app_user (
  -- 全英文账号：CHECK 限制只含字母（'c' 表示区分大小写匹配），主键使用 *_ci 排序规则，因此 Admin 与 admin 视为重复
  username      VARCHAR(32) COLLATE utf8mb4_0900_ai_ci NOT NULL,
  password_hash VARCHAR(255) NOT NULL COMMENT '密码散列（不存明文）',
  role          ENUM('admin','research','academic','teacher','student') NOT NULL,
  is_active     TINYINT(1)  NOT NULL DEFAULT 1 COMMENT '1 启用 / 0 停用',
  student_id    VARCHAR(20) NULL,
  teacher_id    VARCHAR(20) NULL,
  created_at    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (username),
  UNIQUE KEY uk_user_student (student_id),
  UNIQUE KEY uk_user_teacher (teacher_id),
  CONSTRAINT fk_user_student FOREIGN KEY (student_id) REFERENCES student (id),
  CONSTRAINT fk_user_teacher FOREIGN KEY (teacher_id) REFERENCES teacher (id),
  CONSTRAINT ck_user_name CHECK (REGEXP_LIKE(username, '^[A-Za-z]{1,32}$', 'c')),
  CONSTRAINT ck_user_active CHECK (is_active IN (0,1)),
  CONSTRAINT ck_user_role CHECK (
    (role = 'student' AND student_id IS NOT NULL AND teacher_id IS NULL) OR
    (role = 'teacher' AND teacher_id IS NOT NULL AND student_id IS NULL) OR
    (role IN ('admin','research','academic') AND student_id IS NULL AND teacher_id IS NULL))
) ENGINE=InnoDB COMMENT='系统账号';

CREATE TABLE role_permission (
  role ENUM('admin','research','academic','teacher','student') NOT NULL,
  func VARCHAR(20) NOT NULL COMMENT '系统功能',
  PRIMARY KEY (role, func)
) ENGINE=InnoDB COMMENT='角色 → 功能权限';

CREATE TABLE setting (
  setting_key   VARCHAR(40)  NOT NULL,
  setting_value VARCHAR(200) NOT NULL,
  PRIMARY KEY (setting_key)
) ENGINE=InnoDB COMMENT='系统参数（如教学班人数限额）';
