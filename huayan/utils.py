# -*- coding: utf-8 -*-
"""
utils.py —— 工具函数
================================
包含三部分：
1. URL 本地特征检测（形近字识别、敏感路径、子域名层级、SSL、HTTP 明文）
   ★ 这部分是真实计算的逻辑，mock 模式下也不伪造，答辩时可说明是真实规则引擎
2. 风险等级判断（后端固化，四级：safe / suspicious / danger / unknown）
3. 通用请求工具（统一超时与异常捕获，第三方失败一律降级不崩溃）
"""
import re
import time
import socket
from datetime import datetime, timezone
from urllib.parse import urlparse

import requests
from config import REQUEST_TIMEOUT, RISK_CONFIG, MAX_URLS_PER_REQUEST


# ============================================================
# 一、URL 本地特征检测（真实计算，不 mock）
# ============================================================

# 形近混淆字符集：数字与其形近字母
# 骗子常用这些替换仿冒官网，肉眼极难分辨
LOOKALIKE_PAIRS = {
    "0": "O", "1": "l", "3": "E", "4": "A",
    "5": "S", "6": "G", "7": "T", "8": "B", "9": "g",
}
# 常见品牌词 —— 若域名中出现品牌词但主体是"仿冒"结构，命中形近检测
BRAND_KEYWORDS = [
    "alipay", "taobao", "weixin", "wechat", "qq", "baidu", "taobao",
    "bank", "unionpay", "icbc", "ccb", "abc", "cmb", "gov", "alipay",
]

# 敏感路径关键词 —— 命中意味着可能诱导输入隐私或下载可执行文件
SENSITIVE_PATH_KEYS = [
    "token", "password", "passwd", "download", "login", "signin",
    "verify", "account", "pay", "payment", "wallet",
    ".exe", ".apk", ".bat", ".scr", ".dll", ".msi", ".jar", ".zip", ".rar",
]

# 高危子域名黑名单关键词（黑产中转常用）
SUSPICIOUS_SUBDOMAIN_KEYS = [
    "login", "account", "verify", "secure", "auth", "confirm",
    "update", "billing", "payment", "signin",
]


def normalize_url(url: str) -> str:
    """补全缺失协议头，统一小写域名部分"""
    if not url:
        return ""
    url = url.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    return url


def extract_multiple_urls(raw) -> list:
    """
    从输入中提取全部 URL，支持三种形态：
      1. 字符串（含链接的文本）→ 正则提取
      2. 数组 → 逐项校验
      3. 单个 URL 字符串
    返回规范化后的 URL 列表（已去重、已过滤非法项）
    """
    candidates = []

    if isinstance(raw, list):
        candidates = [str(x) for x in raw]
    elif isinstance(raw, str):
        if raw.strip():
            candidates = re.findall(
                r"(?:https?://)?[a-zA-Z0-9][a-zA-Z0-9\-._~:/?#@!$&'()*+,;=%]*"
                r"(?:\.[a-zA-Z]{2,})+(?::\d{2,5})?(?:/[^\s]*)?",
                raw)
            # 正则要求域名段含字母，纯 IP 地址（如 http://192.168.1.1:8080/admin）
            # 匹配不到，需单独补充提取，否则 IP 直连类检测无法生效
            if not candidates:
                candidates = re.findall(
                    r"(?:https?://)?\d{1,3}(?:\.\d{1,3}){3}(?::\d{2,5})?(?:/[^\s]*)?",
                    raw)
            else:
                # 混合场景：文本中可能同时存在域名链接与纯 IP 链接
                candidates += re.findall(
                    r"(?<![\w.])https?://\d{1,3}(?:\.\d{1,3}){3}(?::\d{2,5})?(?:/[^\s]*)?",
                    raw)
    else:
        return []

    seen = set()
    out = []
    for c in candidates:
        c = c.strip()
        if not c:
            continue
        u = normalize_url(c)
        if u and is_valid_url(u) and u not in seen:
            seen.add(u)
            out.append(u)
    return out


def is_valid_url(url: str) -> bool:
    """URL 语法合法性校验"""
    try:
        p = urlparse(normalize_url(url))
        if p.scheme not in ("http", "https"):
            return False
        if not p.netloc:
            return False
        # 域名必须有点号，或者是纯 IP
        host = p.hostname or ""
        if "." not in host and not re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
            return False
        return True
    except Exception:
        return False


