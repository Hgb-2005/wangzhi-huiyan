# -*- coding: utf-8 -*-
"""
service/web_search.py —— 通道B 站点推荐的联网检索能力
================================
解决的问题
----------
原实现从本地 SQLite 的 WebsiteLib 预置演示数据里检索，返回的是
demo-trans.sub 这类**根本不存在的假域名**（DNS 解析失败，浏览器报
DNS_PROBE_FINISHED_NXDOMAIN），用户点开全是死链。

本模块的做法：**候选来源 + 联网实测校验**两道工序
------------------------------------------------
1) 候选来源（三路互补，任意一路可用即可出结果）
   A. 正规站点知识库 CURATED_SITES —— 收录国内主流大厂/官方公共服务站点，
      按主题分组。稳定、无广告捆绑、正规可控，作为高质量基本盘。
   B. Bing 网页搜索 —— 抓 https://www.bing.com/search?q=... 的 HTML，
      从 <li class="b_algo"> 条目里正则提取真实域名与标题。
   C. 搜索引擎不可用时，仅用 A（但仍会做实测校验，不返回死链）

2) 联网实测校验（关键：绝不返回打不开的站点）
   - DNS 解析（socket.gethostbyname）：假域名在这一步直接出局
   - HTTPS/HTTP 实连（HEAD，退化时 GET）：确认真的能打开
   - 任何一项失败 → 该候选直接剔除

3) 合规过滤
   - 命中违禁词库（色情/赌博/盗版/翻墙/外挂等）的域名与标题直接剔除
   - 剔除搜索引擎自身域名、社交/UGC 灌水域名（知乎专栏、贴吧等，
     它们不是"提供该服务的站点"，只是讨论文章）

设计约束
--------
- 纯后端实现，前端不直接跨域调搜索引擎（避免浏览器 CORS 报错）
- 全部第三方异常一律降级，**绝不抛异常崩溃**；网络不通时返回
  {"ok": False, "reason": ...} 让上层给出"联网检索失败"提示，
  而不是塞一个假域名给用户
- 不影响通道A：detect_service.py 完全未被本模块触碰
"""
import json
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request

from config import LLM
from models import db, WebsiteLib

# ============================================================
# 一、网络常量
# ============================================================
# 统一 UA：部分站点对默认 python-urllib UA 直接 403
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

CONNECT_TIMEOUT = 5        # 单站点探测超时（秒）
SEARCH_TIMEOUT = 10        # 搜索引擎抓取超时（秒）
MAX_CANDIDATES = 12        # 参与实测校验的候选上限，避免拖慢响应

# 忽略证书校验：部分国内站点证书链在容器/代理环境下验证失败，
# 但站点本身是真实可访问的。这里只用于"能否打开"的可达性判断，
# 不涉及安全结论（风险判定仍由 detect_service 负责）。
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode = ssl.CERT_NONE

# ============================================================
# 二、剔除名单
# ============================================================
# 搜索引擎/聚合站自身，不该作为"推荐给用户的工具站点"
ENGINE_DOMAINS = {
    "bing.com", "www.bing.com", "cn.bing.com", "go.microsoft.com",
    "baidu.com", "www.baidu.com", "baike.baidu.com",
    "so.com", "www.so.com", "sogou.com", "www.sogou.com",
    "duckduckgo.com", "search.yahoo.com", "yandex.ru",
}

# UGC 讨论站：只是"谈这个话题"的文章页，不是提供该服务的站点
UGC_DOMAINS = {
    "zhihu.com", "www.zhihu.com", "zhuanlan.zhihu.com",
    "tieba.baidu.com", "bbs.csdn.net", "www.csdn.net",
    "juejin.cn", "segmentfault.com", "www.cnblogs.com",
    "jianshu.com", "www.jianshu.com", "51cto.com",
    "wenku.baidu.com", "doc88.com", "docin.com",
    "wikipedia.org", "zh.wikipedia.org", "baike.com",
    "zdic.net", "hanyuguoxue.com", "gushici.net",
}

# 违规/风险关键词：出现在域名或标题里直接剔除（与 site_service 的
# 违禁词库互补，这里额外覆盖境外灰产常见的擦边写法）
RISK_KEYWORDS = [
    "盗版", "未删减", "片库", "免费看大片", "色情", "情色", "成人",
    "av在线", "博彩", "赌博", "百家乐", "澳门", "casino", "彩票预测",
    "破解", "crack", "绿色版", "免授权", "激活工具", "去广告破解",
    "外挂", "辅助器", "自动瞄准", "游戏作弊",
    "翻墙", "梯子", "机场节点", "代理工具", "科学上网",
    "钓鱼", "盗号", "撞库", "密码字典", "社工库",
    "假证", "办假证", "代开发票", "刷单", "刷好评",
    "磁力链接", "bt种子", "私拍", "福利姬", "网盘资源",
]

# 明显不是"正规服务站"的域名后缀（TLD 黑名单）
RISKY_TLDS = {".su", ".top", ".xyz", ".tk", ".ml", ".ga", ".cf", ".gq"}


