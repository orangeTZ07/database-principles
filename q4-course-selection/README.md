# 第四题 · 选课管理系统

> 数据库课程设计：教研科制定培养方案 → 教务科制定教学进程、教学任务、课程表 → 学生选课 → 查询统计与数据挖掘。
> **核心业务规则都放在 MySQL 里**：约束、视图、触发器、存储过程、存储函数、事务与行锁、窗口函数、递归 CTE。
> Python/Flask 只是一层薄薄的界面。

## 目录

```
q4-course-selection/
├── sql/                    ← 数据库本体（可以不用 Python，单独用 mysql 客户端执行）
│   ├── 01_schema.sql         22 张表：主外键、CHECK、ENUM、生成列唯一索引、索引设计
│   ├── 02_views.sql          8 个视图：班级/学生/课头/课表、教学进程、同比环比统计的数据源
│   ├── 03_triggers.sql       6 个触发器：先导课程防环(递归 CTE)、教师资格、教室冲突、选课容量(行锁)、人数/流水维护
│   ├── 04_routines.sql       存储过程 sp_generate_tasks / sp_enroll / sp_drop，存储函数 fn_forecast_enroll(线性回归)
│   ├── 05_reference_data.sql 角色权限、系统参数
│   └── 06_report_queries.sql 演示 / 报表查询（培养方案、课表、同比环比 LAG、预测与教室推荐……）
├── app/                    ← Flask 应用（路由、模板、排课算法）
├── tests/                  ← 66 个测试（unittest，真实连接 MySQL）
├── docs/数据库设计.md        ER 图、表结构、范式、索引、事务与并发、触发器 / 存储过程说明
├── docs/需求对照.md          题目每一条需求 → 实现位置
├── run.py                  ← 启动入口（首次运行自动建库 + 导入演示数据）
└── requirements.txt
```

## 快速开始

### 1. 准备 MySQL 8.0+

需要一个能创建库、建触发器 / 存储过程的账号。最省事：直接用 `root`。

> **关于 binlog**：MySQL 8 默认开启二进制日志，此时非 SUPER 账号创建触发器 / 存储函数会报 *1419*。
> 要么用 root 初始化，要么由 DBA 先执行一次 `SET GLOBAL log_bin_trust_function_creators = 1;`。

也可以新建一个专用账号（库名以 `course_selection` 开头，测试库叫 `course_selection_test_*`）：

```sql
CREATE USER 'course'@'%' IDENTIFIED BY '换成你自己的密码';
GRANT ALL PRIVILEGES ON `course\_selection%`.* TO 'course'@'%';
```

没有现成的 MySQL 时，可以用 Docker 启一个（命令为标准用法，作者的沙箱里没有 Docker，实际验证用的是 MySQL 8.4.6 官方二进制包）：

```bash
docker run -d --name mysql8 -p 3306:3306 -e MYSQL_ROOT_PASSWORD=root mysql:8.4 --log-bin-trust-function-creators=1
```

### 2. 安装依赖并启动

```bash
cd q4-course-selection
pip install -r requirements.txt

# 连接参数用环境变量设置（括号内为默认值）
export MYSQL_HOST=127.0.0.1 MYSQL_PORT=3306 MYSQL_USER=root MYSQL_PASSWORD=root MYSQL_DATABASE=course_selection

python run.py              # 首次运行自动建库 → 执行 sql/*.sql → 导入演示数据（约 3~5 秒），然后打开 http://127.0.0.1:5000
python run.py --reset      # 删除旧库并重建
python run.py --no-seed    # 只建空库（仅有管理员 admin / 123）
python run.py --port 8080
```

> 只想看数据库本身：`mysql -uroot -p -e "CREATE DATABASE course_selection DEFAULT CHARSET utf8mb4"`，
> 然后依次 `mysql -uroot -p course_selection < sql/0X_xxx.sql`（01→05），再 `source sql/06_report_queries.sql`。

### 3. 演示账号（初始密码一律为 `123`）

| 角色 | 账号 | 能做什么 |
| --- | --- | --- |
| 系统管理员 | `admin` | 全部功能（含用户管理、基础信息维护） |
| 教研科 | `research` | 制定专业培养方案（课程、先导课程、各专业各学期开课计划） |
| 教务科 | `academic` | 教学进程、教学任务（课头拆分/教师分配）、课程表、选课统计、数据挖掘 |
| 学生 | `hupeng`（计科2301班 202301001）、`zhuchaomin`（计科2401班）、`linwei`（计科2601班） | 选课 / 退选、查询课表 |
| 教师 | `hexuepeng`（计算机科学系）、`guoyangkai`（软件工程系） | 查询自己的课表、教学班名单 |

学生 / 教师账号是按姓名拼音生成的，完整列表在 admin 的「用户管理」页面。

演示数据：3 个专业 × 4 个年级 × 40 人 = 480 名学生、28 名教师、30 门课程、72 间教室、2023~2026 共 8 个学期。
其中 **2026-2027-1 学期已排好课表并有选课记录；2026-2027-2 学期正在开放选课**（选课窗口为初始化当天前一天到之后 30 天），可以直接用学生账号选课。

## 功能一览

