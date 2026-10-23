# -*- coding: utf-8 -*-
"""
app.py —— Flask 主入口
================================
启动方式：
    python app.py

启动后：
    - 自动建表（SQLite，无需手动导入 SQL）
    - 自动写入站点库预置示范数据
    - 挂载 4 个路由蓝图，全局前缀 /api/v1
    - 开启 CORS（允许前端原型 8899 端口跨源访问）
    - 启用单 IP 每分钟 60 次限流
"""
import logging
import os

from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS

from config import (SQLALCHEMY_DATABASE_URI, CORS_ORIGINS, RATE_LIMIT_PER_MINUTE,
                    RUN_MODE, HOST, PORT, DEBUG)
from models import db, init_db
from utils import make_response, RateLimiter

# 前端静态资源目录（index.html / engine.js / diagnose.html）
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# 中文日志格式
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

# 全局限流器
limiter = RateLimiter(RATE_LIMIT_PER_MINUTE)


def create_app():
    app = Flask(__name__)
    app.config["SQLALCHEMY_DATABASE_URI"] = SQLALCHEMY_DATABASE_URI
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["JSON_AS_ASCII"] = False
    app.json.ensure_ascii = False

    db.init_app(app)

    # ---------- CORS 跨域 ----------
    # 允许前端 html 原型跨源调用本后端
    CORS(app, resources={r"/api/*": {"origins": CORS_ORIGINS}},
         supports_credentials=True,
         expose_headers=["Content-Type"])

    # ---------- 限流中间件 ----------
    @app.before_request
    def rate_limit():
        # 只对 /api 接口限流
        if not request.path.startswith("/api/"):
            return None
        ip = request.headers.get("X-Forwarded-For", request.remote_addr or "unknown")
        ip = ip.split(",")[0].strip()
        if not limiter.allow(ip):
            resp = jsonify(make_response(429))
            resp.status_code = 200   # 业务码在 JSON 的 code 字段中体现
            return resp
        return None

    # ---------- 注册路由蓝图 ----------
    from routes.url_api import url_bp
    from routes.site_api import site_bp
    from routes.feedback_api import feedback_bp
    from routes.history_api import history_bp

    app.register_blueprint(url_bp)
    app.register_blueprint(site_bp)
    app.register_blueprint(feedback_bp)
    app.register_blueprint(history_bp)

    # ---------- 前端静态页面（前后端合并为单端口服务） ----------
    # 线上部署时，前端 index.html / engine.js / diagnose.html 由本后端直接
    # serve，与 /api/v1 同源，前端用相对路径请求接口，无需跨域。
    @app.route("/", methods=["GET"])
    def index_page():
        return send_from_directory(STATIC_DIR, "index.html")

    @app.route("/diagnose.html", methods=["GET"])
    def diagnose_page():
        return send_from_directory(STATIC_DIR, "diagnose.html")

    @app.route("/engine.js", methods=["GET"])
    def engine_js():
        return send_from_directory(STATIC_DIR, "engine.js")

    # ---------- 全局错误处理（任何异常都不崩溃，返回统一格式） ----------
    @app.errorhandler(Exception)
    def handle_error(e):
        app.logger.exception("未捕获异常: %s", e)
        return jsonify(make_response(500, msg="服务异常：%s" % str(e)[:150]))

    @app.errorhandler(404)
    def handle_404(e):
        return jsonify(make_response(404, msg="接口不存在"))

    # ---------- 健康检查 ----------
    @app.route("/api/v1/health", methods=["GET"])
    def health():
        return jsonify(make_response(200, {
            "status": "ok",
            "runMode": RUN_MODE,
            "rateLimit": RATE_LIMIT_PER_MINUTE,
            "endpoints": [
                "POST /api/v1/url/expand",
                "POST /api/v1/url/detect",
                "POST /api/v1/site/search",
                "POST /api/v1/feedback/submit",
                "GET  /api/v1/history/list",
                "POST /api/v1/history/whitelist",
                "GET  /api/v1/history/whitelist",
            ],
        }))

    # ---------- 启动时自动建表 + 预置站点数据 ----------
    with app.app_context():
        init_db(app)
        # 本地站点库为空时，把真实站点（CURATED_SITES）同步进 WebsiteLib
        from service.web_search import ensure_local_lib
        ensure_local_lib()

    return app


app = create_app()


if __name__ == "__main__":
    print("=" * 60)
    print("  网址慧眼 Flask 后端")
    print("  运行模式：%s" % ("mock 调试模式（后端返回模拟数据）"
                        if RUN_MODE == "mock" else "real 真实 API 模式"))
    print("  服务地址：http://%s:%d" % (HOST, PORT))
    print("  接口前缀：http://%s:%d/api/v1" % (HOST, PORT))
    print("  限流：单 IP 每分钟 %d 次" % RATE_LIMIT_PER_MINUTE)
    print("  健康检查：http://%s:%d/api/v1/health" % (HOST, PORT))
    print("=" * 60)
    app.run(host=HOST, port=PORT, debug=DEBUG)