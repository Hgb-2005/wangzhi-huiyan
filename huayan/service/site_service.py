# -*- coding: utf-8 -*-
"""
service/site_service.py —— 可信站点检索 + 批量复测
================================
业务规则：
  1. 检索前先过内置违禁词库，命中即拒绝，不返回任何资源
  2. **站点来源为联网实时检索**（service/web_search.py）：
     正规站点知识库 + 搜索引擎，每个候选都做 DNS/HTTP 实连校验，
     打不开的直接剔除
  3. 高危站点直接过滤剔除
  4. ★强制实时复测：每次推荐前都重跑一遍通道A 检测

★ 变更记录
----------
原实现从本地 SQLite 的 WebsiteLib 预置演示数据检索，返回的是
demo-trans.sub 这类**不存在的假域名**（DNS_PROBE_FINISHED_NXDOMAIN），
用户点开全是死链；同时存在"匹配不到就逐字拆词 + 默认站兜底"的逻辑，
会把「音乐」拆成单字「音」，进而把"字幕转换"站点当成音乐站点推荐。
现已全部移除：不再检索本地演示库、不再有演示数据兜底，
联网检索失败时明确返回 search_failed，由前端展示失败提示。
"""
from config import RECOMMEND_LIMIT

# 违禁类别与关键词（与前端 engine.js 内置词库保持一致，双端校验）
PROHIBITED = {
    "盗版影视": ["盗版", "未删减", "片库", "免费看大片全集"],
    "破解软件": ["破解", "crack", "绿色版", "免授权", "激活工具", "去广告破解"],
    "外挂": ["外挂", "辅助器", "自动瞄准", "游戏作弊"],
    "翻墙": ["翻墙", "梯子", "机场节点", "代理工具", "科学上网"],
    "赌博": ["赌博", "博彩", "百家乐", "澳门赌场", "彩票预测"],
    "色情": ["色情", "黄色网站", "成人视频", "情色"],
    "钓鱼工具": ["钓鱼网站", "钓鱼链接生成", "盗号", "盗取密码"],
    "虚假证件": ["假证", "办假证", "代开发票", "假身份证", "假学历"],
    "刷单": ["刷单", "刷好评", "刷信誉", "代刷"],
    "隐私爬虫": ["爬取隐私", "手机号查询", "身份证信息查询", "户籍查询", "开盒"],
    "盗号工具": ["盗号", "撞库", "密码字典", "社工库"],
    "网盘非法资源": ["网盘资源", "磁力链接", "BT种子下载", "内网资源泄露"],
}

# 说明：原"头部大站黑名单 HEAD_SITES"与"不优先推荐大站"的规则已移除。
# 联网检索返回的是**用户需求对应的正规服务站点**（QQ音乐/起点中文网/
# 百度翻译等），这些站点本身就是大厂主体，"不推荐大站"的规则会让
# 推荐结果退回到不知名的小站，与"优先正规可靠站点"的目标相悖。
# 现行的优先级改为：联网检索相关度 > 可访问性实测通过 > 非高危。


def check_prohibited(text: str) -> dict:
    """违禁词库校验。命中返回 {"hit":True,"category":..}，否则 hit=False"""
    lower = (text or "").lower()
    for category, keys in PROHIBITED.items():
        for k in keys:
            if k.lower() in lower:
                return {"hit": True, "category": category, "keyword": k}
    return {"hit": False}


def search_sites(keyword: str, constraints: dict = None,
                 limit: int = None) -> dict:
    """
    站点检索推荐主入口。

    :param keyword: 用户需求关键词
    :param constraints: 约束条件 {free, noAd, online}
    :param limit: 返回数量，默认取 config.RECOMMEND_LIMIT
    :return: {prohibited, total, sites, note} 或 {search_failed: True, ...}

    ★ 数据来源已从"本地预置演示数据"改为**联网实时检索**
      （见 service/web_search.py）。原实现返回的是 demo-trans.sub 这类
      根本不存在的假域名，浏览器报 DNS_PROBE_FINISHED_NXDOMAIN，用户
      点开全是死链。现在改为：
        - 正规站点知识库 + 搜索引擎实时检索 产生候选
        - 每个候选都做 DNS 解析 + HTTP 实连，打不开的直接剔除
        - 联网检索彻底失败时返回 search_failed，**不再兜底塞假域名**，
          由前端展示"联网检索失败，暂时无法推荐站点"
    """
    constraints = constraints or {}
    limit = limit or RECOMMEND_LIMIT

    # ---------- 步骤1：违禁词库初审 ----------
    verdict = check_prohibited(keyword)
    if verdict["hit"]:
        return {"prohibited": verdict, "total": 0, "sites": [], "note": None}

    # ---------- 步骤2：联网实时检索（通道B 核心能力） ----------
    from service.web_search import search_recommend_sites
    try:
        found = search_recommend_sites(keyword, limit=limit)
    except Exception as e:
        # 联网检索本身异常也要降级成"检索失败"，不能崩、也不能塞假域名
        return {"prohibited": None, "total": 0, "sites": [],
                "note": None, "search_failed": True,
                "msg": "联网检索失败，暂时无法推荐站点（%s）" % str(e)[:60]}

    if not found.get("ok"):
        # 检索失败：一条都不返回，绝不用演示数据兜底
        return {"prohibited": None, "total": 0, "sites": [], "note": None,
                "search_failed": True,
                "msg": found.get("msg") or "联网检索失败，暂时无法推荐站点"}

    sites = found["sites"]

    # ---------- 步骤3：对真实站点跑一次通道A 检测（复用既有能力） ----------
    # 注意：这里只做"风险复测"，不改变 detect_service 的任何逻辑
    from service.detect_service import detect_url
    results = []
    recheck_count = 0

    for item in sites:
        if len(results) >= limit:
            break
        try:
            live = detect_url(item["url"], save_history=False, source="recommend")
            recheck_count += 1

            # ★ 复测后变成高危的站点直接过滤，不展示
            if live["level"] == "danger":
                continue

            row = dict(item)
            row["id"] = item.get("id") or ("web-%d" % (item.get("_idx", 0)))
            row["liveLevel"] = live["level"]
            row["liveLabel"] = live["label"]
            row["liveRisks"] = live["risks"]
            row["rechecked"] = True
            results.append(row)
        except Exception:
            # 单站点复测异常：跳过该站，而不是整条链路失败
            continue

    note = None
    if len(results) < limit:
        note = "符合条件的站点较少，仅找到 %d 个" % len(results)

    return {"prohibited": None, "total": len(results), "sites": results,
            "note": note, "recheckCount": recheck_count}


