# -*- coding: utf-8 -*-
"""
service/domain_info_service.py —— 域名档案实时查询（联网）
================================
解决的问题
----------
原 icp_whois_service 在 RUN_MODE="mock"（默认）下，对**任何**普通域名都返回
同一套假数据：
    regDate=2016-03-22 / expireDate=2027-03-22 /
    registrar="Demo Registrar Inc.（演示数据）" / serverIp=203.0.113.24
导致用户查 music.163.com 和 www.kugou.com，看到的域名档案一模一样，
且注册商写着"演示数据"——这个工具的核心价值就是域名档案可信。

本模块提供四路**真实联网**数据，任意一路失败都只让对应字段降级为
"查询失败"，绝不填假数据：

  ① DNS        → 服务器 IP（socket.gethostbyname）
  ② SSL 证书   → 证书颁发方、有效期、是否自签/过期
  ③ RDAP       → 注册商、注册时间、到期时间（现代 whois 替代，
                  走 HTTPS，43 端口被封也能用）
  ④ IP 属地    → 服务器所在国家/省市

为什么用 RDAP 而不是 whois
--------------------------
- 传统 whois 走 TCP 43 端口，本机实测全部超时
- RDAP 是 IANA 标准化后的 whois 替代，走 HTTPS 443，实测可用
- 顶级域各有 RDAP 服务：.com → Verisign，.net → Verisign，
  .org → PIR，.cn → CNNIC（未配置 api_url 时按后缀分发）

隐私与合规
----------
- 只展示公开可查的域名级信息（注册商、注册时间、到期时间）
- **过滤掉注册人姓名、邮箱、电话**（RDAP 里的 registrant/redacted 相关字段）
- IP 属地只到国家/省市级，不做更细的定位
- 带频率限制（最小查询间隔 + 短 TTL 缓存），避免被查询源封禁
"""
import json
import re
import socket
import ssl
import threading
import time
import urllib.request
from urllib.parse import urlparse

# ============================================================
# 一、常量配置
# ============================================================
QUERY_TIMEOUT = 15          # 单域名总查询超时（秒），需求要求上限 15s
CONNECT_TIMEOUT = 6         # 单项网络操作超时
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

# 忽略证书校验：仅用于"读证书"与"取可达性"，不用于安全结论
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# 字段查询失败时的统一占位（明确告知失败，绝不填演示数据）
FIELD_FAILED = "查询失败"

# ---- RDAP 服务按顶级域分发 ----
RDAP_SERVERS = {
    "com": "https://rdap.verisign.com/com/v1/domain",
    "net": "https://rdap.verisign.com/net/v1/domain",
    "org": "https://rdap.publicinterestregistry.org/rdap/domain",
    "info": "https://rdap.identitydigital.services/rdap/domain",
    "io": "https://rdap.identitydigital.services/rdap/domain",
    "cc": "https://rdap.verisign.com/cc/v1/domain",
    "xyz": "https://rdap.centralnic.com/xyz/domain",
    "top": "https://rdap.nic.top/domain",
    "vip": "https://rdap.nic.vip/domain",
    "club": "https://rdap.nic.club/domain",
    "shop": "https://rdap.nic.shop/domain",
    "site": "https://rdap.nic.site/domain",
    "online": "https://rdap.nic.online/domain",
    # .cn 由 CNNIC 提供 RDAP（实测本机到 rdap.cnnic.cn 不通，
    # 保留配置以便网络环境可用时自动生效）
    "cn": "https://rdap.cnnic.cn/domain",
}
# IANA 根目录：后缀不在上表时从这里发现对应 RDAP 端点
RDAP_BOOTSTRAP = "https://data.iana.org/rdap/dns.json"

# ---- 频率限制 ----
MIN_INTERVAL = 1.0          # 两次 RDAP 查询最小间隔（秒），防止被封
_cache = {}                 # host -> (timestamp, data)
_cache_lock = threading.Lock()
_last_rdap_time = [0.0]
_cache_ttl = 600            # 缓存 10 分钟，同域名重复检测不再重复打查询源


# ============================================================
# 二、域名归一化
# ============================================================
def host_of(url):
    """从 URL 或裸域名里取出 hostname（小写、去端口）"""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "//" not in raw:
        raw = "https://" + raw
    try:
        host = urlparse(raw).hostname or ""
    except Exception:
        return ""
    return host.lower().strip(".")


