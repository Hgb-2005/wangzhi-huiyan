# -*- coding: utf-8 -*-
"""
models.py —— SQLAlchemy 数据库模型
================================
4 张数据表：
1. detect_history   检测历史记录
2. website_lib      可信站点库（推荐通道数据源，预置示范站点）
3. feedback         用户反馈记录
4. whitelist        URL 白名单
程序首次运行自动建表，无需手动导入 SQL。
"""
from datetime import datetime

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


# ============================================================
# 表1：检测历史记录
# ============================================================
class DetectHistory(db.Model):
    __tablename__ = "detect_history"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    original_url = db.Column(db.String(1024), nullable=False, index=True)   # 用户提交的原始链接
    final_url = db.Column(db.String(1024))                                 # 短链展开后的真实地址
    domain = db.Column(db.String(255), index=True)                         # 主域名
    risk_level = db.Column(db.String(20), index=True)                      # safe/suspicious/danger/unknown
    risk_label = db.Column(db.String(20))                                  # 中文标签
    risk_count = db.Column(db.Integer, default=0)                          # 风险线索数量
    is_overseas = db.Column(db.Boolean, default=False)                     # 是否境外服务器
    icp_subject = db.Column(db.String(255))                                # 备案主体名称
    expand_hops = db.Column(db.Integer, default=0)                         # 跳转层数
    source = db.Column(db.String(20), default="api")                       # 来源：api/recommend
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)  # 检测时间

    def to_dict(self):
        return {
            "id": self.id,
            "originalUrl": self.original_url,
            "finalUrl": self.final_url,
            "domain": self.domain,
            "riskLevel": self.risk_level,
            "riskLabel": self.risk_label,
            "riskCount": self.risk_count,
            "isOverseas": self.is_overseas,
            "icpSubject": self.icp_subject,
            "expandHops": self.expand_hops,
            "source": self.source,
            "createdAt": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


# ============================================================
# 表2：可信站点库（通道B 推荐通道的数据源）
# ============================================================
class WebsiteLib(db.Model):
    __tablename__ = "website_lib"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(128), nullable=False)                       # 网站名称
    url = db.Column(db.String(512), nullable=False, unique=True)           # 访问地址
    category = db.Column(db.String(64), index=True)                        # 分类标签
    tags = db.Column(db.String(255))                                       # 逗号分隔的关键词标签
    summary = db.Column(db.String(512))                                    # 一句话简介
    scene = db.Column(db.String(255))                                      # 适用场景
    is_free = db.Column(db.Boolean, default=True)                          # 是否免费
    has_ad = db.Column(db.Boolean, default=False)                          # 有无广告
    history_check_time = db.Column(db.DateTime)                            # 上次安全检测时间
    history_level = db.Column(db.String(20), default="safe")               # 历史安全评级
    is_active = db.Column(db.Boolean, default=True)                        # 是否启用
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "category": self.category,
            "tags": [t for t in (self.tags or "").split(",") if t],
            "summary": self.summary,
            "scene": self.scene,
            "free": self.is_free,
            "hasAd": self.has_ad,
            "historyCheckTime": self.history_check_time.strftime("%Y-%m-%d") if self.history_check_time else None,
            "historyLevel": self.history_level,
        }


# ============================================================
# 表3：用户反馈记录
# ============================================================
class Feedback(db.Model):
    __tablename__ = "feedback"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    type = db.Column(db.String(32), nullable=False, index=True)            # invalid/tooManyAds/phishing/badRecommend/other
    url = db.Column(db.String(1024))                                       # 对应链接
    site_id = db.Column(db.Integer)                                        # 对应站点ID（推荐反馈时）
    message = db.Column(db.String(1024))                                   # 用户补充留言
    status = db.Column(db.String(20), default="pending", index=True)       # pending/reviewed/ignored
    handled_at = db.Column(db.DateTime)                                    # 人工复核时间
    created_at = db.Column(db.DateTime, default=datetime.now, index=True)

    def to_dict(self):
        return {
            "id": self.id,
            "type": self.type,
            "url": self.url,
            "siteId": self.site_id,
            "message": self.message,
            "status": self.status,
            "createdAt": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


# ============================================================
# 表4：URL 白名单（可选扩展功能）
# ============================================================
class Whitelist(db.Model):
    __tablename__ = "whitelist"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    url = db.Column(db.String(1024), nullable=False, unique=True)
    domain = db.Column(db.String(255), index=True)
    note = db.Column(db.String(512))
    created_at = db.Column(db.DateTime, default=datetime.now)

    def to_dict(self):
        return {
            "id": self.id,
            "url": self.url,
            "domain": self.domain,
            "note": self.note,
            "createdAt": self.created_at.strftime("%Y-%m-%d %H:%M:%S") if self.created_at else None,
        }


def init_db(app):
    """
    启动时自动建表。

    ★ 站点库（website_lib）的种子数据已改为真实站点，
      由 service/web_search.py 的 ensure_local_lib() 负责写入
      （首次启动时把 CURATED_SITES 真实站点同步进库）。
      这里不再写入旧的 demo 假域名（demo-trans.sub 等）。
    """
    with app.app_context():
        db.create_all()