def add_site(name, url, tags="", summary="", category=""):
    """
    手动添加站点到本地库（管理员入口）。

    入库前校验（严格，任何一项不通过都拒绝）：
      1. 必填：名称 + 网址
      2. URL 合法（http/https，域名格式正确）
      3. 违禁词校验（名称/标签/备注里不得含违规词）
      4. 合规域名（搜索引擎/UGC/灰色 TLD 直接拒绝）
      5. 连通性实测（DNS + HTTP，打不开的拒绝）
      6. 风险检测（detect_url 复测，高危直接拒绝）

    :return: {"ok": True, "site": {...}} 或 {"ok": False, "msg": "拒绝原因"}
    """
    from models import db, WebsiteLib
    from service.web_search import (probe_site, is_blocked_domain,
                                    _host_of, _normalize_url)

    name = (name or "").strip()
    url = _normalize_url(url or "")
    tags = (tags or "").strip()
    summary = (summary or "").strip()
    category = (category or "").strip()

    # 1. 必填
    if not name:
        return {"ok": False, "msg": "网站名称不能为空"}
    if not url or not url.startswith(("http://", "https://")):
        return {"ok": False, "msg": "网址格式不正确，需以 http:// 或 https:// 开头"}

    host = _host_of(url)
    if not host:
        return {"ok": False, "msg": "网址无法解析出有效域名"}

    # 2. 违禁词校验（名称/标签/备注一起查）
    for field, text in (("名称", name), ("标签", tags), ("备注", summary)):
        v = check_prohibited(text)
        if v["hit"]:
            return {"ok": False,
                    "msg": "【%s】含违规词「%s」，不允许收录" % (field, v["keyword"])}

    # 3. 合规域名
    if is_blocked_domain(host, name):
        return {"ok": False, "msg": "该域名属于境外/灰色/讨论站，不符合收录标准"}

    # 4. 连通性实测
    ok, final_url, _level = probe_site(url)
    if not ok:
        return {"ok": False, "msg": "该网址无法访问（DNS 解析失败或页面打不开），不予收录"}

    # 5. 风险检测（高危直接拒绝）
    try:
        from service.detect_service import detect_url
        live = detect_url(final_url, save_history=False, source="manual_add")
        if live.get("level") == "danger":
            return {"ok": False,
                    "msg": "该站点被检测为高危（%s），拒绝收录" % (live.get("reason") or "命中恶意特征")}
    except Exception as e:
        # 风险检测异常：为安全起见拒绝（宁可少收，不收危险的）
        return {"ok": False, "msg": "风险检测服务异常，暂不收录（%s）" % str(e)[:50]}

    # 6. 入库（url 去重）
    try:
        exists = WebsiteLib.query.filter(WebsiteLib.url == final_url).first()
        if exists:
            # 已存在则更新其标签/备注/名称
            exists.name = name[:128]
            exists.tags = tags[:255] or exists.tags
            exists.summary = summary[:512] or exists.summary
            exists.category = category[:64] or exists.category
            exists.history_level = live.get("level") or "safe"
            db.session.commit()
            return {"ok": True, "duplicated": True, "site": exists.to_dict()}

        row = WebsiteLib(
            name=name[:128], url=final_url[:512],
            category=category[:64] or "自定义",
            tags=tags[:255] or "自定义",
            summary=summary[:512] or "管理员手动添加",
            scene="",
            is_free=True, has_ad=False,
            history_level=live.get("level") or "safe",
        )
        db.session.add(row)
        db.session.commit()
        return {"ok": True, "site": row.to_dict()}
    except Exception as e:
        db.session.rollback()
        return {"ok": False, "msg": "入库失败：%s" % str(e)[:60]}