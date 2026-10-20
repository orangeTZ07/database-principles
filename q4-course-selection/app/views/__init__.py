def register_all(app):
    from . import academic, basic, main, plan, query, stats, student, users
    for m in (main, plan, basic, academic, student, query, stats, users):
        app.register_blueprint(m.bp)
