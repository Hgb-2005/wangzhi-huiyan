# -*- coding: utf-8 -*-
"""
test_site_lib.py —— 站点库扩充专项测试
================================
验证：
  1. 评分逻辑 _score_candidates（本地库/硬编码两种数据源同构）
  2. 同义词扩展 _expand_with_synonyms
  3. 本地库种子导入 ensure_local_lib（清 demo + 写真实站点）
  4. 本地库检索 _search_local
  5. 自动入库 _auto_store（url 去重）
  6. LLM 未配置返回空 _llm_generate_sites
  7. 手动添加校验 add_site（合法/打不开/违规）
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from flask import Flask

from config import SQLALCHEMY_DATABASE_URI
from models import db, WebsiteLib
from service import web_search as ws
from service import site_service as ss

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
print("=== 1. _score_candidates 评分逻辑 ===")
sites = [
    {"name": "QQ音乐", "url": "https://y.qq.com/", "category": "音乐",
     "tags": "听歌,正版音乐", "summary": "在线音乐", "scene": "听歌"},
    {"name": "爱奇艺", "url": "https://www.iqiyi.com/", "category": "影视",
     "tags": "视频,电视剧", "summary": "看剧", "scene": "看视频"},
]
r = ws._score_candidates("听音乐", sites)
eq("音乐需求命中QQ音乐第一", r[0]["name"] if r else None, "QQ音乐")
eq("音乐需求只命中1个", len(r), 1)

# tags 是列表也能处理（WebsiteLib.to_dict 返回列表）
sites2 = [dict(s, tags=["视频", "电视剧"]) for s in sites]
r = ws._score_candidates("看视频", sites2)
eq("tags为列表时也能评分", r[0]["name"] if r else None, "爱奇艺")


print("=== 2. _expand_with_synonyms ===")
# "听音乐" 扩展出同义词"音乐"（用于匹配含"音乐"字段的站点），
# 而不是"听歌"——"听歌"是另一个需求词，不是"听音乐"的同义扩展
check("听音乐扩展出音乐", "音乐" in ws._expand_with_synonyms("听音乐"))


print("=== 3. LLM 未配置返回空 ===")
eq("LLM 未配置 _llm_generate_sites 返回 []", ws._llm_generate_sites("听音乐"), [])


# ============================================================
# 用内存数据库测本地库读写
# ============================================================
app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db.init_app(app)

with app.app_context():
    db.create_all()

    print("=== 4. ensure_local_lib 种子导入 ===")
    ws.ensure_local_lib()
    cnt = WebsiteLib.query.count()
    check("种子导入后库非空", cnt > 0, "count=%d" % cnt)
    check("无 demo 假数据", WebsiteLib.query.filter(
        WebsiteLib.url.like("%demo%")).count() == 0)
    # 幂等：再调一次不重复
    ws.ensure_local_lib()
    eq("ensure_local_lib 幂等", WebsiteLib.query.count(), cnt)

    print("=== 5. _search_local 本地库检索 ===")
    r = ws._search_local("听音乐")
    check("本地库能命中音乐站点", len(r) > 0)
    check("命中站点 source=本地库", all(s.get("source") == "本地库" for s in r))

    print("=== 6. _auto_store 自动入库（去重）===")
    before = WebsiteLib.query.count()
    ws._auto_store({"name": "新站A", "url": "https://new-a.example.com/",
                    "category": "工具", "tags": "测试", "summary": "测试入库",
                    "scene": "", "free": True, "hasAd": False})
    after1 = WebsiteLib.query.count()
    eq("新站点入库成功", after1, before + 1)
    # 重复入库（同 url）不新增
    ws._auto_store({"name": "新站A改名", "url": "https://new-a.example.com/",
                    "category": "工具", "tags": "测试", "summary": "重复",
                    "scene": "", "free": True, "hasAd": False})
    eq("同 url 重复入库被去重", WebsiteLib.query.count(), after1)

    print("=== 7. add_site 手动添加校验 ===")
    # mock 掉联网探测与风险检测
    ws.probe_site = lambda u: (True, "https://good.example.com/", "reachable")
    orig_detect = None
    try:
        from service import detect_service
        orig_detect = detect_service.detect_url
        detect_service.detect_url = lambda u, **kw: {
            "level": "safe", "label": "安全", "reason": "未检出风险", "risks": []}

        # 7a 合法站点
        r = ss.add_site("测试站点", "https://good.example.com/",
                        "工具,测试", "这是一句备注", "工具")
        eq("合法站点添加成功", r.get("ok"), True)

        # 7b 打不开（probe 失败）
        ws.probe_site = lambda u: (False, u, "dns_fail")
        r = ss.add_site("打不开", "https://dead.example.com/", "", "", "")
        check("打不开的站点被拒绝", r.get("ok") is False and "无法访问" in (r.get("msg") or ""))

        # 7c 违规词
        ws.probe_site = lambda u: (True, "https://good.example.com/", "reachable")
        r = ss.add_site("盗版电影站", "https://good.example.com/",
                        "盗版", "", "")
        check("违规站点被拒绝", r.get("ok") is False and "违规" in (r.get("msg") or ""))

        # 7d 高危站点被拒绝（name 不含违规词，专门测 detect_url 的高危分支）
        detect_service.detect_url = lambda u, **kw: {
            "level": "danger", "label": "高危", "reason": "钓鱼", "risks": []}
        r = ss.add_site("某普通站点", "https://good.example.com/", "", "", "")
        check("高危站点被拒绝", r.get("ok") is False and "高危" in (r.get("msg") or ""),
              str(r))
    finally:
        ws.probe_site = ws.probe_site  # noop
        if orig_detect is not None:
            from service import detect_service
            detect_service.detect_url = orig_detect


print("\n" + "=" * 52)
print("通过 %d / 失败 %d" % (len(PASS), len(FAIL)))
if FAIL:
    print("\n失败明细：")
    for f in FAIL:
        print("  -", f)
sys.exit(1 if FAIL else 0)