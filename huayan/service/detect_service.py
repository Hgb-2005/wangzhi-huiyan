# -*- coding: utf-8 -*-
"""
service/detect_service.py —— 核心检测服务
================================
实现接口文档约定的五维检测：
    ① URL本地特征（真实计算，任何模式都不 mock）
    ② 域名基础信息（Whois）
    ③ ICP备案信息
    ④ 第三方安全威胁标签
    ⑤ 页面元数据抓取

最终调用 utils.judge_risk_level 输出四级风险等级（后端固化，前端无权修改）。
★ 所有第三方失败一律降级为 unknown，绝不抛异常崩溃。
"""
import re
from urllib.parse import urlparse

import requests
from config import RUN_MODE, THIRD_PARTY, REQUEST_TIMEOUT, MAX_REDIRECTS
from utils import (local_url_features, judge_risk_level, check_ssl,
                   http_get_json, normalize_url)


# ============================================================
# 维度④：第三方安全威胁标签
# ============================================================
def _mock_threat(url: str) -> dict:
    """mock 模式：按域名特征返回与前端原型样例匹配的威胁标签"""
    host = (urlparse(url).hostname or "").lower()
    clean = {"phishing": False, "fraud": False, "malware": False,
             "gambling": False, "porn": False, "spam": False, "source": "mock"}
    if "0k.cn" in host or "security-check" in host:
        clean["phishing"] = True
        clean["source"] = "mock演示数据"
    elif "gambling" in host or "casino" in host:
        clean["gambling"] = True
        clean["source"] = "mock演示数据"
    elif "adult" in host or "porn" in host:
        clean["porn"] = True
        clean["source"] = "mock演示数据"
    elif "account-verify" in host or "demo-login" in host:
        clean["fraud"] = True
        clean["source"] = "mock演示数据"
    return clean


def _real_threat(url: str) -> dict:
    """real 模式：调用第三方威胁情报 API，失败返回 None 降级"""
    cfg = THIRD_PARTY["threat_intel"]
    if not cfg["enabled"] or not cfg["api_url"]:
        return None
    r = http_get_json(cfg["api_url"], params={"url": url},
                      headers={"Authorization": "Bearer " + cfg["api_key"]})
    if r.get("ok") and isinstance(r.get("data"), dict):
        d = r["data"]
        return {
            "phishing": bool(d.get("phishing", False)),
            "fraud": bool(d.get("fraud", False)),
            "malware": bool(d.get("malware", False)),
            "gambling": bool(d.get("gambling", False)),
            "porn": bool(d.get("porn", False)),
            "spam": bool(d.get("spam", False)),
            "source": "third_party_api",
        }
    return None


def get_threat_intel(url: str) -> dict:
    """维度④统一入口"""
    if RUN_MODE == "mock":
        return _mock_threat(url)
    t = _real_threat(url)
    if t is not None:
        return t
    # 真实模式下第三方不可用 → 返回全 False 但标记 unavailable
    # 注意：返回 None 会让判定层认为"无标记"，但不会降低已有风险等级
    return {"phishing": False, "fraud": False, "malware": False,
            "gambling": False, "porn": False, "spam": False,
            "source": "unavailable", "unavailable": True}


# ============================================================
# 维度⑤：页面元数据抓取
# ============================================================
def _mock_page_meta(url: str) -> dict:
    """mock 模式：按域名特征返回与前端原型一致的页面元数据"""
    host = (urlparse(url).hostname or "").lower()

    if "unreachable" in host:
        return None  # 演示无法访问 → 抓取失败

    if "0k.cn" in host:
        return {"title": "账户异常登录验证", "description": "检测到异常登录，请立即验证身份",
                "favicon": None, "autoPopup": True, "autoDownload": True,
                "fakeLoginForm": True, "source": "mock演示数据"}

    if "account-verify" in host or "demo-login" in host:
        return {"title": "账户安全验证", "description": "请登录以完成账户验证",
                "favicon": None, "autoPopup": True, "autoDownload": True,
                "fakeLoginForm": True, "source": "mock演示数据"}

    if "notebook" in host:
        return {"title": "DemoNote — 多人协作在线笔记",
                "description": "免费在线笔记与团队协作工具", "favicon": None,
                "autoPopup": False, "autoDownload": False, "fakeLoginForm": False,
                "source": "mock演示数据"}

    if "loop" in host:
        return {"title": "页面加载中", "description": "", "favicon": None,
                "autoPopup": False, "autoDownload": False, "fakeLoginForm": False,
                "source": "mock演示数据"}

    # 默认安全站
    title = host.split(".")[0].replace("-", " ").title() or "示例站点"
    return {"title": title, "description": "站点简介", "favicon": None,
            "autoPopup": False, "autoDownload": False, "fakeLoginForm": False,
            "source": "mock演示数据"}


