# -*- coding: utf-8 -*-
"""
接口契约验证脚本 —— 逐条核对 /接口对接说明.md 中的契约
运行：python verify_api.py
"""
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:5000/api/v1"
PASS, FAIL = [], []


def call(method, path, payload=None):
    url = BASE + path
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        return {"code": -1, "msg": "请求失败: %s" % e, "data": {}}


def one(url):
    """
    调用 detect 并取回单条结果。
    接口契约：单条检测时 data.results 就是对象本身（不是数组），
    这里统一兼容两种形态，避免测试取值路径出错。
    """
    r = call("POST", "/url/detect", {"url": url})
    d = r.get("data", {})
    res = d.get("results", d)
    if isinstance(res, list):
        res = res[0] if res else {}
    return res


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print("%s %s %s" % ("[PASS]" if cond else "[FAIL]", name, detail if not cond else ""))


def has_keys(d, keys):
    return isinstance(d, dict) and all(k in d for k in keys)


print("=" * 66)
print("  网址慧眼 后端接口契约验证")
print("=" * 66)

# ---------- 1. 统一响应格式 ----------
print("\n【1】统一响应格式 {code, msg, data}")
r = call("GET", "/health")
check("响应含 code/msg/data", has_keys(r, ["code", "msg", "data"]))
check("code=200 表示成功", r.get("code") == 200)

# ---------- 2. 短链展开 ----------
print("\n【2】POST /url/expand 短链递归展开")
r = call("POST", "/url/expand", {"url": "https://account-verify.demo-login.top/signin"})
d = r.get("data", {})
check("返回 original/final/chain/exceeded",
      has_keys(d, ["original", "final", "chain", "exceeded"]), str(d)[:120])
check("chain 为数组", isinstance(d.get("chain"), list))
check("展开出真实目标地址", d.get("final") is not None, str(d.get("final")))

# 循环跳转
r = call("POST", "/url/expand", {"url": "https://loop.demo-redirect.top/"})
d = r.get("data", {})
check("循环跳转识别 exceeded=True", d.get("exceeded") is True, str(d)[:120])

# 参数错误
r = call("POST", "/url/expand", {"url": "这不是网址"})
check("非法URL返回 code=400", r.get("code") == 400, str(r)[:120])
r = call("POST", "/url/expand", {})
check("缺参数返回 code=400", r.get("code") == 400)

# ---------- 3. 核心检测接口 ----------
print("\n【3】POST /url/detect 五维检测（核心接口）")
res = one("http://0k.cn-security-check.top/login")
check("返回五维检测结构",
      has_keys(res, ["level", "localFeatures", "domainInfo", "icp",
                     "threatIntel", "pageMeta", "risks"]),
      str(list(res.keys())))
check("形近钓鱼站判定为 danger", res.get("level") == "danger", str(res.get("level")))
check("返回风险清单", len(res.get("risks", [])) > 0, str(res.get("risks")))
check("风险等级为四级之一",
      res.get("level") in ("safe", "suspicious", "danger", "unknown"))

# localFeatures 契约字段
lf = res.get("localFeatures", {})
check("localFeatures 字段完整",
      has_keys(lf, ["ipDirect", "lookalikeDomain", "domainLength",
                    "sensitivePathHits", "protocol", "sslStatus", "subdomainDepth"]),
      str(list(lf.keys())))
check("形近混淆真实计算命中", lf.get("lookalikeDomain") is True, str(lf.get("lookalikeDetail"))[:80])
check("敏感路径真实计算命中", len(lf.get("sensitivePathHits", [])) > 0,
      str(lf.get("sensitivePathHits")))
check("HTTP 明文真实计算命中", lf.get("isHttps") is False)

# 正常站
res = one("https://demo-notebook.org/")
check("正常站判定为 safe", res.get("level") == "safe", str(res.get("level")))

# 无法访问
res = one("https://unreachable.demo-site.top/")
check("无法访问判定为 unknown", res.get("level") == "unknown", str(res.get("level")))
check("unknown 时页面简介标注抓取失败",
      res.get("degradeNote") is not None, str(res.get("degradeNote")))

# 循环跳转站
res = one("https://loop.demo-redirect.top/")
check("循环跳转站为 suspicious/danger",
      res.get("level") in ("suspicious", "danger"), str(res.get("level")))

# 批量 + 上限
r = call("POST", "/url/detect", {"url": ["https://demo-notebook.org/",
                                          "https://demo-qr.net/"]})
check("批量检测返回多条", r.get("data", {}).get("total") == 2, str(r.get("data", {}).get("total")))
r = call("POST", "/url/detect", {"url": ["a%d.com" % i for i in range(6)]})
check("超过5条返回400", r.get("code") == 400, str(r)[:100])

