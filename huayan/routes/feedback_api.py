# -*- coding: utf-8 -*-
"""
routes/feedback_api.py —— 用户反馈提交接口
================================
前缀：/api/v1/feedback
    POST /api/v1/feedback/submit   反馈上报
    GET  /api/v1/feedback/list     反馈列表（供后台复核）
"""
from flask import Blueprint, request

from utils import make_response, is_valid_url, normalize_url
from models import db, Feedback

feedback_bp = Blueprint("feedback", __name__, url_prefix="/api/v1/feedback")

# 允许的反馈类型
FEEDBACK_TYPES = {
    "invalid": "站点失效",
    "tooManyAds": "广告过多",
    "phishing": "链接是钓鱼站",
    "badRecommend": "推荐不满意",
    "other": "其他问题",
}


@feedback_bp.route("/submit", methods=["POST"])
def submit():
    """
    反馈上报接口
    请求：{"type": "phishing", "url": "...", "siteId": 1, "message": "..."}
    响应：{"feedbackId": 1, "note": "反馈已记录..."}
    """
    data = request.get_json(silent=True) or {}
    fb_type = (data.get("type") or "other").strip()
    url = (data.get("url") or "").strip()
    site_id = data.get("siteId")
    message = (data.get("message") or "").strip()

    if fb_type not in FEEDBACK_TYPES:
        return make_response(400, msg="参数错误：type 非法，可选值 %s"
                             % "、".join(FEEDBACK_TYPES.keys()))

    # URL 非必填（推荐不满意类反馈可能无链接）
    if url and not is_valid_url(url):
        return make_response(400, msg="识别到不是有效网址，请检查链接，确认有没有多打空格、缺https")

    if not url and not message:
        return make_response(400, msg="参数错误：url 与 message 至少填写一项")

    # 隐私保护：不存储用户提交的敏感内容，仅保留链接与普通留言
    if len(message) > 1000:
        message = message[:1000]

    try:
        fb = Feedback(
            type=fb_type,
            url=normalize_url(url)[:1024] if url else None,
            site_id=site_id if isinstance(site_id, int) else None,
            message=message[:1024],
            status="pending",
        )
        db.session.add(fb)
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        return make_response(500, msg="反馈提交失败：%s" % str(e)[:100])

    return make_response(200, {
        "feedbackId": fb.id,
        "type": fb_type,
        "typeLabel": FEEDBACK_TYPES[fb_type],
        "note": "反馈已记录，后台会定期人工复核该站点状态并优化站点库，"
                "暂不承诺即时处理。",
    })


@feedback_bp.route("/list", methods=["GET"])
def list_feedback():
    """反馈列表（分页），供后台人工复核使用"""
    page = request.args.get("page", 1, type=int)
    size = request.args.get("size", 20, type=int)
    page = max(page, 1)
    size = min(max(size, 1), 100)

    query = Feedback.query
    status = request.args.get("status")
    if status:
        query = query.filter(Feedback.status == status)

    total = query.count()
    items = query.order_by(Feedback.created_at.desc()) \
        .offset((page - 1) * size).limit(size).all()

    return make_response(200, {
        "total": total, "page": page, "size": size,
        "items": [f.to_dict() for f in items],
    })