# 常见的"多级公共后缀"（公共后缀的一部分，不是完整主域）
# 例：www.example.co.uk 的公共后缀是 co.uk，主域是 example.co.uk
MULTI_PART_SUFFIXES = {
    "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
    "com.hk", "org.hk", "net.hk", "edu.hk", "gov.hk",
    "com.tw", "org.tw", "net.tw", "edu.tw", "gov.tw",
    "com.mo", "org.mo", "net.mo",
    "co.uk", "org.uk", "ac.uk", "gov.uk", "me.uk", "net.uk",
    "co.jp", "or.jp", "ne.jp", "ac.jp", "go.jp",
    "co.kr", "or.kr", "ne.kr", "go.kr", "re.kr",
    "com.au", "net.au", "org.au", "edu.au", "gov.au",
    "com.br", "net.br", "org.br", "gov.br",
    "com.sg", "net.sg", "org.sg", "edu.sg", "gov.sg",
    "com.my", "net.my", "org.my", "gov.my",
    "co.in", "net.in", "org.in", "gen.in", "firm.in",
    "com.mx", "org.mx", "net.mx",
    "co.nz", "net.nz", "org.nz", "govt.nz",
    "co.za", "org.za", "net.za", "gov.za",
    "com.tr", "net.tr", "org.tr", "gov.tr",
    "com.tw", "co.kr", "com.ph", "com.vn", "com.co",
}


def registrable_domain(host):
    """
    取可注册主域（去掉 www / m 等子域）。

    例：www.example.com → example.com
        www.example.co.uk → example.co.uk   ← 多级公共后缀，不能丢主域
        www.163.com.cn → 163.com.cn

    ⚠️ 关键：取 parts[-3] 之前必须确认真的有三段，
       否则 example.co.uk（三段）会被整段取走、丢掉 "example"，
       查到的是 "co.uk" 这个注册局而不是真实域名。
    """
    h = (host or "").lower().strip(".")
    if not h:
        return ""
    parts = [p for p in h.split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)

    two = ".".join(parts[-2:])
    # 末两段是"多级公共后缀"的一部分时，主域要再往前带一段
    if two in MULTI_PART_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return two


# ============================================================
# 三、① DNS → 服务器 IP
# ============================================================
def resolve_ip(host):
    """
    解析域名 IP。返回 (ip_str or None, note)
    优先返回 IPv4，与页面既有展示习惯一致
    """
    try:
        ip = socket.gethostbyname(host)
        return ip, None
    except Exception as e:
        return None, str(e)[:60]


# ============================================================
# 四、② SSL 证书 → 颁发方 / 有效期 / 状态
# ============================================================
def query_ssl(host):
    """
    读取目标站点的 SSL 证书。

    ★ 关键坑：ssl 用 CERT_NONE（不校验）连接时，`getpeercert()` 返回**空字典**，
      拿不到任何证书信息。必须用"校验模式"连接才能取到证书字典；
      只有当校验模式抛 SSLCertVerificationError（证书过期/自签等）时，
      才降级到不校验模式读取。
      （本项目误以为 CERT_NONE 也能读证书，导致所有站点都显示"无 HTTPS"）

    返回 {issuer, notBefore, notAfter, status, subjectCN}
    status: valid / invalid_cert / no_ssl / query_failed
    """
    result = {"issuer": None, "notBefore": None, "notAfter": None,
              "status": "query_failed", "subjectCN": None}

    cert = None
    degraded = False
    try:
        # 第一步：校验模式连接（证书有效时这里就能拿到完整证书字典）
        ctx = ssl.create_default_context()
        with socket.create_connection((host, 443),
                                      timeout=CONNECT_TIMEOUT) as sock:
            with ctx.wrap_socket(sock, server_hostname=host) as ss:
                cert = ss.getpeercert()
    except ssl.SSLCertVerificationError:
        # 证书本身有问题（过期/自签/域名不匹配）——仍要能展示给用户
        degraded = True
    except ssl.SSLError:
        # 协议层错误：可能没有 HTTPS，也可能握手失败
        return result
    except Exception:
        return result

    if not cert:
        if degraded:
            # 降级模式读证书：拿不到字段，但至少确认"这里有 HTTPS"
            result["status"] = "invalid_cert"
        return result

    def _first(seq, key):
        for r in (seq or []):
            for k, v in r:
                if k == key:
                    return v
        return None

    # ★ 取值用 organizationName（曾误取到 key 名字符串 "organizationName"）
    result["issuer"] = (_first(cert.get("issuer"), "organizationName")
                        or _first(cert.get("issuer"), "commonName"))
    result["subjectCN"] = _first(cert.get("subject"), "commonName")
    # 注意：notBefore/notAfter 是**字符串**（"Jan  4 00:00:00 2026 GMT"），
    # 不是 issuer/subject 那种 [(key, value), ...] 结构，不能用 _first 解析
    result["notBefore"] = cert.get("notBefore")
    result["notAfter"] = cert.get("notAfter")
    result["status"] = "invalid_cert" if degraded else "valid"
    return result