# ---------- 4. 站点推荐 ----------
print("\n【4】POST /site/search 站点检索 + 强制复测")
r = call("POST", "/site/search", {"keyword": "流程图", "limit": 3})
d = r.get("data", {})
check("返回 sites 数组", isinstance(d.get("sites"), list))
check("推荐数量不超过上限", len(d.get("sites", [])) <= 3)
if d.get("sites"):
    s = d["sites"][0]
    check("站点含 liveLevel 实时复测字段",
          has_keys(s, ["id", "name", "url", "summary", "free", "hasAd",
                       "historyLevel", "liveLevel"]),
          str(list(s.keys())))
    check("liveLevel 已实际复测", s.get("rechecked") is True)
    check("高危站已过滤（无danger）",
          all(x.get("liveLevel") != "danger" for x in d["sites"]))
    check("返回 recheckCount 说明复测数量", d.get("recheckCount", 0) > 0)

# 违禁拦截
r = call("POST", "/site/search", {"keyword": "免费看盗版电影"})
d = r.get("data", {})
check("违禁需求被拒绝", d.get("refused") is True, str(d)[:120])
check("返回固定拒绝话术",
      d.get("msg") == "很抱歉，我无法为你提供该类站点推荐，该需求不符合规范，请更换合法合规需求。")

for kw in ["翻墙工具", "博彩网站", "刷单平台", "假证办理"]:
    r = call("POST", "/site/search", {"keyword": kw})
    check("违禁拦截: %s" % kw, r.get("data", {}).get("refused") is True)

# 缺参数
r = call("POST", "/site/search", {})
check("缺 keyword 返回400", r.get("code") == 400)

# ---------- 5. 反馈 ----------
print("\n【5】POST /feedback/submit 反馈上报")
r = call("POST", "/feedback/submit",
         {"type": "phishing", "url": "http://0k.cn-security-check.top/",
          "message": "这个链接骗钱的"})
d = r.get("data", {})
check("返回 feedbackId", d.get("feedbackId") is not None, str(d))
check("返回人工复核说明（不承诺即时）",
      "定期" in d.get("note", "") and "人工" in d.get("note", ""), d.get("note", ""))
r = call("POST", "/feedback/submit", {"type": "badRecommend", "message": "推荐不相关"})
check("无链接反馈也可提交", r.get("code") == 200, str(r)[:100])
r = call("POST", "/feedback/submit", {"type": "非法类型", "url": "https://a.com"})
check("非法 type 返回400", r.get("code") == 400)
r = call("GET", "/feedback/list?size=5")
check("反馈列表可查", r.get("data", {}).get("total", 0) >= 2,
      str(r.get("data", {}).get("total")))

# ---------- 6. 历史记录 ----------
print("\n【6】GET /history/list 历史记录分页")
r = call("GET", "/history/list?page=1&size=10")
d = r.get("data", {})
check("返回分页结构", has_keys(d, ["total", "page", "size", "items"]), str(list(d.keys())))
check("检测已写入历史", d.get("total", 0) > 0, str(d.get("total")))
if d.get("items"):
    it = d["items"][0]
    check("历史项含 riskLevel/riskLabel/createdAt",
          has_keys(it, ["originalUrl", "riskLevel", "riskLabel", "createdAt"]),
          str(list(it.keys())))

r = call("GET", "/history/list?level=danger")
check("按风险等级筛选生效",
      all(i["riskLevel"] == "danger" for i in r.get("data", {}).get("items", [])),
      str(r.get("data", {}).get("total")))
r = call("GET", "/history/list?page=2&size=3")
check("分页参数生效", r.get("data", {}).get("page") == 2)

# ---------- 7. 白名单 ----------
print("\n【7】白名单（扩展功能）")
# 幂等处理：先确保不在白名单中，否则首次添加成功、重复添加返回400
_wl_url = "https://demo-notebook.org/"
call("POST", "/history/whitelist", {"url": _wl_url})
r = call("POST", "/history/whitelist", {"url": _wl_url})
check("重复添加返回400", r.get("code") == 400, str(r)[:100])
r = call("GET", "/history/whitelist")
check("白名单列表可查", r.get("data", {}).get("total", 0) >= 1)

# ---------- 8. 降级健壮性 ----------
print("\n【8】异常与降级健壮性")
res = one("https://this-domain-really-not-exist-xyz123.com/")
check("不存在的域名不崩溃", r.get("code") == 200, str(r)[:100])
check("返回合法风险等级",
      res.get("level") in ("safe", "suspicious", "danger", "unknown"), str(res.get("level")))

r = call("POST", "/url/detect", {"url": ""})
check("空URL返回400", r.get("code") == 400)
res = one("http://192.168.1.1:8080/admin")
check("IP直连被本地特征识别", res.get("localFeatures", {}).get("ipDirect") is True)

res = one("http://a-b-c-d-e-very-long-subdomain.example.download.exe")
check("可执行文件后缀触发高危",
      res.get("level") == "danger", str(res.get("level")))

# ---------- 汇总 ----------
print("\n" + "=" * 66)
print("  通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    print("\n  失败清单：")
    for f in FAIL:
        print("   - %s" % f)
print("=" * 66)
sys.exit(1 if FAIL else 0)