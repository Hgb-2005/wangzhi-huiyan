# -*- coding: utf-8 -*-
"""
routes/history_api.py —— 检测历史记录 + 白名单接口
================================
前缀：/api/v1
    GET  /api/v1/history/list       分页查询检测历史
    POST /api/v1/history/whitelist   添加白名单（可选扩展）
    GET  /api/v1/history/whitelist   查询白名单（可选扩展）
"""
from flask import Blueprint, request
from urllib.parse import urlparse

from utils import make_response, is_valid_url, normalize_url
from models import db, DetectHistory, Whitelist

history_bp = Blueprint("history", __name__, url_prefix="/api/v1")


@history_bp.route("/history/list", methods=["GET"])
def history_list():
    """
    分页查询历史检测记录
    参数：page, size, level(按风险等级筛选), domain, source
    """
    page = request.args.get("page", 1, type=int)
    size = request.args.get("size", 20, type=int)
    page = max(page, 1)
    size = min(max(size, 1), 100)

    query = DetectHistory.query

    level = request.args.get("level")
    if level:
        query = query.filter(DetectHistory.risk_level == level)

    domain = request.args.get("domain")
    if domain:
        query = query.filter(DetectHistory.domain.like("%" + domain + "%"))

    source = request.args.get("source")
    if source:
        query = query.filter(DetectHistory.source == source)

    total = query.count()
    items = query.order_by(DetectHistory.created_at.desc()) \
        .offset((page - 1) * size).limit(size).all()

    return make_response(200, {
        "total": total,
        "page": page,
        "size": size,
        "items": [h.to_dict() for h in items],
    })


# ============================================================
# 白名单（可选扩展功能）
# ============================================================
@history_bp.route("/history/whitelist", methods=["POST"])
def whitelist_add():
    """添加 URL 到白名单"""
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()
    note = (data.get("note") or "").strip()[:512]

    if not url:
        return make_response(400, msg="参数错误：缺少 url 字段")
    if not is_valid_url(url):
        return make_response(400, msg="识别到不是有效网址，请检查链接，确认有没有多打空格、缺https")

    url = normalize_url(url)
    if Whitelist.query.filter_by(url=url).first():
        return make_response(400, msg="该链接已存在于白名单中")

    host = (urlparse(url).hostname or "").lower()
    try:
        wl = Whitelist(url=url[:1024], domain=host[:255], note=note)
        db.session.add(wl)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return make_response(500, msg="白名单添加失败：%s" % str(e)[:100])

    return make_response(200, {"id": wl.id, "url": wl.url, "note": wl.note})


@history_bp.route("/history/whitelist", methods=["GET"])
def whitelist_list():
    """查询白名单列表"""
    page = request.args.get("page", 1, type=int)
    size = request.args.get("size", 50, type=int)
    page = max(page, 1)
    size = min(max(size, 1), 100)

    total = Whitelist.query.count()
    items = Whitelist.query.order_by(Whitelist.created_at.desc()) \
        .offset((page - 1) * size).limit(size).all()

    return make_response(200, {
        "total": total, "page": page, "size": size,
        "items": [w.to_dict() for w in items],
    })


# ============================================================
# 检测历史记录：删除单条 / 清空全部
# （查询列表接口已在文件开头的 /history/list）
# ============================================================

@history_bp.route("/history/<int:record_id>", methods=["DELETE"])
def history_delete(record_id):
    """
    删除单条检测历史记录
    前端调用：DELETE /api/v1/history/12
    返回：{"code":200,"msg":"已删除该条记录","data":{"deletedId":12}}
    """
    record = DetectHistory.query.get(record_id)
    if record is None:
        # 记录不存在时给出友好提示，而不是抛 404 页面
        return make_response(404, msg="这条记录不存在，可能已经被删除了")

    try:
        db.session.delete(record)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return make_response(500, msg="删除失败：%s" % str(e)[:100])

    return make_response(200, {"deletedId": record_id})


@history_bp.route("/history/clear", methods=["POST", "DELETE"])
def history_clear():
    """
    一键清空全部检测历史
    前端调用：POST /api/v1/history/clear
    返回：{"code":200,"msg":"已清空全部历史记录","data":{"deletedCount":42}}
    """
    try:
        count = DetectHistory.query.count()
        # 按批量方式删除，比逐条 delete 快
        DetectHistory.query.delete()
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return make_response(500, msg="清空失败：%s" % str(e)[:100])

    return make_response(200, {"deletedCount": count})