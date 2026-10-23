# 网址慧眼（WangZhi HuiYan）

双向智能安全助手：既能**检测网址风险**，也能**按需求推荐正规站点**。

> 在线体验：https://wangzhi-huiyan.app.workbuddy.host/?sharecode=CGIKTUYQRP

## 核心能力

### 通道 A · 网址风险检测
输入一个网址，自动完成全套安全研判：

- **跳转链路解析**：追踪短链/重定向的完整链路，识别循环跳转
- **域名档案**：实时查询注册商、注册/到期时间、服务器 IP 与属地（RDAP + DNS + SSL）
- **SSL 证书**：校验颁发方、有效期、域名匹配
- **ICP 备案**：国内域名备案主体查询，境外域名如实标注
- **风险评级**：安全 / 可疑 / 高危 / 未知，附风险线索说明
- **站点用途识别**：一句话说明「这个网站是干什么的」（大模型总结，降级规则分类兜底）

### 通道 B · 需求推荐站点
不输入网址，直接描述需求（如「听音乐」「看小说」「找工作」）：

- 本地站点库优先，不够时联网检索 + 大模型补充
- 每个候选都做 **DNS + HTTP 连通性实测**，打不开的直接剔除
- 高危 / 盗版 / 违规链接自动拦截
- 单次最多返回 5 个，同平台去重

### 其它

- 历史记录、收藏夹、批量检测、TXT 报告导出
- 深色 / 浅色主题自适应
- 全接口超时保护 + 限流

## 目录结构

```
├── huayan/               # Flask 后端（含 static/ 前端，单端口部署）
│   ├── app.py            # 入口：同时服务 API 和前端页面
│   ├── config.py         # 配置（运行模式、LLM、超时、限流）
│   ├── models.py         # SQLAlchemy 数据模型
│   ├── routes/           # 路由蓝图（/api/v1/*）
│   ├── service/          # 业务逻辑（检测 / 检索 / 域名档案 / 用途识别）
│   ├── static/           # 前端静态页（发布用）
│   └── requirements.txt
└── wangzhi-huiyan/       # 前端开发源 + 回归测试
    ├── index.html        # 主页面
    ├── engine.js         # 纯逻辑层（Node 可单测）
    ├── serve.py          # 本地前端静态服务（禁缓存）
    └── _t_*.js           # 回归测试
```

## 快速开始

```bash
# 1. 安装依赖
cd huayan
pip install -r requirements.txt

# 2. 启动（单端口，同时服务前端 + API）
python app.py
# 打开 http://127.0.0.1:5000/
```

默认运行在**真实模式**（`RUN_MODE=real`），全部检测结果来自实时联网查询。若需本地调试演示数据，可设环境变量 `HUAYAN_MODE=mock`。

### 可选：接入大模型

站点用途识别、需求推荐站点支持大模型增强，走 OpenAI 兼容接口（MiniMax-M3 / DeepSeek-V4 Pro 等）。未配置时自动降级到本地规则，功能仍可用：

```bash
export LLM_API_URL=https://api.minimax.io/v1/chat/completions
export LLM_API_KEY=你的key
export LLM_MODEL=MiniMax-M3
```

### 环境变量

| 变量 | 说明 | 默认 |
|---|---|---|
| `PORT` | 服务监听端口（发布平台自动注入） | 5000 |
| `HOST` | 监听地址 | 0.0.0.0 |
| `HUAYAN_MODE` | 运行模式 real / mock | real |
| `LLM_API_URL` / `LLM_API_KEY` / `LLM_MODEL` | 大模型配置 | 空（降级规则） |
| `DATABASE_PATH` | SQLite 文件路径（目录只读时兜底） | 项目目录/database.db |

## 技术栈

- **后端**：Flask + Flask-SQLAlchemy（SQLite）+ requests
- **前端**：原生 HTML / JS，无构建步骤，深浅主题 CSS 变量
- **联网能力**：RDAP（域名注册）、DNS + SSL（证书）、Bing（检索兜底）、大模型（可选）

## 免责声明

检测与推荐结果仅供参考，不构成安全担保。涉及资金、账号操作请务必核实官方渠道。