# ============================================================
# 三、正规站点知识库（通道B 的高质量基本盘）
# ------------------------------------------------------------
# 收录标准：国内可正常访问 + 主体正规（大厂/官方/上市）+ 提供真实服务。
# 这些域名均已实测可达（DNS + HTTPS 200）。所有条目在返回前仍会
# 再跑一次联网实测，所以即便某个站点日后失效也只会少一条结果，
# 不会给用户死链。
# ============================================================
CURATED_SITES = [
    # ---- 音乐 / 音频 ----
    {"name": "QQ音乐", "url": "https://y.qq.com/", "category": "音乐",
     "tags": "听歌,正版音乐,音乐平台", "summary": "腾讯正版音乐平台，在线听歌与无损曲库",
     "scene": "在线听歌,找歌,听歌录歌", "free": True, "hasAd": False},
    {"name": "网易云音乐", "url": "https://music.163.com/", "category": "音乐",
     "tags": "听歌,歌单,音乐平台,评论区", "summary": "网易音乐社区，按歌单与推荐听歌",
     "scene": "听歌,发现新歌,听歌单", "free": True, "hasAd": True},
    {"name": "酷狗音乐", "url": "https://www.kugou.com/", "category": "音乐",
     "tags": "听歌,k歌,正版音乐", "summary": "酷狗音乐在线试听与 K 歌",
     "scene": "在线听歌,k歌录歌", "free": True, "hasAd": True},
    {"name": "酷我音乐", "url": "https://www.kuwo.cn/", "category": "音乐",
     "tags": "听歌,无损音质,音乐平台", "summary": "酷我音乐无损音质在线试听",
     "scene": "在线听歌,无损音质", "free": True, "hasAd": True},
    {"name": "咪咕音乐", "url": "https://music.migu.cn/", "category": "音乐",
     "tags": "听歌,正版音乐,移动音乐", "summary": "中国移动咪咕音乐，正版版权曲库",
     "scene": "在线听歌,正版音乐", "free": True, "hasAd": False},
    {"name": "喜马拉雅", "url": "https://www.ximalaya.com/", "category": "音频",
     "tags": "听书,有声书,广播剧,播客", "summary": "有声书与音频节目平台",
     "scene": "听书,听广播剧,听有声小说", "free": True, "hasAd": True},

    # ---- 小说 / 阅读 ----
    {"name": "起点中文网", "url": "https://www.qidian.com/", "category": "小说",
     "tags": "网络小说,连载,书库", "summary": "阅文集团原创网络小说平台",
     "scene": "看网络小说,追更,找书", "free": True, "hasAd": True},
    {"name": "晋江文学城", "url": "https://www.jjwxc.net/", "category": "小说",
     "tags": "网络小说,原创文学,女性向", "summary": "原创女性向网络文学创作与阅读社区",
     "scene": "看网络小说,找优质原创文", "free": True, "hasAd": True},
    {"name": "红袖读书", "url": "https://www.hongxiu.com/", "category": "小说",
     "tags": "网络小说,原创文学", "summary": "阅文旗下原创小说阅读平台",
     "scene": "看网络小说,找书", "free": True, "hasAd": True},

    # ---- 文档 / 办公 ----
    {"name": "腾讯文档", "url": "https://docs.qq.com/", "category": "文档",
     "tags": "在线文档,表格,幻灯片,协作", "summary": "在线文档表格幻灯片与多人协作",
     "scene": "在线写文档,做表格,做幻灯片,协作编辑", "free": True, "hasAd": False},
    {"name": "WPS Office", "url": "https://www.wps.cn/", "category": "文档",
     "tags": "在线文档,表格,演示,PDF", "summary": "金山 WPS 在线办公套件",
     "scene": "在线文档,编辑PDF,做表格", "free": True, "hasAd": True},
    {"name": "石墨文档", "url": "https://shimo.im/", "category": "文档",
     "tags": "在线文档,协作,知识库", "summary": "在线文档与团队知识库协作工具",
     "scene": "在线协作写文档,团队知识库", "free": True, "hasAd": True},

    # ---- 开发 / 工具 ----
    {"name": "阿里云", "url": "https://www.aliyun.com/", "category": "开发",
     "tags": "云计算,服务器,云主机", "summary": "阿里云云计算与服务器资源",
     "scene": "买服务器,部署网站,云计算", "free": False, "hasAd": True},
    {"name": "腾讯云", "url": "https://cloud.tencent.com/", "category": "开发",
     "tags": "云计算,服务器,云存储", "summary": "腾讯云计算与云存储服务",
     "scene": "买服务器,对象存储,云计算", "free": False, "hasAd": True},
    {"name": "站长工具", "url": "https://tool.chinaz.com/", "category": "工具",
     "tags": "SEO,查询,站长工具", "summary": "域名 Whois、SEO 与网站查询工具",
     "scene": "查域名信息,SEO查询", "free": True, "hasAd": True},
    {"name": "JSON.cn", "url": "https://www.json.cn/", "category": "工具",
     "tags": "JSON,格式化,校验,工具", "summary": "JSON 格式化、校验与转换工具",
     "scene": "格式化JSON,校验JSON格式", "free": True, "hasAd": False},
    {"name": "在线工具", "url": "https://tool.lu/", "category": "工具",
     "tags": "工具箱,在线工具,转换", "summary": "程序员在线工具集合",
     "scene": "在线转换,编码解码,文本工具", "free": True, "hasAd": True},

    # ---- 公共服务 ----
    {"name": "中国铁路12306", "url": "https://www.12306.cn/", "category": "公共服务",
     "tags": "火车票,购票,出行", "summary": "铁路官方购票与行程查询",
     "scene": "买火车票,查车次", "free": True, "hasAd": False},
    {"name": "国家企业信用信息公示系统", "url": "https://www.gsxt.gov.cn/",
     "category": "公共服务",
     "tags": "企业,查询,工商,征信", "summary": "官方企业注册与信用信息公示查询",
     "scene": "查公司信息,企业征信,工商信息", "free": True, "hasAd": False},
    {"name": "中国工商银行", "url": "https://www.icbc.com.cn/", "category": "公共服务",
     "tags": "银行,查询,转账", "summary": "工商银行官方网银与信息查询",
     "scene": "网银,查余额,转账", "free": True, "hasAd": False},
    {"name": "中国人民银行", "url": "https://www.pbc.gov.cn/", "category": "公共服务",
     "tags": "央行,货币政策,金融数据", "summary": "中国人民银行官网与金融统计数据",
     "scene": "查金融数据,货币政策信息", "free": True, "hasAd": False},
    {"name": "国家社会保险公共服务平台", "url": "https://si.12333.gov.cn/",
     "category": "公共服务",
     "tags": "社保,查询,公共服务", "summary": "官方社保信息查询平台",
     "scene": "查社保,社保记录查询", "free": True, "hasAd": False},
    {"name": "教育部政务服务平台", "url": "https://www.moe.gov.cn/",
     "category": "公共服务",
     "tags": "教育,政策,政务", "summary": "教育部官方网站与政务公开",
     "scene": "查教育政策,学历相关政务", "free": True, "hasAd": False},

    # ---- 生活 / 出行 ----
    {"name": "美团", "url": "https://www.meituan.com/", "category": "生活",
     "tags": "外卖,团购,旅游,生活服务", "summary": "外卖、到店与旅游生活服务平台",
     "scene": "点外卖,找优惠,订酒店", "free": True, "hasAd": True},
    {"name": "12306 车次查询", "url": "https://www.12306.cn/index/", "category": "出行",
     "tags": "火车票,出行,查询", "summary": "铁路出行信息查询",
     "scene": "查车次,出行规划", "free": True, "hasAd": False},
    {"name": "高德地图", "url": "https://www.amap.com/", "category": "出行",
     "tags": "地图,导航,出行", "summary": "高德地图导航与出行服务",
     "scene": "导航,查路线,地图", "free": True, "hasAd": True},
    {"name": "百度地图", "url": "https://map.baidu.com/", "category": "出行",
     "tags": "地图,导航,出行", "summary": "百度地图查询与导航",
     "scene": "导航,查路线,周边查询", "free": True, "hasAd": True},

    # ---- 邮箱 / 云盘 ----
    {"name": "QQ邮箱", "url": "https://mail.qq.com/", "category": "邮箱",
     "tags": "邮箱,收发邮件", "summary": "腾讯 QQ 邮箱",
     "scene": "收发邮件,注册邮箱", "free": True, "hasAd": False},
    {"name": "网易邮箱", "url": "https://mail.163.com/", "category": "邮箱",
     "tags": "邮箱,收发邮件", "summary": "网易 163 邮箱",
     "scene": "收发邮件,注册邮箱", "free": True, "hasAd": False},

    # ---- 翻译 ----
    {"name": "百度翻译", "url": "https://fanyi.baidu.com/", "category": "翻译",
     "tags": "翻译,中英翻译,在线翻译,语言", "summary": "百度在线翻译，支持多语种互译",
     "scene": "在线翻译,中译英,英译中,外语学习", "free": True, "hasAd": False},
    {"name": "有道翻译", "url": "https://fanyi.youdao.com/", "category": "翻译",
     "tags": "翻译,中英翻译,词典,语言,单词", "summary": "网易有道在线翻译与词典",
     "scene": "在线翻译,查单词,外语学习,背单词", "free": True, "hasAd": False},
    {"name": "腾讯翻译君", "url": "https://fanyi.qq.com/", "category": "翻译",
     "tags": "翻译,拍照翻译,多语种,语言", "summary": "腾讯在线翻译与拍照翻译",
     "scene": "在线翻译,拍照翻译,外语学习", "free": True, "hasAd": False},

    # ---- 二维码 ----
    {"name": "草料二维码", "url": "https://cli.im/", "category": "二维码",
     "tags": "二维码,生成,二维码生成器,扫码", "summary": "在线二维码生成与美化",
     "scene": "生成二维码,二维码制作,扫码", "free": True, "hasAd": True},
    {"name": "二维码生成器", "url": "https://www.laomei.com/qrcode/", "category": "二维码",
     "tags": "二维码,生成,二维码生成器,条码", "summary": "在线二维码与条码生成工具",
     "scene": "生成二维码,二维码制作,生成条码", "free": True, "hasAd": True},

    # ---- 天气 ----
    {"name": "中国天气网", "url": "https://www.weather.com.cn/", "category": "天气",
     "tags": "天气,预报,温度", "summary": "中国气象局官方天气预报",
     "scene": "查天气,天气预报,查温度", "free": True, "hasAd": False},

    # ---- 学习 ----
    {"name": "中国大学MOOC", "url": "https://www.icourse163.org/",
     "category": "学习",
     "tags": "课程,学习,在线课程,公开课", "summary": "网易中国大学慕课开放课程平台",
     "scene": "在线学习,找课程,学英语,公开课", "free": True, "hasAd": False},
    {"name": "学信网", "url": "https://www.chsi.com.cn/", "category": "学习",
     "tags": "学历,查询,学信,学历查询", "summary": "教育部学历学位在线验证与查询",
     "scene": "学历查询,学位验证,学信报告", "free": True, "hasAd": False},

    # ---- 出行 / 机票 ----
    {"name": "飞猪", "url": "https://www.fliggy.com/", "category": "出行",
     "tags": "机票,旅行,酒店,旅游", "summary": "飞猪旅行，机票与酒店预订",
     "scene": "订机票,订酒店,比价机票,旅游", "free": True, "hasAd": True},
    {"name": "中国国际航空", "url": "https://www.airchina.com.cn/", "category": "出行",
     "tags": "机票,航空,订票", "summary": "中国国际航空公司官网",
     "scene": "订机票,航空公司官网", "free": True, "hasAd": True},

    # ---- 企业信息 ----
    {"name": "爱企查", "url": "https://www.aiqicha.com/", "category": "企业",
     "tags": "企业查询,工商信息,股东,风险", "summary": "企业工商信息与风险查询",
     "scene": "查公司,查股东,企业征信,查风险", "free": True, "hasAd": True},

    # ---- 影视 / 视频 ----
    {"name": "爱奇艺", "url": "https://www.iqiyi.com/", "category": "影视",
     "tags": "视频,影视,电视剧,综艺,电影", "summary": "爱奇艺正版影视视频平台",
     "scene": "看剧,看视频,看电影,追剧", "free": True, "hasAd": True},
    {"name": "腾讯视频", "url": "https://v.qq.com/", "category": "影视",
     "tags": "视频,影视,电视剧,综艺,电影", "summary": "腾讯视频正版影视平台",
     "scene": "看剧,看视频,看电影,追剧", "free": True, "hasAd": True},
    {"name": "优酷", "url": "https://www.youku.com/", "category": "影视",
     "tags": "视频,影视,电视剧,综艺", "summary": "优酷正版视频与影视平台",
     "scene": "看剧,看视频,追剧", "free": True, "hasAd": True},
    {"name": "芒果TV", "url": "https://www.mgtv.com/", "category": "影视",
     "tags": "视频,综艺,电视剧,影视", "summary": "芒果TV综艺与影视平台",
     "scene": "看综艺,看剧,看视频", "free": True, "hasAd": True},
    {"name": "哔哩哔哩", "url": "https://www.bilibili.com/", "category": "视频",
     "tags": "视频,动漫,番剧,弹幕,知识", "summary": "B站正版视频、动漫与知识社区",
     "scene": "看视频,看动漫,追番,学习视频", "free": True, "hasAd": True},

    # ---- 购物 / 电商 ----
    {"name": "京东", "url": "https://www.jd.com/", "category": "购物",
     "tags": "购物,电商,家电,自营", "summary": "京东自营电商，家电数码与日用百货",
     "scene": "买东西,网购,买家电,买数码", "free": True, "hasAd": True},
    {"name": "淘宝", "url": "https://www.taobao.com/", "category": "购物",
     "tags": "购物,电商,百货,零售", "summary": "淘宝综合电商平台",
     "scene": "买东西,网购,淘商品", "free": True, "hasAd": True},
    {"name": "天猫", "url": "https://www.tmall.com/", "category": "购物",
     "tags": "购物,电商,品牌,旗舰店", "summary": "天猫品牌旗舰店电商平台",
     "scene": "买品牌,网购,旗舰店购物", "free": True, "hasAd": True},
    {"name": "拼多多", "url": "https://www.pinduoduo.com/", "category": "购物",
     "tags": "购物,电商,拼团,低价", "summary": "拼多多拼团电商平台",
     "scene": "拼团买东西,网购,淘便宜", "free": True, "hasAd": True},
    {"name": "唯品会", "url": "https://www.vip.com/", "category": "购物",
     "tags": "购物,特卖,品牌折扣,电商", "summary": "唯品会品牌特卖电商平台",
     "scene": "买特价品牌,品牌折扣,网购", "free": True, "hasAd": True},
    {"name": "苏宁易购", "url": "https://www.suning.com/", "category": "购物",
     "tags": "购物,家电,电商,百货", "summary": "苏宁易购家电与百货电商",
     "scene": "买家电,网购,买电器", "free": True, "hasAd": True},

    # ---- 新闻 / 资讯 ----
    {"name": "央视新闻", "url": "https://news.cctv.com/", "category": "新闻",
     "tags": "新闻,资讯,时政,央视", "summary": "中央电视台新闻频道官网",
     "scene": "看新闻,看时事,看资讯", "free": True, "hasAd": False},
    {"name": "新华网", "url": "https://www.xinhuanet.com/", "category": "新闻",
     "tags": "新闻,时政,资讯,权威", "summary": "新华社主办的国家重点新闻网站",
     "scene": "看新闻,查时政,看权威资讯", "free": True, "hasAd": False},
    {"name": "人民网", "url": "https://www.people.com.cn/", "category": "新闻",
     "tags": "新闻,时政,资讯,权威", "summary": "人民日报主办的国家重点新闻网站",
     "scene": "看新闻,看时政,看资讯", "free": True, "hasAd": False},
    {"name": "澎湃新闻", "url": "https://www.thepaper.cn/", "category": "新闻",
     "tags": "新闻,时政,深度报道,资讯", "summary": "澎湃新闻深度时事报道",
     "scene": "看新闻,看深度报道,看时事", "free": True, "hasAd": True},
    {"name": "网易新闻", "url": "https://news.163.com/", "category": "新闻",
     "tags": "新闻,资讯,门户,时事", "summary": "网易新闻综合资讯门户",
     "scene": "看新闻,看资讯,看热点", "free": True, "hasAd": True},

    # ---- 游戏 ----
    {"name": "腾讯游戏", "url": "https://game.qq.com/", "category": "游戏",
     "tags": "游戏,网游,手游,资讯", "summary": "腾讯游戏官网与游戏资讯",
     "scene": "玩游戏,看游戏资讯,下游戏", "free": True, "hasAd": True},
    {"name": "网易游戏", "url": "https://game.163.com/", "category": "游戏",
     "tags": "游戏,网游,手游,资讯", "summary": "网易游戏官网与游戏中心",
     "scene": "玩游戏,看游戏资讯,下游戏", "free": True, "hasAd": True},
    {"name": "4399小游戏", "url": "https://www.4399.com/", "category": "游戏",
     "tags": "游戏,小游戏,网页游戏,休闲", "summary": "4399在线小游戏平台",
     "scene": "玩小游戏,玩网页游戏,休闲游戏", "free": True, "hasAd": True},
    {"name": "TapTap", "url": "https://www.taptap.cn/", "category": "游戏",
     "tags": "游戏,手游,游戏社区,下载", "summary": "TapTap 手游发现与下载平台",
     "scene": "找手游,下游戏,看游戏评测", "free": True, "hasAd": True},
    {"name": "米哈游", "url": "https://www.mihoyo.com/", "category": "游戏",
     "tags": "游戏,二次元,手游,厂商", "summary": "米哈游游戏公司官网",
     "scene": "玩游戏,原神,二次元游戏", "free": True, "hasAd": False},

    # ---- 学习教育 ----
    {"name": "网易公开课", "url": "https://open.163.com/", "category": "学习",
     "tags": "公开课,课程,学习,教育,演讲", "summary": "网易公开课，名校课程与演讲视频",
     "scene": "在线学习,看公开课,看演讲", "free": True, "hasAd": False},
    {"name": "学堂在线", "url": "https://www.xuetangx.com/", "category": "学习",
     "tags": "课程,慕课,学习,教育,公开课", "summary": "清华发起的慕课平台，大学课程在线学习",
     "scene": "在线学习,学课程,慕课,公开课", "free": True, "hasAd": False},
    {"name": "国家中小学智慧教育平台", "url": "https://basic.smartedu.cn/",
     "category": "学习",
     "tags": "教育,课程,中小学,学习,官方", "summary": "教育部中小学智慧教育官方平台",
     "scene": "中小学生学习,找课程,在线教育", "free": True, "hasAd": False},

    # ---- 图片素材 ----
    {"name": "千图网", "url": "https://www.58pic.com/", "category": "图片",
     "tags": "图片,素材,设计,模板,图库", "summary": "设计素材与图片模板库",
     "scene": "找图片素材,找设计模板,找图", "free": True, "hasAd": True},
    {"name": "花瓣网", "url": "https://huaban.com/", "category": "图片",
     "tags": "图片,素材,灵感,设计,采集", "summary": "花瓣网灵感采集与图片素材社区",
     "scene": "找灵感,找图片,采集素材", "free": True, "hasAd": True},

    # ---- 网盘 / 云存储 ----
    {"name": "百度网盘", "url": "https://pan.baidu.com/", "category": "网盘",
     "tags": "网盘,云存储,文件,备份", "summary": "百度网盘个人云存储",
     "scene": "存文件,云盘,网盘,备份文件", "free": True, "hasAd": True},
    {"name": "阿里云盘", "url": "https://www.alipan.com/", "category": "网盘",
     "tags": "网盘,云存储,文件,不限速", "summary": "阿里云盘个人云存储服务",
     "scene": "存文件,云盘,网盘,备份", "free": True, "hasAd": False},
    {"name": "夸克网盘", "url": "https://pan.quark.cn/", "category": "网盘",
     "tags": "网盘,云存储,文件,备份", "summary": "夸克网盘云存储与文件管理",
     "scene": "存文件,云盘,网盘,备份", "free": True, "hasAd": False},

    # ---- 医疗健康 ----
    {"name": "好大夫在线", "url": "https://www.haodf.com/", "category": "医疗",
     "tags": "问诊,挂号,医院,健康,医生", "summary": "好大夫在线预约挂号与在线问诊",
     "scene": "挂号,问诊,找医生,看病", "free": True, "hasAd": True},
    {"name": "丁香医生", "url": "https://dxy.com/", "category": "医疗",
     "tags": "健康,医疗,科普,问诊,医生", "summary": "丁香医生健康科普与在线问诊",
     "scene": "查健康知识,问诊,看医学科普", "free": True, "hasAd": True},
    {"name": "微医", "url": "https://www.guahao.com/", "category": "医疗",
     "tags": "挂号,问诊,医院,健康,预约", "summary": "微医在线挂号与问诊平台",
     "scene": "挂号,预约医生,问诊,看病", "free": True, "hasAd": True},

    # ---- 招聘求职 ----
    {"name": "BOSS直聘", "url": "https://www.zhipin.com/", "category": "招聘",
     "tags": "招聘,求职,找工作,直聊", "summary": "BOSS直聘招聘求职平台",
     "scene": "找工作,求职,投简历,招聘", "free": True, "hasAd": True},
    {"name": "智联招聘", "url": "https://www.zhaopin.com/", "category": "招聘",
     "tags": "招聘,求职,找工作,简历", "summary": "智联招聘求职与招聘平台",
     "scene": "找工作,求职,投简历", "free": True, "hasAd": True},
    {"name": "前程无忧", "url": "https://www.51job.com/", "category": "招聘",
     "tags": "招聘,求职,找工作,简历", "summary": "前程无忧51job求职招聘平台",
     "scene": "找工作,求职,投简历", "free": True, "hasAd": True},

    # ---- 二手交易 ----
    {"name": "闲鱼", "url": "https://www.goofish.com/", "category": "二手",
     "tags": "二手,闲置,交易,买卖", "summary": "闲鱼二手闲置交易平台",
     "scene": "卖闲置,买二手,二手交易", "free": True, "hasAd": True},
    {"name": "转转", "url": "https://www.zhuanzhuan.com/", "category": "二手",
     "tags": "二手,闲置,交易,回收", "summary": "转转二手交易与回收平台",
     "scene": "卖闲置,买二手,回收旧物", "free": True, "hasAd": True},

    # ---- 财经 / 股票 ----
    {"name": "东方财富", "url": "https://www.eastmoney.com/", "category": "财经",
     "tags": "股票,基金,行情,财经,理财", "summary": "东方财富财经行情与股票基金",
     "scene": "看行情,查股票,买基金,看财经", "free": True, "hasAd": True},
    {"name": "同花顺", "url": "https://www.10jqka.com.cn/", "category": "财经",
     "tags": "股票,行情,理财,财经,投资", "summary": "同花顺股票行情与投资工具",
     "scene": "看行情,查股票,炒股,理财", "free": True, "hasAd": True},
    {"name": "雪球", "url": "https://xueqiu.com/", "category": "财经",
     "tags": "股票,投资,理财,社区,财经", "summary": "雪球投资者社区与行情",
     "scene": "看投资观点,查股票,交流投资", "free": True, "hasAd": True},

    # ---- 生活服务 ----
    {"name": "饿了么", "url": "https://www.ele.me/", "category": "生活",
     "tags": "外卖,点餐,生活服务,送餐", "summary": "饿了么外卖订餐平台",
     "scene": "点外卖,订餐,点餐", "free": True, "hasAd": True},
    {"name": "滴滴出行", "url": "https://www.didiglobal.com/", "category": "出行",
     "tags": "打车,网约车,出行,叫车", "summary": "滴滴出行网约车平台",
     "scene": "打车,叫车,网约车,出行", "free": True, "hasAd": True},
    {"name": "携程旅行", "url": "https://www.ctrip.com/", "category": "出行",
     "tags": "旅行,机票,酒店,旅游,火车票", "summary": "携程旅行，机票酒店火车票预订",
     "scene": "订机票,订酒店,旅游,订火车票", "free": True, "hasAd": True},
    {"name": "去哪儿", "url": "https://www.qunar.com/", "category": "出行",
     "tags": "机票,酒店,旅游,旅行,火车票", "summary": "去哪儿旅行，机票酒店比价预订",
     "scene": "订机票,订酒店,旅游,查低价", "free": True, "hasAd": True},

    # ---- 软件下载 ----
    {"name": "腾讯软件中心", "url": "https://pc.qq.com/", "category": "下载",
     "tags": "软件,下载,应用,装机", "summary": "腾讯软件中心，软件官方下载",
     "scene": "下载软件,下载应用,装机", "free": True, "hasAd": True},
    {"name": "华军软件园", "url": "https://www.onlinedown.net/", "category": "下载",
     "tags": "软件,下载,应用,装机", "summary": "华军软件园，软件下载与资讯",
     "scene": "下载软件,下载应用,找软件", "free": True, "hasAd": True},

    # ---- 协同办公 ----
    {"name": "飞书", "url": "https://www.feishu.cn/", "category": "办公",
     "tags": "协同办公,文档,表格,协作,即时通讯", "summary": "字节跳动飞书协同办公套件",
     "scene": "协同办公,在线文档,团队协作", "free": True, "hasAd": False},
    {"name": "钉钉", "url": "https://www.dingtalk.com/", "category": "办公",
     "tags": "协同办公,企业,会议,通讯,管理", "summary": "阿里巴巴钉钉企业协同办公平台",
     "scene": "企业办公,视频会议,协同协作", "free": True, "hasAd": False},
    {"name": "语雀", "url": "https://www.yuque.com/", "category": "文档",
     "tags": "文档,知识库,笔记,协作", "summary": "蚂蚁语雀知识库与文档协作",
     "scene": "写文档,知识库,团队笔记", "free": True, "hasAd": False},

    # ---- 社交 / 短视频 ----
    {"name": "微博", "url": "https://www.weibo.com/", "category": "社交",
     "tags": "社交,微博,热搜,资讯", "summary": "新浪微博社交媒体平台",
     "scene": "刷微博,看热搜,关注热点", "free": True, "hasAd": True},
    {"name": "抖音", "url": "https://www.douyin.com/", "category": "视频",
     "tags": "短视频,视频,娱乐,直播", "summary": "抖音短视频平台",
     "scene": "刷短视频,看视频,看直播", "free": True, "hasAd": True},
]


