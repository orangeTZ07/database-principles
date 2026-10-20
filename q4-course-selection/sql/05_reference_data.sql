-- =====================================================================
-- 选课管理系统 · 05 参照数据：系统参数、角色权限
-- （管理员账号 admin 由 Python 初始化脚本写入，因为密码以散列形式保存）
-- =====================================================================
SET NAMES utf8mb4;

INSERT INTO setting (setting_key, setting_value) VALUES ('section_limit', '60');   -- 教学班人数限额（可在“教学任务”页修改）

INSERT INTO role_permission (role, func) VALUES
  ('admin','plan'),('admin','basic'),('admin','progress'),('admin','tasks'),('admin','timetable'),
  ('admin','enroll'),('admin','query'),('admin','stats'),('admin','mining'),('admin','users'),
  ('research','plan'),('research','query'),
  ('academic','progress'),('academic','tasks'),('academic','timetable'),('academic','stats'),
  ('academic','mining'),('academic','query'),
  ('teacher','query'),
  ('student','enroll'),('student','query');
