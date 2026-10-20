import os

from flask import Flask

from . import auth, db
from .util import PERIOD_TIMES, WEEKDAYS


def create_app(config=None):
    app = Flask(__name__)
    app.jinja_env.globals.update(WEEKDAYS=WEEKDAYS, PERIOD_TIMES=PERIOD_TIMES)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
        DB=db.default_config(),   # MySQL 连接参数，可用环境变量 MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE 覆盖
        NOW=None,  # 测试时可注入当前时间函数
    )
    if config:
        app.config.update(config)
    db.init_app(app)
    auth.init_app(app)
    from .views import register_all
    register_all(app)
    return app