# ============================================================
# 四、需求文本 → 检索关键词
# ============================================================
# 搜索引擎对口语化短需求（"听音乐"）会做字面拆词，结果被"听"字的
# 字典/百科条目淹没。这里把原始需求映射到**有明确服务指向**的检索词，
# 让搜索引擎返回真正的站点首页。
INTENT_HINTS = [
    # (触发词, 扩展检索词)
    ("听音乐", "音乐网站 在线听歌 播放器"),
    ("听歌", "音乐网站 在线听歌 播放器"),
    ("音乐", "音乐网站 在线听歌 播放器"),
    ("k歌", "在线k歌 唱歌网站"),
    ("听书", "有声书 听书平台"),
    ("有声", "有声书 听书平台"),
    ("看小说", "小说阅读网 在线看书"),
    ("小说", "小说阅读网 在线看书"),
    ("读书", "电子书阅读 在线看书"),
    ("看书", "电子书阅读 在线看书"),
    ("文档", "在线文档 表格 协作办公"),
    ("表格", "在线表格 文档 办公"),
    ("翻译", "在线翻译 中英翻译"),
    ("地图", "地图 导航"),
    ("导航", "地图 导航"),
    ("天气", "天气预报"),
    ("外卖", "外卖 点餐"),
    ("打车", "打车 网约车"),
    ("机票", "机票查询 机票预订"),
    ("火车票", "火车票 购票"),
    ("电影", "正版影视 在线购票"),
    ("视频", "视频网站 在线视频"),
    ("图片", "图片素材 图库"),
    ("图片压缩", "图片压缩 在线工具"),
    ("二维码", "二维码生成 在线工具"),
    ("邮箱", "免费邮箱 注册"),
    ("网盘", "云盘 网盘"),
    ("股票", "股票行情 查询"),
    ("医院", "医院 挂号"),
    ("学校", "教育 政务公开"),
    ("公司查询", "企业信息 查询 工商"),
    ("企业查询", "企业信息 查询 工商"),
    ("公司信息", "企业信息 查询 工商"),
    ("股东", "企业信息 查询 工商"),
    ("工商", "企业信息 查询 工商"),
    ("征信", "企业信用 信息查询"),
    ("学英语", "英语学习 在线网站"),
    ("英语", "英语学习 在线网站"),
    ("学习", "在线学习 教育网站"),
    ("背单词", "英语单词 在线学习"),
    ("截图", "截图工具 在线"),
    ("购物", "购物网站 电商平台"),
    ("网购", "购物网站 电商平台"),
    ("买东西", "购物网站 电商平台"),
    ("看新闻", "新闻网站 资讯门户"),
    ("新闻", "新闻网站 资讯门户"),
    ("玩游戏", "游戏平台 游戏官网"),
    ("游戏", "游戏平台 游戏官网"),
    ("网盘", "云盘 网盘"),
    ("云盘", "云盘 网盘"),
    ("找工作", "招聘网站 求职平台"),
    ("求职", "招聘网站 求职平台"),
    ("招聘", "招聘网站 求职平台"),
    ("二手", "二手交易 闲置平台"),
    ("股票", "股票行情 财经"),
    ("基金", "基金 理财 财经"),
    ("理财", "理财 财经 投资"),
    ("看病", "在线问诊 挂号平台"),
    ("挂号", "在线挂号 医院预约"),
    ("问诊", "在线问诊 挂号"),
    ("图片素材", "图片素材 设计图库"),
    ("找图", "图片素材 设计图库"),
    ("短视频", "短视频 视频平台"),
]

