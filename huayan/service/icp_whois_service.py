# -*- coding: utf-8 -*-
"""
service/icp_whois_service.py —— ICP备案查询封装
================================
★ 变更记录（域名档案真实化）
------------------------------
原实现有两套 mock 演示数据，导致查任何域名看到的备案/注册信息都一样：
    备案：  「某某科技有限公司（演示数据）」 / 审核日期 2016-05-10
    whois： 2016-03-22 / Demo Registrar Inc. / 203.0.113.24
现已全部移除。

现状：
  - **域名档案（注册商/注册时间/到期时间/IP/属地/SSL）**
    走 service/domain_info_service.py 的联网实时查询（RDAP + DNS + SSL），
    每个域名独立查询、结果互不相同。本文件仅保留 get_whois 作为兼容入口。
  - **ICP备案**：工信部接口需 token 且有反爬，未配置第三方 API 时
    如实返回"查询失败"，**不再编造备案主体**。境外域名仍标注
    "境外域名，无ICP备案数据"（这是事实判断，非假数据）。

★ 任何第三方服务失败都不抛出异常，由调用方降级为 unknown。
"""
from urllib.parse import urlparse

from config import RUN_MODE, THIRD_PARTY

# 确定属于境外注册局的顶级后缀 —— 这些域名不会有 ICP 备案。
# ⚠️ 这里是**顶级后缀**（不带前缀），如 de / jp / co.uk。
#    注意 "in"（印度）、"is"（冰岛）这类两字母后缀，
#    不要把整个二级后缀（如 "qq.com"）拿来比对。
OVERSEAS_TLDS = {
    # 欧洲
    "de", "fr", "nl", "it", "es", "pt", "be", "at", "ch", "se", "no",
    "dk", "fi", "pl", "cz", "sk", "hu", "ro", "bg", "gr", "ie", "lu",
    "is", "li", "mc", "sm", "ad", "va", "eu",
    # 美洲
    "us", "ca", "mx", "br", "ar", "cl", "co", "pe", "ve", "uy", "ec",
    "bo", "py", "cu", "do", "gt", "pa", "pr", "vi",
    # 亚太
    "jp", "kr", "tw", "hk", "sg", "my", "th", "vn", "id", "ph", "mn",
    "kh", "la", "bn", "mm", "au", "nz", "fj", "pg", "nc", "pf", "ws",
    # 中东非洲
    "ae", "sa", "qa", "kw", "bh", "om", "jo", "lb", "il", "ir", "iq",
    "tr", "eg", "za", "ng", "ke", "gh", "ma", "dz", "tn", "ly", "sd",
    # 多国二级后缀
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.jp", "or.jp", "ne.jp",
    "co.kr", "com.au", "net.au", "org.au", "com.br", "com.sg", "com.my",
    "co.in", "co.nz", "co.za", "com.tr", "com.mx", "com.ar", "com.cn",
}


def is_cn_domain(host: str) -> bool:
    """
    判断该域名是否**可能有** ICP 备案。

    判断依据是**顶级后缀（TLD）**，不是"末两段"——
    例：qq.com 的末两段是 "qq.com"，顶级后缀是 "com"，
    两者含义完全不同，用错会导致所有 .com 域名被误判为境外。

    注意：.com/.net/.org 在国内同样允许注册并备案（qq.com、163.com 均有备案），
    所以"可能已备案"的后缀集里要包含这些通用后缀；
    只有确定属于境外注册局的（.co.uk/.jp/.de 等）才排除。
    """
    host = (host or "").lower()
    parts = host.split(".")
    if len(parts) < 2:
        return False
    # 顶级后缀（必要时含二级后缀，如 com.cn / co.uk）
    tld = parts[-1]
    second = parts[-2] if len(parts) >= 2 else ""
    full_suffix = "%s.%s" % (second, tld)

    # 确定属于境外注册局的后缀 → 不会有 ICP 备案
    if tld in OVERSEAS_TLDS or full_suffix in OVERSEAS_TLDS:
        return False
    return True


def get_icp(host: str) -> dict:
    """
    ICP 备案查询。
    返回：{hasIcp, subjectName, subjectType, auditDate, note}

    ★ 已移除 mock 演示数据。
      原实现在 RUN_MODE="mock" 下对任何国内域名都返回
      「某某科技有限公司（演示数据）」，与"域名档案"里那套假数据一样不可信。
      现在：未配置备案查询 API 时如实返回"查询失败"，绝不编造备案主体。
    """
    host = (host or "").lower()

    # 确定属于境外注册局后缀 → 明确标注无备案数据（这是事实判断，不是假数据）
    if not is_cn_domain(host):
        return {"hasIcp": False, "subjectName": None, "subjectType": None,
                "auditDate": None, "note": "境外注册局后缀域名，无ICP备案数据"}

    # ---------- 真实查询：走 config 配置的第三方备案 API ----------
    cfg = THIRD_PARTY["icp"]
    if cfg["enabled"] and cfg["api_url"]:
        try:
            from utils import http_get_json
            r = http_get_json(cfg["api_url"], params={"domain": host},
                              headers={"Authorization": "Bearer " + cfg["api_key"]})
            if r.get("ok") and isinstance(r.get("data"), dict):
                d = r["data"]
                return {
                    "hasIcp": bool(d.get("hasIcp", d.get("exist"))),
                    "subjectName": d.get("subjectName"),
                    "subjectType": d.get("subjectType"),
                    "auditDate": d.get("auditDate"),
                    "note": d.get("note", "备案信息实时查询"),
                }
            return {"hasIcp": None, "subjectName": None, "subjectType": None,
                    "auditDate": None,
                    "note": "备案查询失败：%s" % (r.get("error") or "查询源无响应"),
                    "unavailable": True}
        except Exception as e:
            return {"hasIcp": None, "subjectName": None, "subjectType": None,
                    "auditDate": None,
                    "note": "备案查询失败：%s" % str(e)[:60],
                    "unavailable": True}

    # 未配置备案 API → 如实说明查询失败，不返回任何虚构备案主体
    return {"hasIcp": None, "subjectName": None, "subjectType": None,
            "auditDate": None,
            "note": "ICP备案查询服务未配置，无法查询真实备案主体",
            "unavailable": True}


def get_whois(host: str) -> dict:
    """
    Whois 域名注册信息查询（旧入口，保留兼容）。

    ★ 已废弃：域名档案现在统一走 service/domain_info_service.py 的
      RDAP 实时查询（原实现对任何域名都返回同一套
      "2016-03-22 / Demo Registrar Inc. / 203.0.113.24" 假数据）。
      新实现按域名独立查询真实注册信息，且每个域名结果不同。

      本函数保留仅为兼容旧调用，推荐改用 get_domain_profile()。
    """
    host = (host or "").lower()
    try:
        from service.domain_info_service import get_domain_profile
        return get_domain_profile(host)
    except Exception as e:
        return {"unavailable": True, "error": str(e)[:100]}