def _real_page_meta(url: str) -> dict:
    """real 模式：真实抓取页面 HTML 解析元数据，被反爬拦截返回 None"""
    cfg = THIRD_PARTY["page_meta"]
    if not cfg["enabled"]:
        return None
    try:
        resp = requests.get(url, timeout=REQUEST_TIMEOUT,
                            headers={"User-Agent": "Mozilla/5.0 (compatible; HuayanBot/1.0)"},
                            allow_redirects=True, verify=True)
        if resp.status_code != 200:
            return None
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text[:200000]
    except Exception:
        # 超时/连接失败/被反爬拦截 → 统一降级
        return None

    def _pick(pattern):
        m = re.search(pattern, html, re.I | re.S)
        return re.sub(r"\s+", " ", m.group(1)).strip()[:200] if m else None

    title = _pick(r"<title[^>]*>(.*?)</title>")
    desc = _pick(r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"'](.*?)[\"']") \
        or _pick(r"<meta[^>]+content=[\"'](.*?)[\"'][^>]+name=[\"']description[\"']")
    favicon = _pick(r"<link[^>]+rel=[\"'][^\"']*icon[^\"']*[\"'][^>]+href=[\"'](.*?)[\"']")

    # 仿登录表单：页面同时含 password 输入框 和 表单提交行为
    has_pwd_input = "type=[\"']password[\"']" in html.lower()
    has_form = "<form" in html.lower()
    fake_login = has_pwd_input and has_form

    # ★ 自动下载：只认「页面强制下载**可执行文件**」的明确信号。
    #   大站首页普遍带 App 下载链接（<a href="xxx.apk">下载App</a>，用户主动点击），
    #   也有"下载原图"这类带 download 属性但只下图片的按钮——这些都不是"自动下载木马"。
    #   若用 .apk/.exe 全文匹配或"有 download 属性"就判高危，会把正规站误杀
    #   （历史 bug：切 real 后 music.163.com 因首页"下载原图"按钮被误判）。
    #   真正的自动下载 = download 强制属性 或 meta refresh，且目标指向可执行文件。
    _exe_ext = r"\.(apk|exe|msi|dmg|bat|cmd|scr|jar)"
    force_download_exe = bool(re.search(
        r"<a[^>]+\sdownload\b[^>]*href=[\"'][^\"']*" + _exe_ext,
        html, re.I))
    meta_refresh_exe = bool(re.search(
        r"<meta[^>]+http-equiv=[\"']?refresh[\"']?[^>]+url=[\"']?[^\"'>]*" + _exe_ext,
        html, re.I))
    auto_download = force_download_exe or meta_refresh_exe

    return {
        "title": title,
        "description": desc,
        "favicon": favicon,
        "autoPopup": "window.open" in html or "autoplay" in html.lower(),
        "autoDownload": auto_download,
        "fakeLoginForm": fake_login,
        "source": "live_crawl",
    }


def get_page_meta(url: str) -> dict:
    """维度⑤统一入口"""
    if RUN_MODE == "mock":
        return _mock_page_meta(url)
    return _real_page_meta(url)


# ============================================================
# 站点简介生成（AI 总结位；mock 模式用规则模板，real 模式可接 LLM）
# ============================================================
def build_site_summary(host: str, page_meta: dict, domain_info: dict) -> str:
    """根据页面元数据生成站点简介"""
    if not page_meta:
        return "页面抓取失败，暂无站点介绍"
    title = page_meta.get("title")
    desc = page_meta.get("description")
    if title and desc:
        return "【%s】%s（服务器属地：%s）" % (title, desc,
                                          (domain_info or {}).get("serverArea") or "未知")
    if title:
        return "【%s】该站点主要提供 %s 相关内容（服务器属地：%s）" % (
            title, host.split(".")[0], (domain_info or {}).get("serverArea") or "未知")
    if desc:
        return "%s（服务器属地：%s）" % (desc,
                                      (domain_info or {}).get("serverArea") or "未知")
    return "未能获取站点简介"