# 通用兜底后缀：让搜索引擎倾向于返回"提供服务的网站"而非释义页
GENERIC_SUFFIX = " 官网"


def build_search_query(text):
    """把用户需求转成更适合搜索引擎的检索词"""
    t = (text or "").strip()
    if not t:
        return ""
    for key, expanded in INTENT_HINTS:
        if key in t:
            return expanded
    # 没命中映射：原样 + 官网倾向
    return t + GENERIC_SUFFIX


# ============================================================
# 五、域名工具
# ============================================================
def _host_of(url):
    """从 URL 里取小写域名；取不到返回 None"""
    try:
        p = urllib.parse.urlparse(url if "//" in url else "http://" + url)
        host = (p.hostname or "").lower().strip(".")
        return host or None
    except Exception:
        return None


def _normalize_url(url):
    """补全协议、统一小写域名"""
    u = (url or "").strip()
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = "https://" + u.lstrip("/")
    return u


def _registrable(host):
    """取可注册主域（简单实现：末两段；.com.cn 等二级后缀特殊处理）"""
    parts = (host or "").split(".")
    if len(parts) <= 2:
        return host
    two = ".".join(parts[-2:])
    if two in ("com.cn", "net.cn", "org.cn", "gov.cn", "edu.cn", "ac.cn"):
        return ".".join(parts[-3:])
    return two


