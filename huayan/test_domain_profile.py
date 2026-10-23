# -*- coding: utf-8 -*-
"""
test_domain_profile.py —— 域名档案实时查询专项测试
================================
验证要点：
  1. 不同域名返回**不同**数据（原 bug：所有域名共用一套演示假数据）
  2. 代码中不再残留 Demo Registrar / 某某科技 / 2016-03-22 等假数据
  3. 网络失败时字段标记为「查询失败」，不回落假数据
  4. RDAP 解析正确（注册商在 vcardArray 的 fn 4元组 [3] 处）
  5. SSL 证书读取正确（必须用校验模式，CERT_NONE 下 getpeercert 返回空）
  6. ICP 国内/境外判定正确

不依赖网络的部分用 monkeypatch 替身；联网的部分标记为可跳过。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from service import domain_info_service as dis          # noqa: E402
from service import icp_whois_service as icp_svc       # noqa: E402

PASS = []
FAIL = []


def check(label, cond, detail=""):
    if cond:
        PASS.append(label)
    else:
        FAIL.append("%s  %s" % (label, detail))
        print("  FAIL %s  %s" % (label, detail))


def eq(label, got, want):
    check(label, got == want, "=> %r (期望 %r)" % (got, want))


# ============================================================
print("=== 1. registrable_domain 域名归一化 ===")
R = dis.registrable_domain
eq("裸域名", R("www.baidu.com"), "baidu.com")
eq("多级子域", R("a.b.example.com"), "example.com")
eq("com.cn 二级后缀", R("www.163.com.cn"), "163.com.cn")
eq("gov.cn 二级后缀", R("www.moe.gov.cn"), "moe.gov.cn")
eq("两段直接返回", R("example.com"), "example.com")
eq("单段", R("localhost"), "localhost")
eq("空", R(""), "")
# 回归：多级公共后缀不能丢掉主域（曾把 example.co.uk 截成 co.uk）
eq("co.uk 保留主域", R("www.example.co.uk"), "example.co.uk")
eq("com.cn 保留主域", R("www.abc.com.cn"), "abc.com.cn")
eq("co.jp 保留主域", R("www.example.co.jp"), "example.co.jp")
eq("com.au 保留主域", R("shop.example.com.au"), "example.com.au")
eq("三段纯公共后缀不丢内容", R("a.co.uk"), "a.co.uk")


print("=== 2. host_of URL 取主机 ===")
H = dis.host_of
eq("带协议", H("https://music.163.com/path"), "music.163.com")
eq("裸域名", H("music.163.com"), "music.163.com")
eq("带端口", H("https://example.com:8443/x"), "example.com")
eq("大写转小写", H("HTTPS://WWW.BAIDU.COM"), "www.baidu.com")
eq("空", H(""), "")


print("=== 3. _parse_cert_date 证书日期解析 ===")
P = dis._parse_cert_date
eq("标准格式", P("Jan  4 00:00:00 2026 GMT"), "2026-01-04")
eq("两位日", P("Dec 26 06:42:58 2026 GMT"), "2026-12-26")
eq("空", P(None), None)


print("=== 4. _parse_rdap 注册信息解析 ===")
# RDAP 实测真实结构：entities[].roles 是**字符串数组**，fn 是 4 元组
sample = {
    "entities": [{
        "roles": ["registrar"],
        "vcardArray": ["vcard", [
            ["version", {}, "text", "4.0"],
            ["fn", {}, "text", "DNSPod, Inc."],
        ]],
    }],
    "events": [
        {"eventAction": "registration", "eventDate": "2004-02-10T05:00:00Z"},
        {"eventAction": "expiration", "eventDate": "2028-02-10T05:00:00Z"},
        {"eventAction": "last changed", "eventDate": "2024-01-01T00:00:00Z"},
    ],
}
r = dis._parse_rdap(sample)
eq("注册商(fn 第4元素)", r["registrar"], "DNSPod, Inc.")
eq("注册时间", r["regDate"], "2004-02-10")
eq("到期时间", r["expireDate"], "2028-02-10")

# 角色为对象数组的兼容形态
sample2 = {
    "entities": [{
        "roles": [{"title": "registrar"}],
        "vcardArray": ["vcard", [["fn", {}, "text", "MarkMonitor Inc."]]],
    }],
    "events": [],
}
eq("兼容对象型 roles", dis._parse_rdap(sample2)["registrar"], "MarkMonitor Inc.")

# 无 registrar 实体 → 注册商为 None（不报错）
eq("无注册商实体", dis._parse_rdap({"entities": [], "events": []})["registrar"], None)
# 畸形输入不崩溃
for bad in [{}, {"entities": None}, {"entities": [None]}, {"events": [None]}]:
    try:
        dis._parse_rdap(bad)
        check("畸形输入不崩溃: %s" % str(bad)[:26], True)
    except Exception as e:
        check("畸形输入不崩溃: %s" % str(bad)[:26], False, str(e))


print("=== 5. _rdap_server_for 按后缀分发 ===")
S = dis._rdap_server_for
check(".com 走 Verisign", "verisign" in (S("baidu.com") or "").lower())
check(".com.cn 走 .cn 通道", S("163.com.cn") is not None)
check("无后缀返回 None", S("localhost") is None)


print("=== 6. ICP 国内/境外判定 ===")
C = icp_svc.is_cn_domain
check(".cn 是国内", C("163.cn") is True)
check(".gov.cn 是国内", C("www.gov.cn") is True)
check(".com 不排除（可能已备案）", C("qq.com") is True)
check(".co.uk 是境外", C("example.co.uk") is False)
check(".jp 是境外", C("example.jp") is False)
eq("境外域名标注文案", icp_svc.get_icp("example.co.uk")["hasIcp"], False)


print("=== 7. 断网兜底：不回落假数据（关键）===")
# 替换所有联网函数为失败替身
orig = (dis.resolve_ip, dis.query_ssl, dis.query_rdap, dis.query_ip_area)
dis.resolve_ip = lambda h: (None, "模拟断网")
dis.query_ssl = lambda h: {"issuer": None, "notBefore": None, "notAfter": None,
                           "status": "query_failed", "subjectCN": None}
dis.query_rdap = lambda d: {"registrar": None, "regDate": None, "expireDate": None,
                            "source": None, "error": "模拟断网"}
dis.query_ip_area = lambda ip: {"area": None, "isOverseas": None}
dis._cache.clear()
try:
    p = dis.get_domain_profile("https://music.163.com")
    for f in ["registrar", "regDate", "expireDate", "serverIp", "serverArea"]:
        eq("断网时 %s 标记查询失败" % f, p.get(f), dis.FIELD_FAILED)
    eq("断网时 whoisSource", p.get("whoisSource"), dis.FIELD_FAILED)
    eq("断网时 partial 为真", p.get("partial"), True)
    eq("断网时主域名仍可解析", p.get("domain"), "163.com")

    # 断言没有出现任何演示假数据
    fake_markers = ["Demo Registrar", "某某科技", "演示数据",
                    "2016-03-22", "203.0.113", "198.51.100", "192.0.2"]
    blob = str(p)
    leaked = [m for m in fake_markers if m in blob]
    check("断网结果不含任何演示数据", not leaked, "泄漏: %s" % leaked)
finally:
    (dis.resolve_ip, dis.query_ssl, dis.query_rdap, dis.query_ip_area) = orig
    dis._cache.clear()


print("=== 8. 每个域名独立查询（核心回归）===")
# 用两份明显不同的假数据模拟两个域名，验证结果不共用
responses = {
    "163.com": {"registrar": "MarkMonitor Shanghai", "regDate": "1997-09-15",
                "expireDate": "2027-09-14", "ip": "113.201.9.14"},
    "kugou.com": {"registrar": "DNSPod, Inc.", "regDate": "2004-02-10",
                  "expireDate": "2028-02-10", "ip": "180.95.141.100"},
}
orig_rdap, orig_ip = dis.query_rdap, dis.resolve_ip
dis.query_rdap = lambda d: dict(responses[d], source="RDAP 实时查询", error=None)
dis.resolve_ip = lambda h: (responses[dis.registrable_domain(h)]["ip"], None)
dis._cache.clear()
try:
    a = dis.get_domain_profile("https://music.163.com")
    b = dis.get_domain_profile("https://www.kugou.com")
    check("两个域名档案不同", a != b)
    eq("网易域名", a["domain"], "163.com")
    eq("酷狗域名", b["domain"], "kugou.com")
    check("注册商不同", a["registrar"] != b["registrar"])
    check("注册时间不同", a["regDate"] != b["regDate"])
    check("IP 不同", a["serverIp"] != b["serverIp"])
    # 同域名二次查询应命中缓存，返回一致
    a2 = dis.get_domain_profile("https://music.163.com")
    eq("同域名结果稳定（缓存命中）", a2["regDate"], a["regDate"])
finally:
    dis.query_rdap, dis.resolve_ip = orig_rdap, orig_ip
    dis._cache.clear()


print("=== 9. 源码残留检查 ===")
here = os.path.dirname(os.path.abspath(__file__))
files = {
    "service/icp_whois_service.py": True,
    "service/domain_info_service.py": True,
}
for rel in files:
    path = os.path.join(here, rel)
    if not os.path.exists(path):
        continue
    src = open(path, encoding="utf-8").read()
    # 剥掉注释后再查（注释里可以提"原来有假数据"）
    import re as _re
    code_only = _re.sub(r'#.*', '', src)
    code_only = _re.sub(r'"""[\s\S]*?"""', '', code_only)
    for marker in ["Demo Registrar Inc.", "某某科技有限公司", "203.0.113.24",
                   "198.51.100.77", "192.0.2.156"]:
        check("%s 代码中无 %s" % (rel, marker), marker not in code_only)


print("\n" + "=" * 52)
print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("\n失败明细：")
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)