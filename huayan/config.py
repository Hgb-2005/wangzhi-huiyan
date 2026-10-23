# -*- coding: utf-8 -*-
"""
config.py —— 全局配置
================================
集中管理：运行模式开关、第三方 API 密钥、请求超时、限流参数。
切换 mock / 真实模式只需修改下方 RUN_MODE。
"""
import os

# ============================================================
# 一、运行模式开关
# ============================================================
# "mock" : 调试模式。ICP/Whois/威胁库/页面meta 用模拟数据，
#           但 utils.py 的本地 URL 特征检测仍为真实计算。
#           用于与前端原型联调，默认模式。
# "real" : 真实模式。调用 config 中配置的第三方 API 获取真实数据。
# ★ 线上部署默认切 real：绝不能把演示假数据展示给真实用户。
RUN_MODE = os.getenv("HUAYAN_MODE", "real")

# ============================================================
# 二、第三方能力总开关（真实模式下生效）
#    每一项可独立开启/关闭，关闭后该维度降级为 unknown，不影响其他维度
# ============================================================
THIRD_PARTY = {
    # 短链递归展开
    "expand": {
        "enabled": True,
        "api_url": os.getenv("EXPAND_API_URL", ""),
        "api_key": os.getenv("EXPAND_API_KEY", ""),
    },
    # ICP 备案查询（仅 .cn / .com.cn 等国内域名）
    "icp": {
        "enabled": True,
        "api_url": os.getenv("ICP_API_URL", ""),
        "api_key": os.getenv("ICP_API_KEY", ""),
    },
    # Whois 域名注册信息查询
    "whois": {
        "enabled": True,
        "api_url": os.getenv("WHOIS_API_URL", ""),
        "api_key": os.getenv("WHOIS_API_KEY", ""),
    },
    # 第三方恶意网址安全检测（威胁情报库）
    "threat_intel": {
        "enabled": True,
        "api_url": os.getenv("THREAT_API_URL", ""),
        "api_key": os.getenv("THREAT_API_KEY", ""),
    },
    # 页面元数据抓取
    "page_meta": {
        "enabled": True,
        "api_url": "",  # 直接用 requests 抓取，无需第三方
        "api_key": "",
    },
}

# ============================================================
# 三、请求参数
# ============================================================
REQUEST_TIMEOUT = 8          # 第三方请求统一超时 8 秒
MAX_REDIRECTS = 5            # 短链最大跳转层数上限，超过判定循环跳转
MAX_URLS_PER_REQUEST = 5     # 单次最多检测链接数
RECOMMEND_LIMIT = 5          # 推荐站点单次最多返回数量，默认取 5

# 限流：单 IP 每分钟最多请求次数
RATE_LIMIT_PER_MINUTE = 60

# SSL 证书校验超时（真实模式抓取页面用）
SSL_VERIFY_TIMEOUT = 5

# ============================================================
# 站点用途识别（大模型总结）
# ------------------------------------------------------------
# 走 OpenAI 兼容 /chat/completions 接口（MiniMax-M3 / DeepSeek-V4 Pro 均可）。
# 未配置 api_url/api_key 时，自动降级到本地规则分类器（关键词映射），
# 仍会输出【站点用途】一句话，只是不如大模型精准。
# ============================================================
LLM = {
    "api_url": os.getenv("LLM_API_URL", ""),       # 例 https://api.minimax.io/v1/chat/completions
    "api_key": os.getenv("LLM_API_KEY", ""),
    "model": os.getenv("LLM_MODEL", "MiniMax-M3"),
}

# 站点用途识别的页面抓取超时（秒），需求要求上限 12 秒
PAGE_PURPOSE_TIMEOUT = 12

# ============================================================
# 四、数据库配置
# ============================================================
# SQLite 文件默认放项目目录（发布沙箱支持随项目打包的 SQLite）。
# 若部署环境目录只读，可用 DATABASE_PATH 环境变量指向可写位置兜底。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATABASE_PATH = os.getenv("DATABASE_PATH") or os.path.join(BASE_DIR, "database.db")
SQLALCHEMY_DATABASE_URI = "sqlite:///" + DATABASE_PATH
SQLALCHEMY_TRACK_MODIFICATIONS = False

# ============================================================
# 五、服务端配置
# ============================================================
# 发布平台要求服务监听 $PORT 环境变量并绑定 0.0.0.0。
# 本地开发时未设置 PORT 则回落到 5000，保持原有行为不变。
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "5000"))
# debug 模式会在文件改动时自动重启，联调接口测试时建议关闭避免请求撞上重启窗口
DEBUG = os.getenv("HUAYAN_DEBUG", "0") == "1"

# 允许跨域的前端地址（前端原型跑在 8899 端口）
CORS_ORIGINS = [
    "http://127.0.0.1:8899",
    "http://localhost:8899",
    "http://127.0.0.1:5000",
    "http://localhost:5000",
]

# 风险等级阈值（后端固化判定，前端无权修改）
RISK_CONFIG = {
    "DOMAIN_AGE_SAFE_DAYS": 90,        # 域名注册满 90 天才视为安全期
    "MAX_SUBDOMAIN_DEPTH": 3,          # 子域名层级超过此值视为多层子域名
    "MAX_DOMAIN_LENGTH": 30,           # 域名长度阈值
}

# ============================================================
# 六、统一响应码文案
# ============================================================
CODE_MSG = {
    200: "success",
    400: "参数错误",
    404: "资源不存在",
    429: "请求过于频繁，请稍后再试",
    500: "服务异常",
}