def is_blocked_domain(host, title=""):
    """判断域名/标题是否该被剔除"""
    if not host:
        return True
    reg = _registrable(host)

    # 搜索引擎自身：只做**精确主机名**匹配，不做注册域匹配。
    # 因为像 pan.baidu.com（百度网盘）、map.baidu.com（百度地图）这类
    # 合法服务子域，其注册域也是 baidu.com，若按注册域匹配会把它们
    # 一并误杀（历史 bug：云盘推荐时百度网盘被当成搜索引擎剔除）。
    if host in ENGINE_DOMAINS:
        return True

    # UGC 讨论站：做注册域匹配即可（zhihu.com / jianshu.com 等整个站
    # 都是"谈这个话题"的内容，任何子域都不该作为服务站点推荐）。
    if host in UGC_DOMAINS or reg in UGC_DOMAINS:
        return True

    # 高风险后缀（.su/.top 等灰色顶级域，demo 假域名也集中在这里）
    for tld in RISKY_TLDS:
        if host.endswith(tld) or reg.endswith(tld):
            return True
    # IP 直连的站点不作为推荐
    if re.match(r"^\d{1,3}(\.\d{1,3}){3}$", host):
        return True
    # 违规关键词（域名 + 标题一起看）
    hay = (host + " " + (title or "")).lower()
    for kw in RISK_KEYWORDS:
        if kw.lower() in hay:
            return True
    return False


# ============================================================
# 六、联网可达性实测（假域名在这一步全部出局）
# ============================================================
def probe_site(url):
    """
    实测站点是否真的能打开。
    :return: (ok:bool, final_url:str, level:str)
             level ∈ reachable / http_error / dns_fail / conn_fail
    """
    u = _normalize_url(url)
    host = _host_of(u)
    if not host:
        return False, u, "dns_fail"

    # URL 里就带着 404/500/error 之类路径的，直接判死（Bing 经常把
    # 站内 404 页、报错页当成检索结果返回，例如
    # https://mail.sina.com.cn/error/404.html）
    if re.search(r"/(error|errors|404|403|500|not[-_]?found)\b",
                 urllib.parse.urlparse(u).path, re.I):
        return False, u, "http_error"

    # 验证码 / 登录 / 反爬中间页：服务器活着，但用户点开只会看到
    # 验证码，不是"可用的服务站点"。
    # 注意不能加 \b 词边界 —— 实际路径是 /px/captcha-v2/ind 这种
    # "captcha" 夹在两段中间的形式，加了边界反而匹配不上
    if re.search(r"/(captcha|verify|login|signin|sign-in|antibot|challenge)",
                 urllib.parse.urlparse(u).path, re.I):
        return False, u, "http_error"

    # 第 1 关：DNS 解析 —— demo-trans.sub 这类假域名在此直接出局
    try:
        socket.gethostbyname(host)
    except Exception:
        return False, u, "dns_fail"

    # 第 2 关：HTTP(S) 实连
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(
                u, headers={"User-Agent": UA,
                            "Accept": "text/html,application/xhtml+xml"},
                method=method)
            with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT,
                                        context=_SSL_CTX) as r:
                code = r.getcode()
                final = r.geturl()
            # 只认 2xx/3xx：说明服务器活着且这个 URL 真的返回内容。
            # 4xx/5xx 都不算"可用站点"——用户点过去看到的是报错页，
            # 那和假域名死链一样没意义。
            if code and 200 <= code < 400:
                final = _normalize_url(final)
                # 重定向可能把我们带到验证码/登录/报错页 —— 那不是可用的
                # 服务站点，必须拦掉（例：tianxun.com/sttc/px/... 会 302
                # 到 /sttc/px/captcha-v2/index.html）
                fpath = urllib.parse.urlparse(final).path or "/"
                if re.search(r"/(captcha|verify|login|signin|sign-in|"
                             r"antibot|challenge|error|404)", fpath, re.I):
                    return False, u, "http_error"
                if fpath.rstrip("/") in ("", "/index.html", "/index.htm",
                                         "/index.php", "/index.jsp"):
                    final = "https://%s/" % host
                return True, final, "reachable"
            return False, u, "http_error"
        except urllib.error.HTTPError as e:
            # 4xx/5xx 都算"打不开这个页面"，不算可用站点
            return False, u, "http_error"
        except Exception:
            continue

    return False, u, "conn_fail"


