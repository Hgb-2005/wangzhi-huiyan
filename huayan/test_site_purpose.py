# -*- coding: utf-8 -*-
"""
test_site_purpose.py —— 站点用途识别专项测试
================================
验证：
  1. 规则分类器（title → desc → text 三段式，标题优先）
  2. 标题清洗（去副标题、去"官网"尾巴）
  3. 门户识别（"XX网" 形态不误判成频道）
  4. 企业官网识别
  5. 抓取失败兜底文案
  6. LLM 不可用时自动降级规则分类
  7. 无演示假数据残留
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from service import site_purpose as sp

PASS = []
FAIL = []


def check(label, cond, detail=""):
    if cond:
        PASS.append(label)
    else:
        FAIL.append(label)
        print("  FAIL %s  %s" % (label, detail))


def eq(label, got, want):
    check(label, got == want, "=> %r (期望 %r)" % (got, want))


# ============================================================
print("=== 1. 标题清洗 _clean_subject ===")
C = sp._clean_subject
eq("去副标题", C("酷狗音乐 - 就是歌多！小说相声也很多！", "kugou.com"), "酷狗音乐")
eq("去竖线副标题", C("网易云音乐 | 发现好音乐", "163.com"), "网易云音乐")
eq("去官网尾巴", C("腾讯官网", "qq.com"), "腾讯")
check("长标题截断到20字",
      len(C("这是一个非常非常非常长的网站标题啊啊啊啊啊", "x.com")) == 20)
eq("空标题用域名", C("", "baidu.com"), "baidu.com")


print("=== 2. 分类器 _classify 三段式 ===")
K = sp._classify
# 标题命中音乐
check("标题命中音乐", K("网易云音乐") == ("在线音乐播放平台",
                                        "提供歌曲收听、歌单、音乐社区服务"))
# desc 命中视频
check("描述命中视频", K("爱奇艺 在线视频平台") == ("在线视频平台",
                                                 "提供影视、综艺、动漫等视频观看服务"))
# 无匹配
eq("无匹配返回 None", K("随便写点无关内容"), None)


print("=== 3. 门户识别（XX网 不误判）===")
R = sp._rule_classify
r = R("腾讯网", "腾讯网提供新闻视频游戏等综合内容", "", "qq.com")
check("腾讯网判综合门户", "综合门户" in r, r)
r = R("新浪网", "新闻资讯", "", "sina.com.cn")
check("新浪网判综合门户", "综合门户" in r, r)


print("=== 4. 企业官网识别 ===")
r = R("某某科技有限公司", "专业从事软件开发", "", "example.com")
check("有限公司判企业官网", "企业官网" in r, r)
r = R("中国工商银行中国网站", "个人网上银行", "", "icbc.com.cn")
check("银行 title 优先判金融", "金融服务" in r, r)


print("=== 5. 抓取失败兜底 ===")
eq("空 URL", sp.identify_site_purpose(""), sp.PURPOSE_UNAVAILABLE)
# 模拟抓取失败
orig_fetch = sp.fetch_page_text
sp.fetch_page_text = lambda u: {"ok": False, "reason": "模拟抓取失败"}
try:
    eq("抓取失败返回兜底文案",
       sp.identify_site_purpose("https://x.com"), sp.PURPOSE_UNAVAILABLE)
finally:
    sp.fetch_page_text = orig_fetch


print("=== 6. LLM 不可用自动降级规则 ===")
# LLM 未配置（api_url 为空）→ _summarize_with_llm 返回 None
eq("无 api_url 时 LLM 返回 None",
   sp._summarize_with_llm("标题", "描述", "正文"), None)


print("=== 7. 输出格式校验 ===")
# _rule_classify 是内部函数，返回不带前缀的纯一句话；
# 【站点用途】前缀由 identify_site_purpose 统一加
rule = sp._rule_classify("网易云音乐", "音乐平台", "", "music.163.com")
check("_rule_classify 不带前缀", not rule.startswith("【站点用途】"), rule)
check("含'这是'主语", "这是" in rule, rule)

# 模拟抓取成功，验证 identify_site_purpose 加前缀
orig_fetch = sp.fetch_page_text
sp.fetch_page_text = lambda u: {"ok": True, "title": "网易云音乐",
                                "description": "音乐平台", "text": ""}
try:
    full = sp.identify_site_purpose("https://music.163.com")
    check("identify 带【站点用途】前缀", full.startswith("【站点用途】"), full)
    check("长度合理", 10 <= len(full) <= 120, "len=%d" % len(full))
finally:
    sp.fetch_page_text = orig_fetch


print("=== 8. 源码残留检查 ===")
here = os.path.dirname(os.path.abspath(__file__))
src = open(os.path.join(here, "service", "site_purpose.py"),
           encoding="utf-8").read()
import re as _re
code_only = _re.sub(r'#.*', '', src)
code_only = _re.sub(r'"""[\s\S]*?"""', '', code_only)
for marker in ["Demo Registrar", "某某科技", "演示数据", "203.0.113"]:
    check("代码无 %s" % marker, marker not in code_only)


print("\n" + "=" * 52)
print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("\n失败明细：")
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)