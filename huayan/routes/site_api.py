# -*- coding: utf-8 -*-
"""
routes/site_api.py —— 可信站点检索推荐接口
================================
前缀：/api/v1/site
    POST /api/v1/site/search   站点检索推荐（含强制批量实时复测）
    POST /api/v1/site/add      手动添加站点到本地库
"""
from flask import Blueprint, request

from config import RECOMMEND_LIMIT
from utils import make_response
from service.site_service import search_sites, add_site

site_bp = Blueprint("site", __name__, url_prefix="/api/v1/site")


@site_bp.route("/search", methods=["POST"])
def search():
    """
    站点检索推荐接口
    请求：{
        "keyword": "流程图",
        "constraints": {"free": true, "noAd": true, "online": true},
        "limit": 3
    }
    响应：{total, sites[], note}
    ★ sites 中每个站点都带 liveLevel（本次实时复测结果）
    """
    data = request.get_json(silent=True) or {}
    keyword = (data.get("keyword") or data.get("query") or "").strip()
    constraints = data.get("constraints") or {}
    limit = data.get("limit") or RECOMMEND_LIMIT

    if not keyword:
        return make_response(400, msg="参数错误：缺少 keyword 字段")

    if not isinstance(limit, int) or limit < 1:
        limit = RECOMMEND_LIMIT
    # 规格要求：默认推荐5个，最多不超过5个
    limit = min(limit, 5)

    try:
        result = search_sites(keyword, constraints, limit)
    except Exception as e:
        return make_response(500, msg="站点检索服务异常：%s" % str(e)[:100])

    # 命中违禁词 → 固定拒绝话术
    if result.get("prohibited"):
        p = result["prohibited"]
        return make_response(200, {
            "refused": True,
            "category": p["category"],
            "keyword": p["keyword"],
            "msg": "很抱歉，我无法为你提供该类站点推荐，该需求不符合规范，请更换合法合规需求。",
            "total": 0,
            "sites": [],
        })

    # ★ 联网检索失败（网络不通/无候选）→ 明确告知前端，
    #   绝不返回演示假域名糊弄用户
    if result.get("search_failed"):
        return make_response(200, {
            "searchFailed": True,
            "total": 0,
            "sites": [],
            "msg": result.get("msg") or "联网检索失败，暂时无法推荐站点",
        })

    if not result["sites"]:
        return make_response(200, {
            "total": 0, "sites": [],
            "msg": result.get("msg") or "站点库暂时没有匹配你需求的网站，你可以尝试修改描述关键词",
        })

    return make_response(200, {
        "total": result["total"],
        "sites": result["sites"],
        "note": result.get("note"),
        "recheckCount": result.get("recheckCount", 0),
    })


@site_bp.route("/add", methods=["POST"])
def add():
    """
    手动添加站点到本地库（管理员入口）。
    请求：{
        "name": "网站名称",
        "url": "https://...",
        "tags": "标签1,标签2",
        "summary": "一句话备注",
        "category": "分类"
    }
    响应：{ok, duplicated, site} 或 {ok:False, msg:拒绝原因}
    ★ 入库前严格校验：URL格式 + 违禁词 + 合规域名 + 连通性 + 风险检测，
      高危/盗版/违规直接拒绝。
    """
    data = request.get_json(silent=True) or {}
    try:
        result = add_site(
            name=data.get("name"),
            url=data.get("url"),
            tags=data.get("tags"),
            summary=data.get("summary"),
            category=data.get("category"),
        )
    except Exception as e:
        return make_response(500, msg="添加站点异常：%s" % str(e)[:100])

    if result.get("ok"):
        return make_response(200, {
            "ok": True,
            "duplicated": result.get("duplicated", False),
            "site": result.get("site"),
        })
    return make_response(200, {"ok": False, "msg": result.get("msg") or "添加失败"})