# ============================================================
# 七、搜索引擎抓取（Bing）
# ============================================================
def _search_bing(query):
    """抓 Bing 搜索结果，返回 [{name,url}]；失败返回 []"""
    url = ("https://www.bing.com/search?q=" + urllib.parse.quote(query)
           + "&ensearch=0&count=20")
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    })
    try:
        with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT,
                                    context=_SSL_CTX) as r:
            html = r.read().decode("utf-8", "ignore")
    except Exception:
        return []

    if len(html) < 5000:      # 明显是反爬页/空页
        return []

    items = re.findall(r'<li class="b_algo".*?</li>', html, re.S)
    out = []
    for it in items:
        m = re.search(
            r'<h2>\s*<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', it, re.S)
        if not m:
            continue
        link = m.group(1).strip()
        # 标题里常混进换行/制表符，直接压成单行，否则后面拼卡片会错位
        title = re.sub(r"<[^>]+>", "", m.group(2))
        title = re.sub(r"\s+", " ", title).strip()
        # HTML 实体反转义（&#183; &amp; 等），否则会原样显示给用户
        title = re.sub(r"&(amp|lt|gt|quot|#39|nbsp|#160|hellip|mdash|"
                       r"middot|laquo|raquo);",
                       lambda mm: {"amp": "&", "lt": "<", "gt": ">",
                                   "quot": '"', "#39": "'", "nbsp": " ",
                                   "#160": " ", "hellip": "…",
                                   "mdash": "—", "middot": "·",
                                   "laquo": "«", "raquo": "»"}
                       .get(mm.group(1), mm.group(0)), title)
        if not link or not title:
            continue
        out.append({"name": title[:40], "url": _normalize_url(link)})
    return out