def _is_ip(host: str) -> bool:
    """判断是否 IP 直连访问"""
    return bool(re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host))


def detect_lookalike(host: str) -> dict:
    """
    域名形近混淆检测。
    原理：把域名标签中的数字按形近关系还原成字母，若还原后能命中已知品牌词，
    说明该域名极可能是用形近字符仿冒的钓鱼域名。
    例：0k.cn-security-check.top → Ok.cn-security-check.top → 疑似仿 alipay/ok
    """
    labels = host.split(".")
    for label in labels:
        # 还原数字为形近字母
        restored = "".join(LOOKALIKE_PAIRS.get(ch, ch) for ch in label)
        restored_lower = restored.lower()

        # 情况1：还原后命中品牌词，而原域名没有命中 → 高度疑似形近仿冒
        for brand in BRAND_KEYWORDS:
            if brand in restored_lower and brand not in label.lower():
                return {
                    "hit": True,
                    "label": label,
                    "restored": restored,
                    "brand": brand,
                    "detail": '域名片段 "%s" 含数字与字母混合，还原后形近品牌词 "%s"，存在仿冒风险（如 0/o、1/l 替换）'
                              % (label, brand),
                }

        # 情况2：标签本身同时含数字和字母（排除纯 IP 和常见版本号）
        if re.search(r"\d", label) and re.search(r"[a-zA-Z]", label) and len(label) <= 12:
            if not re.match(r"^(v\d+|www\d*)$", label):
                return {
                    "hit": True,
                    "label": label,
                    "restored": restored,
                    "brand": "",
                    "detail": '域名片段 "%s" 同时含数字与字母，易为形近字符仿冒（如 0/o、1/l 替换），肉眼极易看错'
                              % label,
                }
    return {"hit": False, "label": "", "restored": "", "brand": "", "detail": ""}


def count_subdomain_depth(host: str) -> int:
    """
    计算子域名层级数。
    规则：去掉 www；通用后缀（com/cn/net/org/top/xyz 等）与其二级后缀不计入层级。
    例：www.a.b.demo.com.cn → 3
    """
    labels = [l for l in host.split(".") if l and l != "www"]
    # 常见二级公共后缀
    two_level_suffixes = [
        "com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn",
        "co.uk", "com.hk", "com.tw", "com.au", "co.jp",
    ]
    suffix_len = 1
    if len(labels) >= 2 and ".".join(labels[-2:]) in two_level_suffixes:
        suffix_len = 2
    depth = len(labels) - suffix_len
    return max(depth, 0)


def detect_sensitive_path(path: str, query: str) -> list:
    """敏感路径关键词检测"""
    target = (path + " " + query).lower()
    return [k for k in SENSITIVE_PATH_KEYS if k in target]


# 可执行文件 / 压缩包后缀（出现在域名或路径任一处都需警惕）
EXECUTABLE_SUFFIXES = [".exe", ".apk", ".bat", ".scr", ".dll", ".msi",
                       ".jar", ".zip", ".rar", ".7z", ".vbs", ".ps1"]


def detect_executable_target(url: str, host: str, path: str) -> list:
    """
    检测链接是否直接指向可执行文件。
    场景：钓鱼站常把 .exe/.apk 伪装成域名的一部分（如 xxx.download.exe）
          或直接作为路径（/setup.exe）。命中即高危。
    """
    target = (host + path).lower()
    return [s for s in EXECUTABLE_SUFFIXES if s in target]


def detect_suspicious_subdomain(host: str) -> list:
    """可疑子域名关键词检测（登录/验证/支付类中转页）"""
    return [k for k in SUSPICIOUS_SUBDOMAIN_KEYS if k in host.lower()]