| 角色 | 功能 |
| --- | --- |
| 教研科 | 课程与先导课程（保存时检测循环依赖）、专业培养方案（各专业各学期计划开课、学时 / 学分 / 开课学院，四年制 8 学期） |
| 系统管理员 | 学院 / 系、专业、行政班、学生、教师（可上课程）、教室（教学区 / 多媒体 / 容量）；用户管理（添加、停用 / 启用、复位密码） |
| 教务科 | **教学进程**：录入学期（教学日历周数、选课窗口），一键生成教学任务，并提示「应在上学期期中前制定」<br>**教学任务**：必修课按班额限额 / 教室最大容量拆课头，任选课按预测人数拆；多教师合上、仅一名主讲<br>**课程表**：手工 + 自动排课，冲突检测（教室 / 教师 / 班级 / 容量 / 周次），同课头尽量保持教室不变、必须换时就近<br>**选课统计**：班级、教师维度的选课数与学时，同比 / 环比，图表 + 表格 + 打印<br>**数据挖掘**：用历年选课人数做线性回归预测任选课人数，按预测人数推荐教室并可一键拆课头 |
| 学生 | 本学期必修课程列表、任选课程；选教学班（有空位才成功，满员拒绝）、选课时间冲突提示、选课时段内可退选；课表查询与打印 |
| 教师 / 全部角色 | 培养方案查询、选课综合查询（按学生 / 班级 / 课程 / 教学班）、课表打印 |
| 所有人 | 登录、修改密码（必须提供原密码）、CSRF 防护；账号全英文、不区分大小写不重复；停用账号立刻失效 |

## MySQL 在这个项目里做了什么

| MySQL 特性 | 用在哪里 |
| --- | --- |
| **InnoDB 事务 + 行锁** | `sp_enroll` 内 `SELECT … FOR UPDATE` 锁住教学班一行，并发选课排队，**30 人同时抢 5 个名额恰好 5 人成功**（`tests/test_selection.py`） |
| **存储过程** | `sp_enroll` / `sp_drop` 选课退选（选课时段、必修资格、重复、时间冲突、满员 → 返回码）；`sp_generate_tasks` 由「培养方案 × 班级人数」一条 `INSERT … SELECT … ON DUPLICATE KEY UPDATE` 生成教学任务（幂等） |
| **触发器** | 先导课程不得成环（**递归 CTE**）、教师须有任课资格、教室同时间不得被占用、周次不超过教学日历、选课容量兜底、选课 / 退选自动维护已选人数并写流水 |
| **约束** | 外键（含 `(section_id, task_id)` 复合外键）、`CHECK`（学时>0、人数 ≤ 容量、账号全英文 `REGEXP_LIKE`……）、`ENUM`、唯一约束 |
| **生成列 + 唯一索引** | `section_teacher.lead_key` 虚拟生成列 + 唯一索引 = 「每个课头只能有一个主讲」（MySQL 没有“部分唯一索引”，用生成列实现） |
| **排序规则** | 账号主键用 `utf8mb4_0900_ai_ci`，`Admin` 与 `admin` 视为重复 |
| **视图** | 学生/班级/课头/课表/教学进程/统计，复杂 JOIN 只写一次，页面和报表复用 |
| **窗口函数** | `LAG() OVER (PARTITION BY … ORDER BY term_id)` 取上学期 / 去年同期，算同比环比；`RANK()` 教师工作量排名 |
| **递归 CTE** | 展开先导课程链、触发器里检测环 |
| **存储函数** | `fn_forecast_enroll` 在数据库内用 `COUNT/SUM` 聚合做一元线性回归，与 Python 实现交叉校验 |
| **聚合扩展** | `GROUP_CONCAT`、`WITH ROLLUP`、`INSERT … ON DUPLICATE KEY UPDATE` |

详细设计见 [`docs/数据库设计.md`](docs/数据库设计.md)。

## 测试

```bash
export MYSQL_USER=root MYSQL_PASSWORD=root          # 需要能创建 / 删除 course_selection_test_* 库
python -m unittest -v                               # 约 1 分钟；每个测试类自建一个临时库，用完删除
```

66 个测试覆盖：登录与角色权限矩阵、CSRF、改密码、用户管理规则；选课成功 / 满员 / 重复 / 时间冲突 / 时段 / 非本班必修；
**并发选课不超卖**；各触发器与约束；`sp_generate_tasks` 幂等且与独立重算一致；课头拆分规则；排课无冲突、同教室优先、就近换教室；
Python 与 MySQL 函数预测值一致；同比环比；`06_report_queries.sql` 每条语句可执行；各角色全站页面巡检无错误。
（MySQL 连不上时测试会自动跳过。）

## 已知取舍

- 学生的专业、入学年份、学院不重复存储，由行政班推导（视图 `v_student`），满足 3NF。
- 任选课人数预测只用「同一学期序号、历年」的数据（上学期与下学期的选课人数不可比），数据点少于 3 个时退化为两点外推 / 沿用上年。
- 学期、教学进程截止时间等业务日期以数据库里的学期表为准；演示数据的「当前时间」取初始化时刻。
- 开发服务器仅用于演示；部署请换 gunicorn / waitress，并设置环境变量 `SECRET_KEY`。