# ============================================================
# 主检测流程
# ============================================================
def detect_url(url: str, resolved_final: str = None,
               save_history: bool = True, source: str = "api") -> dict:
    """
    链接全套安全检测主入口。

    :param url: 原始提交链接
    :param resolved_final: 短链展开后的真实地址（若有则以它为检测目标）
    :param save_history: 是否写入历史记录
    :param source: 来源标记 api / recommend（推荐通道复测时标记为 recommend）
    :return: 严格对齐接口文档契约的检测结果 dict
    """
    from service.icp_whois_service import get_icp, get_whois
    from service.expand_service import expand_url

    original = normalize_url(url)

    # ============================================================
    # 【步骤0】短链递归展开 —— 优先解析跳转链路
    # 循环跳转时 final 为 None，此时保持用原始链接作为检测目标，
    # 避免后续维度因空值崩溃。
    # ============================================================
    expand_result = None
    try:
        if resolved_final:
            # 调用方已展开过，直接采用
            expand_result = {"original": original, "final": normalize_url(resolved_final),
                             "chain": [], "exceeded": False, "is_loop": False,
                             "hopCount": 0, "note": "调用方已提供展开结果"}
        else:
            expand_result = expand_url(original)
    except Exception as e:
        expand_result = {"original": original, "final": None, "chain": [],
                         "exceeded": False, "is_loop": False, "hopCount": 0,
                         "error": str(e)[:100], "note": "短链解析异常"}

    is_loop = bool(expand_result.get("is_loop") or expand_result.get("exceeded"))
    final_url = expand_result.get("final")
    # 循环跳转时 final 为 None，回退用原始链接继续检测
    target = normalize_url(final_url) if final_url else original
    expand_chain = expand_result.get("chain") or []

    # 无法访问判定：域名解析失败
    host = (urlparse(target).hostname or "").lower()
    if not host:
        return {
            "url": original, "finalUrl": target, "level": "unknown", "label": "未知",
            "localFeatures": {}, "domainInfo": {}, "icp": {},
            "threatIntel": {}, "pageMeta": None,
            "risks": ["链接格式无法解析"], "siteSummary": "无法解析该链接",
            "sitePurpose": "暂时无法获取页面信息，无法识别站点用途",
            "sslStatus": "无法获取", "degradeNote": None,
            "expand": {"chain": expand_chain, "finalUrl": target,
                       "isLoop": is_loop, "hopCount": expand_result.get("hopCount", 0),
                       "note": expand_result.get("note")},
        }

    # ---------- 维度①：URL本地特征（真实计算） ----------
    ssl_result = check_ssl(target) if RUN_MODE == "real" else None
    local = local_url_features(target, ssl_result)

    # ---------- 维度②：域名基础信息（联网实时查询） ----------
    # ★ 已从 mock 演示数据改为真实联网查询（DNS/RDAP/SSL/IP属地）。
    #   原实现对任何域名都返回同一套假数据：
    #   regDate=2016-03-22 / registrar="Demo Registrar Inc.（演示数据）"
    #   导致查不同网址看到的域名档案完全一样，且写着"演示数据"。
    #   现在每个域名独立查询，查询失败的字段为"查询失败"而非假数据。
    domain_info = None
    try:
        from service.domain_info_service import get_domain_profile
        domain_info = get_domain_profile(host)
    except Exception:
        domain_info = None

    # ---------- 维度③：ICP备案 ----------
    icp = get_icp(host)

    # ---------- 维度④：第三方威胁标签 ----------
    threat = get_threat_intel(target)

    # ---------- 维度⑤：页面元数据 ----------
    page_meta = get_page_meta(target)

    # ---------- 风险等级判定（后端固化） ----------
    judgment = judge_risk_level(local, domain_info, icp, threat, page_meta)

    # 域名解析失败 → 强制 unknown。
    # 注意：domain_info 现在总返回 dict（字段失败时是"查询失败"），
    # 所以不能只看 domain_info is None，还要看 resolvable 标记——
    # 否则 DNS 失败的域名会因为"有 domain 字段"而漏过这个分支，
    # 被 judge_risk_level 误判成 safe（历史回归 bug）。
    if domain_info is None or domain_info.get("resolvable") is False:
        judgment = {"level": "unknown", "label": "未知",
                    "reason": "域名解析失败或无法获取基础信息",
                    "risks": ["域名解析失败 / 服务器无响应 / 页面无法访问，缺少足够研判数据"]}

    # ============================================================
    # 循环跳转风险注入
    # ★ 只允许升风险，绝不覆盖已有判定结果。
    #   已判 high危/danger → 维持原级，仅补充线索
    #   已判 unknown → 仍维持 unknown（数据不足，不能凭跳转链断定恶意）
    #   判为 safe/suspicious → 至少升到 suspicious
    # ============================================================
    if is_loop:
        hop_desc = "超过 %d 层上限" % MAX_REDIRECTS \
            if (expand_result.get("hopCount") or 0) > MAX_REDIRECTS \
            else "检测到重复跳转地址"
        loop_risk = "循环跳转告警：链接跳转%s，判定为循环跳转。正规业务场景极少出现循环跳转，存在较高可疑风险。" % hop_desc
        if loop_risk not in judgment["risks"]:
            judgment["risks"].insert(0, loop_risk)
        if judgment["level"] in ("safe", "suspicious"):
            judgment["level"] = "suspicious"
            judgment["label"] = "可疑"
            judgment["reason"] = "存在循环跳转风险"

    # 页面抓取失败 → 标注降级提示
    degrade_note = None
    if page_meta is None:
        degrade_note = "页面抓取失败，暂无站点介绍"
    if threat and threat.get("unavailable"):
        degrade_note = (degrade_note + "；" if degrade_note else "") + \
            "当前安全威胁库服务不可用，仅做基础特征筛查，识别能力大幅下降"
    if icp and icp.get("unavailable"):
        degrade_note = (degrade_note + "；" if degrade_note else "") + \
            "ICP备案查询服务不可用"

    site_summary = build_site_summary(host, page_meta, domain_info)

    # ============================================================
    # 站点用途识别（大模型总结，独立于风险判定）
    # ------------------------------------------------------------
    # 拿到网页 title/meta/正文后，交给大模型一句话说清"这网站是干嘛的"。
    # ★ 高危链接也照常识别用途（风险结论优先级更高，由前端红标提醒）。
    # ★ 独立 try/except：识别失败只影响这一个字段，绝不拖垮主检测流程。
    # ============================================================
    site_purpose = None
    try:
        from service.site_purpose import identify_site_purpose
        site_purpose = identify_site_purpose(target)
    except Exception:
        site_purpose = None
    if not site_purpose:
        site_purpose = "暂时无法获取页面信息，无法识别站点用途"

    # ★ 实时 SSL 证书信息并入 local，供前端"SSL 证书"行展示。
    #   原来是 local_url_features 自己给的粗判（仅看 URL 是不是 https），
    #   现在用真实证书的颁发方与有效性（domain_info_service 实测所得）
    if domain_info and domain_info.get("ssl"):
        local["sslStatus"] = domain_info["ssl"]
        local["sslDetail"] = domain_info.get("sslDetail") or {}

    result = {
        "url": original,
        "finalUrl": target,
        "domain": host,
        "level": judgment["level"],
        "label": judgment["label"],
        "reason": judgment["reason"],
        # ---- 短链跳转链路（本次新增，供报告单"完整跳转链路"区展示）----
        "expand": {
            "chain": expand_chain,
            "finalUrl": final_url,
            "isLoop": is_loop,
            "hopCount": expand_result.get("hopCount", len(expand_chain)),
            "note": expand_result.get("note"),
            "error": expand_result.get("error"),
        },
        # 顶层冗余一份，方便前端直接取用
        "expandChain": expand_chain,
        "isLoop": is_loop,
        "localFeatures": local,
        "domainInfo": domain_info or {},
        "icp": icp,
        "threatIntel": threat or {},
        "pageMeta": page_meta,
        "risks": judgment["risks"],
        "riskCount": len(judgment["risks"]),
        "siteSummary": site_summary,
        "sitePurpose": site_purpose,
        "sslStatus": local.get("sslStatus"),
        "degradeNote": degrade_note,
        "runMode": RUN_MODE,
        "source": source,
    }

    # ---------- 写入历史记录 ----------
    if save_history:
        try:
            from models import db, DetectHistory
            db.session.add(DetectHistory(
                original_url=original[:1024],
                final_url=target[:1024],
                domain=host[:255],
                risk_level=judgment["level"],
                risk_label=judgment["label"],
                risk_count=len(judgment["risks"]),
                is_overseas=bool((domain_info or {}).get("isOverseas")) if domain_info else False,
                icp_subject=(icp or {}).get("subjectName"),
                expand_hops=expand_result.get("hopCount", len(expand_chain)),
                source=source,
            ))
            db.session.commit()
        except Exception:
            # 历史记录写入失败不影响主流程
            try:
                db.session.rollback()
            except Exception:
                pass

    return result