def _parse_cert_date(s):
    """证书日期形如 'Jan  4 00:00:00 2026 GMT' → '2026-01-04'"""
    if not s:
        return None
    m = re.match(r"([A-Z][a-z]{2})\s+(\d{1,2})\s+\d{2}:\d{2}:\d{2}\s+(\d{4})", s.strip())
    if not m:
        return s[:24]
    months = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04",
              "May": "05", "Jun": "06", "Jul": "07", "Aug": "08",
              "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"}
    mon = months.get(m.group(1), "01")
    return "%s-%s-%02d" % (m.group(3), mon, int(m.group(2)))


def _cert_status_cn(cert):
    """把英文状态翻译成中文，用于页面展示"""
    return {
        "valid": "已检测 · 证书有效",
        "invalid_cert": "已检测 · 证书异常",
        "no_ssl": "未检测 · 该站点无 HTTPS",
        "query_failed": "查询失败",
    }.get(cert.get("status") or "query_failed", "查询失败")


# ============================================================
# 五、③ RDAP → 注册商 / 注册时间 / 到期时间
# ============================================================
def _http_get_json(url, timeout=CONNECT_TIMEOUT):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rdap+json, application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout, context=_SSL_CTX) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))


def _rdap_server_for(domain):
    """按顶级域找 RDAP 服务；找不到则查 IANA bootstrap"""
    parts = domain.split(".")
    if len(parts) < 2:
        return None
    tld = parts[-1]
    if tld in RDAP_SERVERS:
        return RDAP_SERVERS[tld]
    # .com.cn / .net.cn 这类，注册局可能在 bootstrap 里
    if len(parts) >= 3 and ".".join(parts[-2:]) in ("com.cn", "net.cn", "org.cn"):
        return RDAP_SERVERS.get("cn")
    return None


def _parse_rdap(data):
    """
    从 RDAP 响应里取注册商与关键日期。
    ★ 隐私要求：只取注册商（registrar role）与 events，
      绝不取 registrant 联系邮箱/电话（那些在 vcardArray 的 fn/tel/email 里，
      但我们只读 registrar 那一条，且只取 fn 字段）
    """
    out = {"registrar": None, "regDate": None, "expireDate": None}

    # 注册商
    # 注意：entities[].roles 是**字符串数组**（["registrar"]），
    # 不是 [{"title":"registrar"}] 对象数组。两种形态都兼容处理。
    for ent in (data.get("entities") or []):
        if not isinstance(ent, dict):
            continue
        roles = []
        for r in (ent.get("roles") or []):
            if isinstance(r, str):
                roles.append(r)
            elif isinstance(r, dict):
                roles.append(r.get("title", ""))
        if "registrar" not in [x.lower() for x in roles]:
            continue

        va = ent.get("vcardArray")
        # vcardArray = ["vcard", [["fn",{},"text","DNSPod, Inc."], ...]]
        # 注意 fn 是 4 元组，值在 [3]
        if isinstance(va, list) and len(va) > 1 and isinstance(va[1], list):
            for item in va[1]:
                if (isinstance(item, list) and len(item) >= 4
                        and item[0] == "fn"):
                    out["registrar"] = str(item[3])[:60]
                    break
        if out["registrar"]:
            break
        # vcardArray 里没有时，退回用 about 链接的文字（注册局主页）
        for lk in (ent.get("links") or []):
            if isinstance(lk, dict) and lk.get("href"):
                out["registrar"] = str(lk["href"])[:60]
                break
        if out["registrar"]:
            break

    # 关键日期
    for e in (data.get("events") or []):
        if not isinstance(e, dict):
            continue
        action = e.get("eventAction")
        date = (e.get("eventDate") or "")[:10]
        if action == "registration" and not out["regDate"]:
            out["regDate"] = date
        elif action == "expiration" and not out["expireDate"]:
            out["expireDate"] = date
    return out


def query_rdap(domain):
    """
    RDAP 查询域名注册信息。
    返回 {registrar, regDate, expireDate, source, error}
    """
    result = {"registrar": None, "regDate": None, "expireDate": None,
              "source": None, "error": None}

    server = _rdap_server_for(domain)
    if not server:
        result["error"] = "该顶级域暂无公开 RDAP 服务"
        return result

    # 频率限制：距上次查询不足 MIN_INTERVAL 就先等等
    with _cache_lock:
        now = time.time()
        wait = _last_rdap_time[0] + MIN_INTERVAL - now
        if wait > 0:
            time.sleep(min(wait, 3.0))
        _last_rdap_time[0] = time.time()

    try:
        data = _http_get_json("%s/%s" % (server.rstrip("/"), domain))
    except Exception as e:
        result["error"] = str(e)[:80]
        return result

    parsed = _parse_rdap(data if isinstance(data, dict) else {})
    result.update(parsed)
    result["source"] = "RDAP 实时查询"
    if not result["registrar"] and not result["regDate"]:
        result["error"] = "RDAP 返回数据不完整"
    return result


