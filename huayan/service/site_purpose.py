# -*- coding: utf-8 -*-
"""
service/site_purpose.py —— 站点用途识别（大模型总结）
================================
目标：拿到任意网址后，用一句话讲清楚"这个网站是干什么的、属于什么类型平台"。

流程
----
1) 真实抓取网页公开文本（title / meta 描述 / 正文摘要）
2) 交给大模型做简短总结（OpenAI 兼容接口：MiniMax-M3 / DeepSeek-V4 Pro）
3) 大模型不可用时，降级到本地规则分类器，仍输出一句话用途
4) 抓取失败/超时 → 返回明确的"无法识别"提示，不报错、不炸页面

安全约束（需求要求）
--------------------
- 只抓公开前端文本，不提交表单、不下载文件、不执行脚本
- 加浏览器 UA 伪装，避免被反爬拦截
- 抓取超时上限 12 秒（config.PAGE_PURPOSE_TIMEOUT）
- 短时间高频识别做简单限流（内存级最小间隔）
- 高危链接也照常识别用途，风险结论优先级高于用途（由调用方保证）
"""
import json
import re
import threading
import time
import urllib.request
from urllib.parse import urlparse

import requests

from config import LLM, PAGE_PURPOSE_TIMEOUT

# ============================================================
# 一、常量
# ============================================================
BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/120.0.0.0 Safari/537.36")

# 抓取失败 / 无法识别的统一提示（需求指定的文案）
PURPOSE_UNAVAILABLE = "暂时无法获取页面信息，无法识别站点用途"

# 简单限流：两次站点用途识别的最小间隔（秒）
MIN_PURPOSE_INTERVAL = 0.5
_last_purpose_time = [0.0]
_purpose_lock = threading.Lock()

# LLM 调用的超时（秒）
LLM_TIMEOUT = 15

# 正文摘要最大长度（字符），避免把整页文本塞给模型
MAX_TEXT_LEN = 1200


# ============================================================
# 二、页面文本抓取（真实，独立于 detect_service 的 mock page_meta）
# ============================================================
def _extract_plain_text(html):
    """
    从 HTML 里提取纯文本（title + meta 描述 + 正文摘要）。
    只做正则剥标签，不执行脚本、不解析外部资源。
    """
    if not html:
        return "", ""
    # 去掉 script / style / 注释，这些不是"公开前端文本"
    cleaned = re.sub(r"<(script|style|noscript)[^>]*>.*?</\1>", " ",
                     html, flags=re.I | re.S)
    cleaned = re.sub(r"<!--.*?-->", " ", cleaned, flags=re.S)

    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", cleaned, re.I | re.S)
    if m:
        title = re.sub(r"\s+", " ", m.group(1)).strip()[:200]

    desc = ""
    m = re.search(r"<meta[^>]+name=[\"']description[\"'][^>]+content=[\"'](.*?)[\"']",
                  cleaned, re.I | re.S)
    if not m:
        m = re.search(r"<meta[^>]+content=[\"'](.*?)[\"'][^>]+name=[\"']description[\"']",
                      cleaned, re.I | re.S)
    if m:
        desc = re.sub(r"\s+", " ", m.group(1)).strip()[:300]

    # 正文：把剩余标签都去掉，取纯文本
    body = re.sub(r"<[^>]+>", " ", cleaned)
    # 去掉内联 CSS 块（如 :root{--fixed-top-offset:78px}）——
    # 这些是 style 标签没剥干净时的残留，纯噪音
    body = re.sub(r":root\s*\{[^}]*\}", " ", body)
    body = re.sub(r"[a-zA-Z-]+\s*:\s*[0-9a-zA-Z#%.]+(?:px|em|rem|vh|vw|%)?(?:\s*[;!])", " ", body)
    body = re.sub(r"&[a-z#0-9]+;", " ", body)
    body = re.sub(r"\s+", " ", body).strip()
    return title, desc, body


def fetch_page_text(url):
    """
    真实抓取页面公开文本。

    :return: {"ok": True, "title":..., "description":..., "text":...}
             或 {"ok": False, "reason":...}
    """
    # 简单限流
    with _purpose_lock:
        now = time.time()
        wait = _last_purpose_time[0] + MIN_PURPOSE_INTERVAL - now
        if wait > 0:
            time.sleep(min(wait, 1.0))
        _last_purpose_time[0] = time.time()

    try:
        resp = requests.get(
            url, timeout=PAGE_PURPOSE_TIMEOUT,
            headers={"User-Agent": BROWSER_UA,
                     "Accept": "text/html,application/xhtml+xml",
                     "Accept-Language": "zh-CN,zh;q=0.9"},
            allow_redirects=True,
            verify=False,  # 部分站点证书链在容器/代理下验证失败
            stream=True,
        )
        if resp.status_code != 200:
            return {"ok": False, "reason": "HTTP %d" % resp.status_code}
        # 只读前 200KB，够解析 head + 正文摘要
        raw = resp.raw.read(200000, decode_content=True)
        resp.close()
    except Exception as e:
        return {"ok": False, "reason": str(e)[:80]}

    # 编码探测
    html = None
    for enc in ("utf-8", "gbk", "gb18030", "latin-1"):
        try:
            html = raw.decode(enc)
            break
        except Exception:
            continue
    if html is None:
        return {"ok": False, "reason": "编码无法解析"}

    title, desc, body = _extract_plain_text(html)
    if not title and not desc and not body:
        return {"ok": False, "reason": "页面无文本内容"}

    # 正文摘要截断
    text = body[:MAX_TEXT_LEN]
    return {"ok": True, "title": title, "description": desc, "text": text}