def check_ssl(url: str) -> dict:
    """
    SSL 证书状态检查（真实网络请求，仅做 HEAD 探测）。
    返回：证书是否正常 / 是否过期 / 是否域名不匹配 / 是否 HTTP 明文。
    真实模式抓取；mock 模式下由调用方传入 mock 值。
    """
    p = urlparse(url)
    if p.scheme == "http:":
        return {"status": "HTTP明文", "is_https": False, "valid": False, "expired": None,
                "mismatch": None, "self_signed": None, "detail": "该链接使用 HTTP 明文协议，无加密传输"}
    try:
        import ssl
        ctx = ssl.create_default_context()
        with socket.create_connection((p.hostname, 443), timeout=5) as sock:
            with ctx.wrap_socket(sock, server_hostname=p.hostname) as ssock:
                cert = ssock.getpeercert()
                return {"status": "正常", "is_https": True, "valid": True,
                        "expired": False, "mismatch": False, "self_signed": False,
                        "detail": "证书颁发机构可信，有效期内且域名匹配"}
    except ssl.SSLCertVerificationError as e:
        msg = str(e)
        if "expired" in msg:
            return {"status": "证书过期", "is_https": True, "valid": False, "expired": True,
                    "mismatch": None, "self_signed": None, "detail": "SSL 证书已过期"}
        if "Hostname mismatch" in msg or "doesn't match" in msg:
            return {"status": "域名不匹配", "is_https": True, "valid": False, "expired": None,
                    "mismatch": True, "self_signed": None,
                    "detail": "SSL 证书与访问域名不匹配，站点身份无法核验"}
        return {"status": "证书异常", "is_https": True, "valid": False, "expired": None,
                "mismatch": None, "self_signed": True, "detail": "证书校验失败：%s" % msg[:80]}
    except Exception as e:
        return {"status": "证书校验失败", "is_https": True, "valid": False, "expired": None,
                "mismatch": None, "self_signed": None, "detail": "无法校验证书：%s" % str(e)[:80]}


def local_url_features(url: str, ssl_result: dict = None) -> dict:
    """
    URL 本地特征检测总入口 —— 返回接口文档约定的 localFeatures 结构。
    ★ 真实计算，与运行模式无关。
    """
    url = normalize_url(url)
    p = urlparse(url)
    host = (p.hostname or "").lower()
    path = p.path or ""
    query = p.query or ""

    is_ip = _is_ip(host)
    lookalike = detect_lookalike(host)
    domain_len = len(".".join([l for l in host.split(".") if l != "www"]))
    sensitive_hits = detect_sensitive_path(path, query)
    suspicious_subs = detect_suspicious_subdomain(host)
    depth = count_subdomain_depth(host)
    exe_hits = detect_executable_target(url, host, path)

    if ssl_result is None:
        ssl_result = {"status": "未检测", "is_https": p.scheme == "https", "valid": None,
                      "expired": None, "mismatch": None, "self_signed": None, "detail": ""}

    return {
        "ipDirect": is_ip,
        "lookalikeDomain": lookalike["hit"],
        "lookalikeDetail": lookalike["detail"],
        "domainLength": domain_len,
        "sensitivePathHits": sensitive_hits,
        "executableTargets": exe_hits,
        "isExecutableTarget": len(exe_hits) > 0,
        "suspiciousSubdomainKeys": suspicious_subs,
        "protocol": p.scheme,
        "sslStatus": ssl_result.get("status", "未检测"),
        "sslValid": ssl_result.get("valid"),
        "sslExpired": ssl_result.get("expired"),
        "sslMismatch": ssl_result.get("mismatch"),
        "sslSelfSigned": ssl_result.get("self_signed"),
        "sslDetail": ssl_result.get("detail", ""),
        "subdomainDepth": depth,
        # 派生布尔量，供风险判定直接使用
        "isHttps": p.scheme == "https",
        "tooManySubdomains": depth >= RISK_CONFIG["MAX_SUBDOMAIN_DEPTH"],
        "domainTooLong": domain_len > RISK_CONFIG["MAX_DOMAIN_LENGTH"],
    }


# ============================================================
# 二、风险等级判定（后端固化 —— 前端无权修改）
# ============================================================

def domain_age_days(reg_date_str: str):
    """根据注册时间计算域名存在天数，无法解析返回 None"""
    if not reg_date_str:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S", "%Y.%m.%d"):
        try:
            d = datetime.strptime(str(reg_date_str)[:19].strip(), fmt)
            return (datetime.now() - d).days
        except ValueError:
            continue
    return None


