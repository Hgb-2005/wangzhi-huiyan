# -*- coding: utf-8 -*-
"""
routes/url_api.py —— 短链展开 + 链接检测接口
================================
前缀：/api/v1/url
    POST /api/v1/url/expand   短链递归展开（最大5层，识别循环跳转）
    POST /api/v1/url/detect   链接全套安全检测（核心接口）
"""
from flask import Blueprint, request

from config import MAX_URLS_PER_REQUEST, MAX_REDIRECTS
from utils import make_response, is_valid_url, normalize_url, extract_multiple_urls
from service.expand_service import expand_url
from service.detect_service import detect_url

url_bp = Blueprint("url", __name__, url_prefix="/api/v1/url")


@url_bp.route("/expand", methods=["POST"])
def expand():
    """
    短链递归展开接口
    请求：{"url": "https://t.cn/x9k2Lm"}
    响应：{original, final, chain, exceeded, hopCount}
    """
    data = request.get_json(silent=True) or {}
    url = (data.get("url") or "").strip()

    if not url:
        return make_response(400, msg="参数错误：缺少 url 字段")

    if not is_valid_url(url):
        return make_response(400, msg="识别到不是有效网址，请检查链接，确认有没有多打空格、缺https")

    try:
        result = expand_url(normalize_url(url))
        return make_response(200, result)
    except Exception as e:
        # 降级：解析失败不崩溃
        return make_response(200, {
            "original": url, "final": None, "chain": [], "exceeded": False,
            "hopCount": 0, "error": str(e)[:120],
            "note": "短链解析异常：%s" % str(e)[:80],
        })


@url_bp.route("/detect", methods=["POST"])
def detect():
    """
    链接全套安全检测核心接口
    请求：{"url": "...", "resolvedFinal": "...", "saveHistory": true}
    响应：五维检测结果 + 风险清单 + 四级风险等级
    """
    data = request.get_json(silent=True) or {}
    raw = data.get("url") or ""

    # 支持批量：url 可为字符串或数组，最多 5 条
    urls = extract_multiple_urls(raw)
    if not urls:
        return make_response(400, msg="识别到不是有效网址，请检查链接，确认有没有多打空格、缺https")

    if len(urls) > MAX_URLS_PER_REQUEST:
        return make_response(400, msg="单次最多支持同时检测5条链接，剩余链接请分批提交")

    resolved_final = data.get("resolvedFinal")
    save_history = data.get("saveHistory", True)
    source = data.get("source", "api")

    results = []
    for u in urls:
        try:
            r = detect_url(u, resolved_final=resolved_final if len(urls) == 1 else None,
                           save_history=save_history, source=source)
            results.append(r)
        except Exception as e:
            # 单条失败不影响整体，降级为 unknown
            results.append({
                "url": u, "level": "unknown", "label": "未知", "risks": [],
                "reason": "检测服务异常：%s" % str(e)[:80], "degradeNote": "服务异常",
            })

    return make_response(200, {
        "total": len(results),
        "results": results if len(results) > 1 else results[0],
    })