# ============================================================
# 三、大模型总结（OpenAI 兼容接口）
# ============================================================
def _summarize_with_llm(title, desc, text):
    """
    调大模型做简短总结。成功返回一句话（不含前缀），失败返回 None。

    走标准 OpenAI 兼容 /chat/completions，用 urllib 避免额外依赖。
    """
    cfg = LLM
    if not cfg.get("api_url") or not cfg.get("api_key"):
        return None

    # 把抓到的文本拼成精简上下文
    material = []
    if title:
        material.append("网页标题：" + title)
    if desc:
        material.append("网页描述：" + desc)
    if text:
        material.append("网页正文摘要：" + text)
    context = "\n".join(material)[:2000]

    prompt = (
        "请用一句话（20~80字）用大白话说明下面这个网站是干什么的、属于什么类型平台"
        "（例如：音乐平台、视频网站、企业官网、网盘、论坛、工具网站等）。"
        "只输出这句话本身，不要加【站点用途】前缀，不要解释，不要复述网页内容：\n\n"
        + context
    )

    body = {
        "model": cfg.get("model", "MiniMax-M3"),
        "messages": [
            {"role": "system",
             "content": "你是一个网址安全检测工具里的站点用途识别助手。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 200,
    }
    try:
        req = urllib.request.Request(
            cfg["api_url"],
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + cfg["api_key"],
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=LLM_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return None

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return None

    content = (content or "").strip()
    # 去掉模型偶尔自带的前缀
    content = re.sub(r"^【?站点用途】?\s*[:：]?\s*", "", content)
    content = content.strip(" \n\"'")
    if not content:
        return None
    # 硬性长度约束（20~80 字的目标，模型超了就截断）
    if len(content) > 90:
        content = content[:88] + "…"
    return content


# ============================================================
# 四、本地规则降级分类器
# ------------------------------------------------------------
# 大模型不可用时兜底。基于 title/meta/正文/域名做关键词分类，
# 输出仍是一句话，格式与 LLM 一致。
# ============================================================
# 关键词 → (平台类型, 用途描述模板)
RULE_CATEGORIES = [
    # 企业官网（强特征词，放最前：title 含"有限公司/集团/股份"基本就是企业官网）
    (("有限公司", "集团", "股份有限公司", "有限责任公司", "corp", "inc.", "ltd."),
     "企业官网", "属于企业或机构官方站点，用于展示公司业务与品牌"),
    (("音乐", "听歌", "歌曲", "歌单", "无损", "music", "电台"),
     "在线音乐播放平台", "提供歌曲收听、歌单、音乐社区服务"),
    (("视频", "影视", "电影", "电视剧", "综艺", "追剧", "弹幕", "video", "动漫", "直播"),
     "在线视频平台", "提供影视、综艺、动漫等视频观看服务"),
    (("云盘", "网盘", "文件存储", "备份", "同步盘", "drive", "存储"),
     "云存储网盘", "提供文件上传、备份与共享服务"),
    (("邮箱", "邮件", "mail", "电子邮箱"),
     "电子邮箱服务", "提供邮件收发服务"),
    (("文档", "表格", "幻灯片", "在线办公", "协作", "office", "文档编辑", "wps"),
     "在线文档办公平台", "提供文档、表格、幻灯片的在线编辑与协作"),
    (("小说", "阅读", "看书", "电子书", "读书", "网文", "文学"),
     "在线小说阅读平台", "提供小说、电子书在线阅读服务"),
    (("购物", "商城", "电商", "旗舰店", "淘宝", "京东", "拼多多", "下单"),
     "电商购物平台", "提供商品在线购买服务"),
    (("地图", "导航", "出行", "路线", "打车"),
     "地图导航平台", "提供地图查询与出行导航服务"),
    (("翻译", "词典", "译", "translat"),
     "在线翻译工具", "提供多语言在线翻译与词典查询"),
    (("银行", "网银", "转账", "理财", "基金", "证券", "支付"),
     "金融服务平台", "提供银行、支付或理财相关服务"),
    (("论坛", "社区", "贴吧", "讨论", "bbs", "forum", "问答", "知乎"),
     "社区论坛平台", "提供话题讨论、问答与内容交流"),
    (("工具", "在线工具", "转换", "压缩", "生成", "tool", "计算器", "二维码"),
     "在线工具网站", "提供格式转换、压缩、生成等实用工具"),
    (("搜索", "搜索引擎", "search"),
     "搜索引擎", "提供网页信息检索服务"),
    (("招聘", "求职", "找工作", "简历", "job", "人才"),
     "招聘求职平台", "提供职位搜索与简历投递服务"),
    (("新闻", "资讯", "头条", "news", "门户"),
     "新闻资讯网站", "提供新闻与资讯内容"),
    (("社交", "聊天", "朋友圈", "好友", "社交"),
     "社交平台", "提供社交互动服务"),
    (("游戏", "游戏平台", "game", "手游"),
     "游戏平台", "提供游戏下载、资讯或相关服务"),
    (("教育", "课程", "学习", "网课", "培训", "题库", "公开课"),
     "在线教育平台", "提供课程学习与培训服务"),
    (("下载", "软件下载", "资源下载", "download"),
     "软件下载站", "提供软件或资源下载"),
    (("旅游", "机票", "酒店", "火车票", "行程", "travel", "景点"),
     "旅游出行平台", "提供票务预订与旅游服务"),
]


def _clean_subject(title, host):
    """
    取"主语"（一句话里"这是 XX"的 XX）：
    - 优先用标题，但标题常带副标题（"酷狗音乐 - 就是歌多！"），
      只取主标题部分，去掉 " - / | / _ / — " 之后的副标题
    - 标题为空或纯噪音时，退回用域名
    """
    t = (title or "").strip()
    if t:
        # 去掉副标题分隔符后的内容
        t = re.split(r"\s*[-|_—–]\s*", t, maxsplit=1)[0].strip()
        # 去掉常见的站名尾巴（"官网"、"首页" 等重复词）
        t = re.sub(r"(官网|官方网站|首页)$", "", t).strip()
        if t and len(t) >= 2:
            return t[:20]
    h = (host or "").strip()
    return h[:24] if h else "该网站"


def _classify(scope_text):
    """在给定文本里做关键词分类，返回 (platform, usage) 或 None"""
    low = scope_text.lower()
    for keywords, platform, usage in RULE_CATEGORIES:
        for kw in keywords:
            if kw in scope_text or kw.lower() in low:
                return platform, usage
    return None


def _rule_classify(title, desc, text, host):
    """
    基于关键词分类，输出一句话用途。找不到匹配时给保守描述。

    ★ 两段式匹配（避免门户站被误判）：
      阶段1 只在 title + meta 描述（站点的自述，强信号）里匹配；
      阶段2 匹配不到才退到正文（正文常有导航频道词，如门户首页的"音乐"
            链接会把腾讯网误判成音乐平台）。
    """
    subject = _clean_subject(title, host)

    # ★ 门户识别前置：title 形如"腾讯网""新浪网""人民网"（短名 + 网结尾），
    #   这类门户 desc 会罗列"新闻/视频/音乐/游戏"所有频道，直接按分类会
    #   误判成第一个命中的频道。先拦下，判定为综合门户。
    raw_title = re.sub(r"(官网|官方网站|首页)$", "", (title or "").strip()).strip()
    if re.match(r"^[\u4e00-\u9fa5A-Za-z0-9]{2,6}网$", raw_title):
        return "这是%s，是综合门户资讯网站，提供新闻、视频、娱乐等多频道内容。" % subject

    # 阶段1a：标题强匹配（最可信，命中即停，不再看描述）
    hit = _classify(title or "")
    # 阶段1b：meta 描述匹配（次强）
    if hit is None:
        hit = _classify(desc or "")
    # 阶段2：正文摘要匹配（弱信号）
    if hit is None:
        hit = _classify(text or "")

    if hit is not None:
        platform, usage = hit
        return "这是%s，是%s，%s。" % (subject, platform, usage)

    # 无明确分类：用标题兜底
    if title and title.strip():
        return "这是%s，从页面内容看属于一个综合型网站。" % subject
    if host:
        return "这是%s，从公开信息暂无法判断具体业务类型。" % subject
    return PURPOSE_UNAVAILABLE


# ============================================================
# 五、主入口
# ============================================================
def identify_site_purpose(url):
    """
    站点用途识别主入口。

    :param url: 用户提交的网址（含协议）
    :return: 固定格式的一句话用途；
             抓取失败/超时返回 PURPOSE_UNAVAILABLE
    """
    u = (url or "").strip()
    if not u:
        return PURPOSE_UNAVAILABLE

    fetched = fetch_page_text(u)
    if not fetched.get("ok"):
        return PURPOSE_UNAVAILABLE

    title = fetched.get("title") or ""
    desc = fetched.get("description") or ""
    text = fetched.get("text") or ""
    host = (urlparse(u).hostname or "").lower()

    # 优先大模型，失败降级规则
    summary = _summarize_with_llm(title, desc, text)
    if summary:
        # 统一加上【站点用途】前缀
        return "【站点用途】" + summary

    rule = _rule_classify(title, desc, text, host)
    if rule.startswith("【站点用途】"):
        return rule
    if rule == PURPOSE_UNAVAILABLE:
        return rule
    return "【站点用途】" + rule