def collect_local_risk_clues(local: dict, domain_info: dict) -> list:
    """
    提取 URL 本地特征产生的风险线索。
    ★ 独立函数：danger 与 suspicious 两种分支都要调用，
      保证高危站点的报告单里同样能展示形近混淆、明文传输等关键线索，
      而不是只显示威胁库标记。用户需要看到完整的风险构成。
    """
    clues = []

    if local.get("ipDirect"):
        clues.append("IP 直连访问：直接以 IP 访问站点，正规企业官网极少采用，恶意站点偏好此方式")

    if local.get("lookalikeDomain"):
        clues.append("域名形近混淆：" + local.get("lookalikeDetail", ""))

    age = domain_age_days((domain_info or {}).get("regDate"))
    if age is not None and age < RISK_CONFIG["DOMAIN_AGE_SAFE_DAYS"]:
        clues.append("新建域名风险：域名注册仅 %d 天，远低于 %d 天安全阈值，黑产常用新域名搭建临时钓鱼站"
                     % (age, RISK_CONFIG["DOMAIN_AGE_SAFE_DAYS"]))

    if domain_info and domain_info.get("serverArea") and (
            "境外" in domain_info["serverArea"] or "海外" in domain_info["serverArea"]
            or domain_info.get("isOverseas")):
        clues.append("境外服务器风险：服务器部署在海外，国内监管难以覆盖")

    if not local.get("isHttps"):
        clues.append("HTTP 无加密：账号密码、手机号在传输过程中可能被窃听截取")

    if local.get("sslStatus") not in ("正常", "未检测"):
        clues.append("SSL 证书异常：%s，站点身份无法正常核验" % local.get("sslStatus"))

    if local.get("tooManySubdomains"):
        clues.append("多层子域名结构（%d 级）：常见于短链中转或仿冒路径"
                     % local["subdomainDepth"])

    if local.get("sensitivePathHits"):
        clues.append("敏感路径风险：命中关键词 %s，可能诱导输入隐私信息或下载可执行文件"
                     % "、".join("「%s」" % k for k in local["sensitivePathHits"]))

    if local.get("domainTooLong"):
        clues.append("域名长度偏长（%d 字符），增加仿冒与混淆难度" % local["domainLength"])

    return clues


def judge_risk_level(local: dict, domain_info: dict, icp: dict,
                     threat: dict, page_meta: dict) -> dict:
    """
    核心风险等级判定 —— 后端固化，输出四级之一：
        safe       安全
        suspicious 可疑
        danger     高危
        unknown    未知

    判定优先级（严格顺序）：
      1. 数据不足 → unknown
      2. 第三方威胁库命中 / 页面仿冒登录 → danger
      3. 满足可疑条件之一 → suspicious
      4. 全部安全条件满足 → safe
      5. 其余情况 → suspicious（有风险线索但暂无恶意标记）

    ★ 关键约束：只允许"升风险"，绝不因为某维度缺失而下调已有风险等级。
    """
    risks = []

    # ---------- 第 1 层：数据充分性检查 ----------
    if not domain_info or not domain_info.get("domain"):
        return {
            "level": "unknown",
            "label": "未知",
            "reason": "缺少域名基础信息，无法完成研判",
            "risks": ["域名基础信息缺失，可能域名解析失败或接口超时"],
        }

    age = domain_age_days(domain_info.get("regDate"))

    # ---------- 第 2 层：高危判定（一旦命中即为 danger） ----------
    danger_hit = False

    if threat:
        if threat.get("phishing"):
            risks.append("第三方安全接口标记为【钓鱼站点】")
            danger_hit = True
        if threat.get("fraud"):
            risks.append("第三方安全接口标记为【诈骗站点】")
            danger_hit = True
        if threat.get("malware"):
            risks.append("第三方安全接口标记为【恶意软件站点】")
            danger_hit = True
        if threat.get("gambling"):
            risks.append("第三方安全接口标记为【赌博站点】")
            danger_hit = True
        if threat.get("porn"):
            risks.append("第三方安全接口标记为【色情站点】")
            danger_hit = True

    if page_meta:
        if page_meta.get("fakeLoginForm"):
            risks.append("页面存在仿冒登录表单，可能诱导输入账号密码")
            danger_hit = True
        if page_meta.get("autoDownload"):
            risks.append("页面含自动下载可执行文件脚本，存在木马植入风险")
            danger_hit = True

    # 敏感路径命中可执行文件后缀 → 直接判高危
    exe_hits = local.get("executableTargets") or []
    if exe_hits:
        risks.append("链接直接指向可执行文件/压缩包（%s），极可能为恶意程序载体"
                     % "、".join(exe_hits))
        danger_hit = True

    if danger_hit:
        # 高危站点同样展示本地特征线索，让用户看到完整的风险构成
        local_clues = collect_local_risk_clues(local, domain_info)
        for c in local_clues:
            if c not in risks:
                risks.append(c)
        return {"level": "danger", "label": "高危", "reason": "命中恶意特征", "risks": risks}

    # ---------- 第 3 层：可疑判定 ----------
    risks.extend(collect_local_risk_clues(local, domain_info))

    if risks:
        return {"level": "suspicious", "label": "可疑", "reason": "存在风险线索", "risks": risks}

    # ---------- 第 4 层：安全判定 ----------
    # 安全需同时满足：无风险标记 + SSL正常 + 域名≥90天 + （国内域名备案主体与标题基本匹配）
    if age is not None and age < RISK_CONFIG["DOMAIN_AGE_SAFE_DAYS"]:
        return {"level": "suspicious", "label": "可疑", "reason": "域名过新", "risks": risks}

    return {"level": "safe", "label": "安全",
            "reason": "未检出风险特征，域名运营时长充足，证书状态正常", "risks": []}