# ============================================================
# 七·b：大模型生成候选站点（本地库不足时补充）
# ------------------------------------------------------------
# 让大模型直接输出"匹配需求的正规国内站点"（name + url），
# 再由调用方逐个实测连通性 + 风险检测，合格才入库/展示。
# 未配置 LLM 时返回 []，由调用方降级到 Bing 搜索。
# ============================================================
def _llm_generate_sites(text):
    """
    调大模型生成候选站点列表（JSON 数组 [{name, url}]）。
    :return: [{"name":..., "url":...}, ...]；失败/未配置返回 []
    """
    cfg = LLM
    if not cfg.get("api_url") or not cfg.get("api_key"):
        return []

    prompt = (
        "用户需求：%s\n\n"
        "请推荐 5 个能满足该需求的【国内正规、合法、大众常用】网站，"
        "要求：①只推荐国内可直接访问的正规大站/官方站，不得推荐境外、盗版、"
        "色情、赌博、翻墙、破解、钓鱼类站点；②每个站点给「网站名称」和「官网首页URL」；"
        "③严格按下面 JSON 数组格式输出，不要输出任何解释、不要加代码块围栏：\n"
        '[{"name":"网站名","url":"https://官网首页"}, ...]'
    ) % text

    body = {
        "model": cfg.get("model", "MiniMax-M3"),
        "messages": [
            {"role": "system",
             "content": "你是网址安全工具里的站点推荐助手，只输出合规的国内正规网站。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 600,
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
        with urllib.request.urlopen(req, timeout=SEARCH_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", "ignore"))
    except Exception:
        return []

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        return []

    # 从模型输出里抠出 JSON 数组（模型偶尔会带围栏或前后缀）
    m = re.search(r"\[[\s\S]*\]", content or "")
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []

    out = []
    for it in arr:
        if not isinstance(it, dict):
            continue
        name = str(it.get("name") or "").strip()
        url = _normalize_url(str(it.get("url") or ""))
        if not name or not url:
            continue
        out.append({"name": name[:40], "url": url})
    return out


def _looks_like_article_page(url):
    """
    判断是否只是"讲解文章页"而不是站点首页。
    搜索引擎经常把专栏文章排前面（如 oi-wiki.org/geometry/2d/），
    推荐"能提供这个服务的站点"应该给站点首页。
    """
    p = urllib.parse.urlparse(url)
    path = (p.path or "").rstrip("/")
    if not path:
        return False
    depth = len([x for x in path.split("/") if x])
    # 深路径（/a/123456、/p/2785521 这类）基本是文章页
    if depth >= 2:
        return True
    # 单段但不是首页语义的常见文章路径
    seg = path.split("/")[-1].lower()
    if re.match(r"^(p|article|posts?|blog|news|ask|question|"
                r"zhuanlan)\b", seg) or re.match(r"^\d+$", seg):
        return True
    return False


def fetch_page_meta(url):
    """
    抓取站点真实页面标题与简介。
    搜索引擎给的标题常常是"XX - 搜索"或文章标题，
    抓真实 <title>/<meta description> 更贴近站点本身，
    推荐卡片显示的信息质量更好。
    :return: (title:str, desc:str)
    """
    u = _normalize_url(url)
    try:
        req = urllib.request.Request(u, headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=CONNECT_TIMEOUT,
                                    context=_SSL_CTX) as r:
            raw = r.read(120000)     # 只读前 120KB，够解析 head 了
            try:
                charset = r.headers.get_content_charset() or "utf-8"
            except Exception:
                charset = "utf-8"
    except Exception:
        return "", ""

    try:
        html = raw.decode(charset, "ignore")
    except Exception:
        html = raw.decode("utf-8", "ignore")

    # <title>
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.S | re.I)
    if m:
        title = re.sub(r"\s+", " ",
                       re.sub(r"<[^>]+>", "", m.group(1))).strip()

    # <meta name="description" content="...">
    # 兼容 name/content 顺序颠倒的写法
    desc = ""
    m = re.search(
        r'<meta[^>]+(?:name=["\']description["\'][^>]*content=["\']([^"\']+)'
        r'|content=["\']([^"\']+)["\'][^>]*name=["\']description["\'])',
        html, re.I)
    if m:
        desc = re.sub(r"\s+", " ", (m.group(1) or m.group(2) or "")).strip()

    return title[:60], desc[:80]


# ============================================================
# 八、知识库检索（按分类/标签/场景匹配需求）
# ============================================================
# 同义词：把用户的口语表达映射到知识库的分类/场景词。
# 没有这一层时"找公司股东信息"这类需求会因为字面不匹配而零召回，
# 最终误判成"联网检索失败"。
SYNONYMS = {
    "音乐": ["听歌", "听音乐", "歌曲", "音乐", "听"],
    "音频": ["听书", "有声", "播客", "广播"],
    "小说": ["看小说", "看书", "读书", "小说", "网文"],
    "文档": ["文档", "表格", "幻灯片", "ppt", "word", "excel", "办公"],
    "邮箱": ["邮箱", "邮件", "mail", "email"],
    "公共服务": ["公司查询", "企业", "工商", "股东", "征信", "查公司",
                 "医院", "挂号", "社保", "教育", "政务", "央行", "银行"],
    "工具": ["翻译", "二维码", "截图", "json", "工具", "转换", "格式化"],
    "生活": ["外卖", "点餐", "美团", "团购", "旅游"],
    "翻译": ["翻译", "译", "中译英", "英译中", "外语", "词典", "查单词"],
    "二维码": ["二维码", "扫码", "二维码生成", "生成二维码"],
    "天气": ["天气", "天气预报", "气温", "下雨", "温度"],
    "学习": ["学英语", "英语", "学习", "课程", "网课", "公开课", "背单词",
             "单词", "教育", "学历", "学位", "学信"],
    "出行": ["机票", "航班", "订票", "旅行", "旅游", "酒店", "出行",
             "打车", "火车票", "导航", "地图"],
    "企业": ["公司", "企业", "工商", "股东", "征信", "查公司", "法人",
             "风险", "企业查询"],
    "影视": ["看剧", "看视频", "看电影", "电视剧", "综艺", "追剧", "影视",
             "视频", "追番", "动漫"],
    "视频": ["看视频", "短视频", "直播", "看动漫", "追番", "动漫", "弹幕"],
    "购物": ["购物", "买东西", "网购", "电商", "商城", "淘宝", "天猫",
             "拼单", "比价", "买家电", "买数码", "特卖", "折扣"],
    "新闻": ["新闻", "资讯", "时事", "时政", "热点", "看新闻", "看资讯"],
    "游戏": ["游戏", "玩游戏", "手游", "网游", "小游戏", "网页游戏",
             "下游戏", "游戏资讯", "二次元"],
    "图片": ["图片", "素材", "图库", "设计", "模板", "找图", "灵感"],
    "网盘": ["网盘", "云盘", "云存储", "存文件", "备份", "网盘资源"],
    "医疗": ["看病", "挂号", "问诊", "医生", "医院", "健康", "用药",
             "查症状", "预约挂号"],
    "招聘": ["找工作", "求职", "招聘", "投简历", "面试", "职位", "招聘信息"],
    "二手": ["二手", "闲置", "回收", "买卖闲置", "淘二手"],
    "财经": ["股票", "基金", "行情", "理财", "投资", "炒股", "财经",
             "看盘", "涨跌"],
    "社交": ["社交", "微博", "热搜", "短视频", "刷视频"],
    "办公": ["协同办公", "办公", "会议", "企业通讯", "团队协作", "钉钉", "飞书"],
    "下载": ["下载软件", "下载应用", "软件下载", "装机", "找软件"],
}


def _expand_with_synonyms(text):
    """
    把需求文本里出现的同义词收集起来（只收集，不改原文本）。
    返回的扩展词仅用于**弱命中**（低权重），不能与原需求的命中等权，
    否则一个高频词（如"听"）会把不相关领域的站点也拉进来。
    """
    extra = set()
    for group in SYNONYMS.values():
        for w in group:
            if w and w in text:
                extra.add(w)
    return extra


def _score_candidates(text, sites):
    """
    对一组站点按"与需求的相关度"打分，返回按分数降序的站点列表。
    数据源无关：既能喂硬编码 CURATED_SITES，也能喂本地库 WebsiteLib。
    """
    t = (text or "").strip()
    if not t:
        return []
    extra = _expand_with_synonyms(t)

    # 分类级语义匹配：文本命中某同义词组（组名 == 站点 category 名）时，
    # 该分类下的**所有**站点都给一个基础分。
    # 解决纯字面匹配召不回的问题："看电影"命不中 tags 里没有"电影"二字
    # 的优酷/芒果TV；"云盘"命不中 tags 写"网盘"的百度网盘/夸克；
    # "买家电"命不中 tags 没有"家电"的淘宝/天猫。
    # 只用 ≥2 字的词，避免"听""译"这类单字误伤。
    cat_hits = set()
    for cat, words in SYNONYMS.items():
        for w in words:
            if w and len(w) >= 2 and w in t:
                cat_hits.add(cat)
                break

    scored = []
    for s in sites:
        score = 0
        weak = 0
        # tags 可能是字符串（CURATED_SITES）也可能是列表（WebsiteLib.to_dict）
        tags = s.get("tags") if isinstance(s.get("tags"), str) else \
            ",".join(s.get("tags") or [])
        hay = " ".join([s.get("name") or "", s.get("category") or "",
                        tags, s.get("summary") or "", s.get("scene") or ""])

        # ---- 强匹配：只看用户原始输入 ----
        if s.get("name") and s["name"] in t:
            score += 100
        if s.get("category") and s["category"] in t:
            score += 60
        for field in (tags, s.get("scene") or "", s.get("summary") or ""):
            for token in re.split(r"[,，\s/、]+", field):
                if len(token) >= 2 and token in t:
                    score += 12
        for token in re.split(r"[\s,，。?？!！、]+", t):
            if len(token) >= 2 and token in hay:
                score += 8

        # ---- 分类级语义匹配：中等权重（低于 name/category 字面命中，高于弱匹配）----
        if score == 0 and s.get("category") and s["category"] in cat_hits:
            score += 40

        # ---- 弱匹配：同义词扩展，权重低，只有强匹配为空时才用 ----
        if score == 0:
            hay_parts = set(re.split(r"[,，、/\s]+", hay))
            for w in extra:
                if w in hay_parts:
                    weak += 6
            score = weak

        if score > 0:
            scored.append((score, s))

    scored.sort(key=lambda x: -x[0])
    return [s for _, s in scored]


def _search_curated(text):
    """
    在硬编码 CURATED_SITES 里按需求匹配候选。
    注：主检索流程已改用 _search_local（WebsiteLib 本地库，支持管理员增删），
    本函数仅作为「绕过本地库、直接查硬编码表」的调试/对照入口保留，
    正常运行路径不会调用它。
    """
    return _score_candidates(text, CURATED_SITES)


# ============================================================
# 八·b：本地站点库（WebsiteLib 持久化）
# ------------------------------------------------------------
# 检索优先级：先查本地库（管理员可手动增删），本地不够再联网/大模型补充。
# ============================================================
def _local_sites():
    """
    从 WebsiteLib 读出所有启用站点，转成与 CURATED_SITES 同构的 dict 列表。
    tags 用逗号字符串，free/hasAd 对齐 CURATED_SITES 的字段名。
    """
    try:
        rows = WebsiteLib.query.filter(WebsiteLib.is_active.is_(True)).all()
    except Exception:
        return []
    out = []
    for r in rows:
        out.append({
            "id": r.id,
            "name": r.name,
            "url": r.url,
            "category": r.category or "",
            "tags": r.tags or "",
            "summary": r.summary or "",
            "scene": r.scene or "",
            "free": bool(r.is_free),
            "hasAd": bool(r.has_ad),
            "source": "本地库",
        })
    return out


def _search_local(text):
    """在本地库 WebsiteLib 里按需求匹配候选"""
    return _score_candidates(text, _local_sites())


def ensure_local_lib():
    """
    启动时同步本地站点库（幂等）：
      1) 清除旧的 demo 假数据（url 含 "demo"，如 demo-trans.sub / demo-draw.tools）
      2) 把硬编码的真实站点 CURATED_SITES **增量**同步进 WebsiteLib：
         只补 url 尚不存在的条目，已存在的（含用户手动添加的）不动、不覆盖。

    这样每次扩充 CURATED_SITES 后重启即生效，无需删库重建，
    也不会覆盖管理员手动添加/修改过的站点。
    """
    try:
        # 清掉旧 demo 假数据
        demo_rows = WebsiteLib.query.filter(WebsiteLib.url.like("%demo%")).all()
        if demo_rows:
            for r in demo_rows:
                db.session.delete(r)
            db.session.commit()

        # 增量同步：把库里还没有的 CURATED_SITES 条目补进去
        existing_urls = {r.url for r in WebsiteLib.query.all()}
        added = 0
        for s in CURATED_SITES:
            if s["url"] in existing_urls:
                continue
            db.session.add(WebsiteLib(
                name=s["name"], url=s["url"], category=s["category"],
                tags=s["tags"], summary=s["summary"], scene=s["scene"],
                is_free=s.get("free", True), has_ad=s.get("hasAd", False),
                history_level="safe",
            ))
            existing_urls.add(s["url"])
            added += 1
        if added:
            db.session.commit()
    except Exception:
        db.session.rollback()


def _auto_store(site):
    """
    把"联网/大模型新发现的合格站点"自动写入本地库（自动入库）。
    已存在（按 url 去重）则跳过。入库失败静默，不影响本次推荐返回。
    """
    try:
        url = (site.get("url") or "").strip()
        if not url:
            return
        exists = WebsiteLib.query.filter(WebsiteLib.url == url).first()
        if exists:
            return
        db.session.add(WebsiteLib(
            name=(site.get("name") or "")[:128],
            url=url[:512],
            category=(site.get("category") or "联网收录")[:64],
            tags=(site.get("tags") or "联网收录")[:255],
            summary=(site.get("summary") or "由联网检索自动收录并通过可访问性与风险校验")[:512],
            scene=(site.get("scene") or "")[:255],
            is_free=bool(site.get("free", True)),
            has_ad=bool(site.get("hasAd", False)),
            history_level=site.get("historyLevel") or "safe",
        ))
        db.session.commit()
    except Exception:
        db.session.rollback()


# ============================================================
# 九、主入口：联网检索站点
# ============================================================
def _relevance_ok(user_text, title, url):
    """
    搜索引擎结果的相关度门槛。

    Bing 的中文检索质量不稳，会返回两类垃圾结果：
      1) 明显不相关（搜"打折机票"返回折扣计算器）
      2) 与查询无关的缓存结果（搜"翻译"返回学信网）
    判定规则（任一命中即认为相关）：
      - 标题/域名里含需求里的核心词
      - 需求里的词出现在站点名或域名中
    不满足的直接剔除，宁可少给几条也不给错的结果。
    """
    hay = (title + " " + url).lower()
    # 抽取需求里的有效词（去虚词，长度≥2）
    # STOP 里必须含"免费/无广告/中文"这类**通用修饰词**：
    # 它们在任何站点标题里都可能出现（"免费在线游戏"），
    # 用它们做匹配会导致完全无关的站点被放进来
    STOP = {"我想", "我要", "帮我", "找一个", "找", "哪里", "有没有",
            "推荐", "一个", "可以", "在线", "网站", "工具", "怎么", "如何",
            "免费", "无广告", "中文", "好用", "正规", "靠谱", "能", "的",
            "什么", "哪个", "哪些", "给我", "帮忙", "求"}
    words = [w for w in re.split(r"[\s,，。?？!！、/]+", user_text)
             if len(w) >= 2 and w not in STOP]
    if not words:
        return True      # 无法拆词时不拦，避免误杀

    # 核心词命中即通过
    for w in words:
        if w in hay:
            return True

    # 整词都不命中时，看是否有多字词部分重叠（如"机票"vs"机票查询"）
    for w in words:
        if len(w) >= 3 and (w[:2] in hay or w[-2:] in hay):
            return True
    return False


def search_recommend_sites(text, limit=3):
    """
    通道B 主检索逻辑（联网）。

    流程：
      1) 违禁校验（复用 site_service 的词库，由调用方传入判断结果，
         这里只做域名/标题层面的合规过滤）
      2) 三路候选：知识库匹配 → Bing 搜索（带优化检索词）→ 合并
      3) 逐个联网实测，剔除打不开的
      4) 同主域去重，返回最多 limit 条

    :return: dict
        {
          "ok": True,  "sites": [...],
          "ok": False, "reason": "no_candidate" | "network_fail", "msg": str
        }
    失败时 **绝不返回任何站点**，由上层展示"联网检索失败"提示，
    不会给用户假域名。
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "reason": "empty", "msg": "请先描述你的需求"}

    network_touched = False   # 是否至少成功触达过一次外部网络
    local_hits = []           # 本地库命中（质量最高，排最前）
    llm_hits = []             # 大模型补充（本地库不足时生成，已实测+入库）
    web_hits = []             # 搜索引擎命中（最后兜底）

    # ---- 来源 A：本地库（WebsiteLib，管理员可手动增删）----
    try:
        local_hits = _search_local(text)
    except Exception:
        local_hits = []

    # ---- 来源 B：大模型生成补充（本地库不够时）----
    # 让大模型输出合规正规站点，逐个做连通性 + 合规校验，
    # 通过的**自动写入本地库**（自动入库）。
    if len(local_hits) < limit:
        try:
            for item in _llm_generate_sites(text):
                host = _host_of(item.get("url") or "")
                if not host or is_blocked_domain(host, item.get("name") or ""):
                    continue
                ok, final_url, _level = probe_site(item["url"])
                if not ok:
                    continue
                network_touched = True
                entry = {
                    "name": item["name"],
                    "url": final_url,
                    "category": "大模型收录",
                    "tags": "大模型收录",
                    "summary": "由大模型推荐，已通过域名解析与可访问性校验",
                    "scene": text,
                    "free": True,
                    "hasAd": False,
                    "source": "大模型收录",
                }
                _auto_store(entry)   # 自动入库
                llm_hits.append(entry)
        except Exception:
            pass

    # ---- 来源 C：Bing 联网搜索（本地库 + 大模型仍不够时兜底）----
    if len(local_hits) + len(llm_hits) < limit:
        query = build_search_query(text)
        if query:
            try:
                for item in _search_bing(query):
                    network_touched = True
                    # 相关度门槛：Bing 会返回不相关/缓存结果，先挡掉
                    if not _relevance_ok(text, item["name"], item["url"]):
                        continue
                    # 只保留站点首页类结果，讲解文章页不算"可用的服务站点"
                    if _looks_like_article_page(item["url"]):
                        continue
                    web_hits.append({
                        "name": item["name"],
                        "url": item["url"],
                        "category": "联网检索",
                        "tags": "联网检索结果",
                        "summary": "由搜索引擎实时检索，并已通过域名解析与可访问性校验",
                        "scene": query,
                        "free": None,
                        "hasAd": None,
                    })
            except Exception:
                pass

    # 本地库优先 → 大模型补充 → 搜索引擎兜底
    candidates = local_hits + llm_hits + web_hits

    if not candidates:
        return {
            "ok": False,
            "reason": "no_candidate",
            "msg": "联网检索失败，暂时无法推荐站点",
        }

    # ---- 去重（按主域）并按顺序截断 ----
    seen = set()
    unique = []
    for c in candidates:
        host = _host_of(c.get("url") or "")
        if not host:
            continue
        if is_blocked_domain(host, c.get("name") or ""):
            continue
        reg = _registrable(host)
        if reg in seen:
            continue
        seen.add(reg)
        unique.append(c)
        if len(unique) >= MAX_CANDIDATES:
            break

    if not unique:
        return {
            "ok": False,
            "reason": "no_candidate",
            "msg": "联网检索失败，暂时无法推荐站点",
        }

    # ---- 逐个联网实测，打不开的直接剔除 ----
    verified = []
    for c in unique:
        ok, final_url, _level = probe_site(c["url"])
        if not ok:
            continue

        network_touched = True
        item = dict(c)
        item["url"] = final_url

        # 搜索引擎来源的条目：用站点真实 <title>/<description> 覆盖，
        # 避免把"XX - 搜索"或文章标题直接展示给用户
        if item.get("category") == "联网检索":
            try:
                real_title, real_desc = fetch_page_meta(final_url)
            except Exception:
                real_title, real_desc = "", ""
            if real_title:
                # 去掉站点名后的分隔后缀（"QQ音乐-音乐-...-搜索"）
                clean = re.split(r"\s*[-|_]\s*", real_title)[0].strip()
                item["name"] = clean[:24] or item["name"]
            if real_desc:
                item["summary"] = real_desc
            else:
                item["summary"] = "通过域名解析与可访问性校验的联网检索结果"

        # 免费/广告未知时给出保守缺省，避免前端渲染出错误标签
        if item.get("free") is None:
            item["free"] = True
        if item.get("hasAd") is None:
            item["hasAd"] = False
        verified.append(item)

        if len(verified) >= limit:
            break

    if not verified:
        # 一个都打不开 —— 明确失败，不返回假域名
        return {
            "ok": False,
            "reason": "network_fail" if network_touched else "no_candidate",
            "msg": "联网检索失败，暂时无法推荐站点",
        }

    return {"ok": True, "sites": verified[:limit]}