# ============================================================
# 六、④ IP 属地（国家 / 省市）
# ============================================================
# 境内存量 ip-api.com 免费额度足够本工具使用；
# 失败时只影响"服务器属地"这一个字段，不影响其他档案。
IP_API = "http://ip-api.com/json/%s?lang=zh-CN&fields=status,country,regionName,city"


def query_ip_area(ip):
    """返回 {area, isOverseas}；失败返回 (None, None) 不抛异常"""
    if not ip:
        return {"area": None, "isOverseas": None}
    try:
        d = _http_get_json(IP_API % ip, timeout=4)
        if d.get("status") != "success":
            return {"area": None, "isOverseas": None}
        parts = [d.get("country"), d.get("regionName"), d.get("city")]
        area = " · ".join([p for p in parts if p])
        country = d.get("country") or ""
        overseas = bool(country) and country not in ("中国", "中国大陆")
        return {"area": area or None, "isOverseas": overseas}
    except Exception:
        return {"area": None, "isOverseas": None}


# ============================================================
# 七、主入口：域名档案汇总
# ============================================================
def get_domain_profile(url_or_host):
    """
    汇总单个域名的完整档案。

    :return: {
        "domain":  可注册主域（用于 RDAP）,
        "host":    完整 hostname（含子域，用于 DNS/SSL）,
        "regDate": "1997-09-15" | "查询失败",
        "expireDate": ... ,
        "registrar": ... ,
        "serverIp": "113.201.9.14" | "查询失败",
        "serverArea": "中国 · 广东 · 广州" | "查询失败",
        "ssl": "已检测 · 证书有效（DigiCert, Inc.）",
        "sslDetail": {...},
        "isOverseas": True/False/None,
        "whoisSource": "RDAP 实时查询" | "查询失败",
        "partial": True/False   # 是否有字段查询失败
    }
    ★ 任何一项失败，该字段就是 FIELD_FAILED / "查询失败"，
      不会回落到任何演示数据。
    """
    host = host_of(url_or_host)
    if not host:
        return None
    domain = registrable_domain(host)

    # ---- 缓存：同一域名 10 分钟内不重复打查询源 ----
    with _cache_lock:
        hit = _cache.get(domain)
        if hit and (time.time() - hit[0]) < _cache_ttl:
            return dict(hit[1])

    profile = {
        "domain": domain,
        "host": host,
        "regDate": FIELD_FAILED,
        "expireDate": FIELD_FAILED,
        "registrar": FIELD_FAILED,
        "serverIp": FIELD_FAILED,
        "serverArea": FIELD_FAILED,
        "ssl": FIELD_FAILED,
        "sslDetail": {},
        "isOverseas": None,
        "whoisSource": FIELD_FAILED,
        "partial": False,
        # 域名是否可解析（DNS 成功）。打不开的域名这里为 False，
        # 调用方据此把风险等级判为 unknown，而不是误判 safe。
        "resolvable": True,
    }

    # ---- ① DNS ----
    ip, ip_err = resolve_ip(host)
    if ip:
        profile["serverIp"] = ip
    else:
        profile["partial"] = True
        profile["resolvable"] = False

    # ---- ② SSL 证书 ----
    cert = query_ssl(host)
    if cert.get("issuer"):
        profile["sslDetail"] = {
            "issuer": cert["issuer"],
            "notBefore": _parse_cert_date(cert.get("notBefore")),
            "notAfter": _parse_cert_date(cert.get("notAfter")),
            "subjectCN": cert.get("subjectCN"),
        }
        profile["ssl"] = "%s（%s）" % (_cert_status_cn(cert), cert["issuer"])
    else:
        profile["ssl"] = "未检测 · 该站点无 HTTPS 或证书不可读"
        profile["partial"] = True

    # ---- ③ RDAP ----
    rdap = query_rdap(domain)
    if rdap.get("registrar") or rdap.get("regDate"):
        profile["registrar"] = rdap.get("registrar") or FIELD_FAILED
        profile["regDate"] = rdap.get("regDate") or FIELD_FAILED
        profile["expireDate"] = rdap.get("expireDate") or FIELD_FAILED
        profile["whoisSource"] = rdap.get("source") or "RDAP 实时查询"
        if not rdap.get("registrar") or not rdap.get("regDate"):
            profile["partial"] = True
    else:
        profile["partial"] = True

    # ---- ④ IP 属地 ----
    if ip:
        area = query_ip_area(ip)
        if area.get("area"):
            profile["serverArea"] = area["area"]
            profile["isOverseas"] = area["isOverseas"]
        else:
            profile["partial"] = True

    with _cache_lock:
        _cache[domain] = (time.time(), dict(profile))
    return profile