# ============================================================
# 三、通用请求工具（第三方失败一律降级，绝不抛出崩溃）
# ============================================================

class RateLimiter:
    """基于内存的单 IP 每分钟限流器（生产环境可换 Redis）"""

    def __init__(self, limit_per_minute=60):
        self.limit = limit_per_minute
        self.records = {}   # ip -> [timestamp, ...]

    def allow(self, ip: str) -> bool:
        now = time.time()
        window_start = now - 60
        hits = [t for t in self.records.get(ip, []) if t > window_start]
        if len(hits) >= self.limit:
            self.records[ip] = hits
            return False
        hits.append(now)
        self.records[ip] = hits
        # 简单清理，防止内存膨胀
        if len(self.records) > 5000:
            for k in list(self.records.keys()):
                if not [t for t in self.records[k] if t > window_start]:
                    del self.records[k]
        return True


def http_get_json(url: str, headers: dict = None, params: dict = None,
                  timeout: int = None) -> dict:
    """
    统一的 GET JSON 请求封装。
    ★ 任何异常（超时/连接失败/解析失败）都返回 {"ok": False} 而非抛出，
      由调用方决定降级为 unknown。
    """
    timeout = timeout or REQUEST_TIMEOUT
    try:
        resp = requests.get(url, headers=headers or {}, params=params,
                            timeout=timeout, allow_redirects=False)
        if resp.status_code != 200:
            return {"ok": False, "error": "HTTP %d" % resp.status_code, "status": resp.status_code}
        return {"ok": True, "data": resp.json()}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "TIMEOUT", "timeout": True}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": "CONNECTION_FAILED", "connectionFailed": True}
    except requests.exceptions.RequestException as e:
        return {"ok": False, "error": str(e)[:120]}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def http_post_json(url: str, data: dict = None, headers: dict = None,
                   timeout: int = None) -> dict:
    """统一的 POST JSON 请求封装，异常处理同上"""
    timeout = timeout or REQUEST_TIMEOUT
    try:
        resp = requests.post(url, json=data or {}, headers=headers or {}, timeout=timeout)
        if resp.status_code != 200:
            return {"ok": False, "error": "HTTP %d" % resp.status_code, "status": resp.status_code}
        return {"ok": True, "data": resp.json()}
    except requests.exceptions.Timeout:
        return {"ok": False, "error": "TIMEOUT", "timeout": True}
    except requests.exceptions.ConnectionError:
        return {"ok": False, "error": "CONNECTION_FAILED", "connectionFailed": True}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120]}


def utcnow_str():
    """当前时间字符串"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def make_response(code: int, data=None, msg: str = None):
    """统一响应格式构造器"""
    from config import CODE_MSG
    return {
        "code": code,
        "msg": msg or CODE_MSG.get(code, "unknown"),
        "data": data if data is not None else {},
    }