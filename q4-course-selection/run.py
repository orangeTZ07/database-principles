"""启动入口（需要先准备好 MySQL 8.x，参见 README）：
    python run.py              # 首次运行自动建库、建表（视图/触发器/存储过程）并导入演示数据，然后启动服务
    python run.py --reset      # 删除旧库，重新建库并导入演示数据
    python run.py --no-seed    # 只建空库（只含管理员账号 admin / 123）
连接参数用环境变量设置：MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE
"""
import argparse

from werkzeug.security import generate_password_hash

from app import create_app, db


def init_database(cfg=None, seed=True, now=None):
    """重建数据库 → 执行 sql/*.sql → 导入演示数据（或仅创建 admin 账号）。"""
    conn = db.recreate_database(cfg)
    try:
        if seed:
            from app.seed import seed as run_seed
            run_seed(conn, now)
        else:
            conn.execute("INSERT INTO app_user(username, password_hash, role) VALUES ('admin', ?, 'admin')",
                         (generate_password_hash("123"),))
    finally:
        conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--reset", action="store_true", help="删除并重建数据库")
    ap.add_argument("--no-seed", action="store_true", help="不导入演示数据")
    ap.add_argument("--port", type=int, default=5000)
    args = ap.parse_args()
    app = create_app()
    cfg = app.config["DB"]
    if args.reset or not db.database_ready(cfg):
        print(f"初始化 MySQL 数据库 `{cfg['database']}`（{cfg['host']}:{cfg['port']}）...")
        init_database(cfg, seed=not args.no_seed)
        print("完成：初始密码均为 123，管理员账号 admin")
    app.run(host="127.0.0.1", port=args.port, debug=False)
