# 网址慧眼 · Flask 后端

配套前端原型（`wangzhi-huiyan/`）的完整后端实现。提供短链展开、五维安全检测、可信站点推荐、反馈上报、历史记录等能力。

---

## 一、快速启动

### 1. 安装依赖

```bash
pip install flask flask-cors flask-sqlalchemy requests python-whois
```

依赖清单：

| 包 | 版本要求 | 用途 |
|---|---|---|
| flask | >= 3.0 | Web 框架 |
| flask-cors | >= 5.0 | 跨域支持 |
| flask-sqlalchemy | >= 3.1 | ORM 与数据库 |
| sqlalchemy | >= 2.0 | SQLAlchemy 核心 |
| requests | >= 2.31 | 第三方 HTTP 请求 |
| python-whois | >= 0.8 | Whois 查询回落方案 |

### 2. 启动服务

```bash
cd huayan
python app.py
```

启动后：

```
服务地址：http://127.0.0.1:5000
接口前缀：http://127.0.0.1:5000/api/v1
运行模式：mock 调试模式
```

**首次启动会自动完成**：
- 创建 SQLite 数据库 `database.db`
- 建 4 张数据表：`detect_history` / `website_lib` / `feedback` / `whitelist`
- 向 `website_lib` 写入 8 条预置示范站点

无需手动导入任何 SQL。

### 3. 验证接口

```bash
python verify_api.py
```

会逐条核对接口契约，输出 52 项检查结果。

---

## 二、运行模式切换

### mock 调试模式（默认）

用于与前端原型联调。

```bash
python app.py
```

或在 `config.py` 中设置：

```python
RUN_MODE = "mock"
```

**行为说明**：
- ICP 备案、Whois 注册信息、第三方威胁标签、页面 meta → **返回模拟数据**（与前端原型测试样例一致）
- URL 本地特征检测 → **真实计算**，不 mock
  - 形近混淆识别（数字↔字母还原比对品牌词）
  - 敏感路径关键词检测
  - 可执行文件后缀检测
  - HTTP 明文检测
  - IP 直连检测
  - 子域名层级计算
  - SSL 证书状态检测

### 真实 API 模式

上线成品使用。

**方式一：环境变量（推荐，不改代码）**

```bash
# Windows CMD
set HUAYAN_MODE=real && python app.py

# PowerShell
$env:HUAYAN_MODE="real"; python app.py

# Linux / macOS
export HUAYAN_MODE=real && python app.py
```

**方式二：改配置文件**

`config.py` 中修改：

```python
RUN_MODE = "real"
```

**行为说明**：全部 5 个维度走真实数据源；第三方服务未配置或调用失败时，自动降级为 `unknown` 并在 `degradeNote` 中说明原因，绝不崩溃、绝不编造数据。

---

## 三、第三方 API 配置

全部集中在 `config.py` 的 `THIRD_PARTY` 字典中，**每项可独立开启/关闭**。

### 方式一：改 config.py

```python
THIRD_PARTY = {
    "threat_intel": {
        "enabled": True,
        "api_url": "https://api.example-threat.com/v1/check",
        "api_key": "your-key-here",
    },
    "icp": {
        "enabled": True,
        "api_url": "https://api.example-icp.com/query",
        "api_key": "your-key-here",
    },
    # ...
}
```

### 方式二：环境变量（适合部署，不落代码）

```bash
set HUAYAN_MODE=real
set THREAT_API_URL=https://api.example-threat.com/v1/check
set THREAT_API_KEY=your-key-here
set ICP_API_URL=https://api.example-icp.com/query
set ICP_API_KEY=your-key-here
set WHOIS_API_URL=https://api.example-whois.com/lookup
set WHOIS_API_KEY=your-key-here
set EXPAND_API_URL=https://api.example-expand.com/resolve
set EXPAND_API_KEY=your-key-here
python app.py
```

### 关闭某项能力

```python
"icp": {"enabled": False, ...}
```

关闭后该维度降级为 unavailable，在响应的 `degradeNote` 中标注，**其他维度不受影响**。

### 第三方期望的响应格式

**威胁情报接口**（GET `?url=`）：
```json
{"phishing": false, "fraud": false, "malware": false,
 "gambling": false, "porn": false, "spam": false}
```

**ICP 查询接口**（GET `?domain=`）：
```json
{"hasIcp": true, "subjectName": "某某公司",
 "subjectType": "企业", "auditDate": "2016-05-10"}
```

**Whois 接口**（GET `?domain=`）：
```json
{"regDate": "2016-03-22", "expireDate": "2027-03-22",
 "registrar": "某Registrar", "serverIp": "1.2.3.4",
 "serverArea": "中国 · 北京", "isOverseas": false}
```

**短链解析接口**（GET `?url=&max=5`）：
```json
{"original": "...", "final": "...", "chain": [],
 "exceeded": false, "hopCount": 2}
```

> Whois 未配置第三方时，会自动回落到 `python-whois` 库直接查询。

---

## 四、接口清单

统一前缀 `/api/v1`，统一响应 `{code, msg, data}`。

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查 |
| POST | `/api/v1/url/expand` | 短链递归展开（上限5层，识别循环） |
| POST | `/api/v1/url/detect` | 链接全套安全检测（核心接口） |
| POST | `/api/v1/site/search` | 站点检索推荐（含强制实时复测） |
| POST | `/api/v1/feedback/submit` | 反馈上报 |
| GET | `/api/v1/feedback/list` | 反馈列表 |
| GET | `/api/v1/history/list` | 历史记录分页查询 |
| POST | `/api/v1/history/whitelist` | 添加白名单 |
| GET | `/api/v1/history/whitelist` | 白名单列表 |

### 请求示例

**短链展开**
```bash
curl -X POST http://127.0.0.1:5000/api/v1/url/expand \
  -H "Content-Type: application/json" \
  -d '{"url":"https://t.cn/x9k2Lm"}'
```

**链接检测**
```bash
curl -X POST http://127.0.0.1:5000/api/v1/url/detect \
  -H "Content-Type: application/json" \
  -d '{"url":"http://0k.cn-security-check.top/login"}'
```

**站点推荐**
```bash
curl -X POST http://127.0.0.1:5000/api/v1/site/search \
  -H "Content-Type: application/json" \
  -d '{"keyword":"流程图","limit":3}'
```

**历史记录**
```bash
curl "http://127.0.0.1:5000/api/v1/history/list?page=1&size=20&level=danger"
```

---

## 五、与前端原型联调

### 启动两个服务

**终端 1 — 后端**：
```bash
cd huayan
python app.py
```

**终端 2 — 前端**：
```bash
cd wangzhi-huiyan
python -m http.server 8899 --bind 127.0.0.1
```

浏览器打开 `http://127.0.0.1:8899/index.html`

### 联调检查点

1. **跨域已放行** —— `config.py` 的 `CORS_ORIGINS` 已包含 `8899` 和 `5000`，前端可直接跨源调用
2. **mock 数据对齐** —— 后端返回的样例与前端原型测试场景一致（形近钓鱼站、短链中转、循环跳转、无法访问）
3. **限流** —— 单 IP 每分钟 60 次；联调时若请求密集会触发 429

### 当前状态提示

前端原型目前仍使用自身的 mock 数据（`engine.js`）。**后端已就绪，但前端尚未切换到调用后端**。

切换时需要修改 `engine.js` 的两个函数：

```javascript
// detect() 改为
const res = await fetch('http://127.0.0.1:5000/api/v1/url/detect', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({url: input, resolvedFinal: finalUrl})
});
return res.json();

// recommend() 改为
const res = await fetch('http://127.0.0.1:5000/api/v1/site/search', {
  method: 'POST',
  headers: {'Content-Type': 'application/json'},
  body: JSON.stringify({keyword: input, limit: 3})
});
return res.json();
```

---

## 六、项目结构

```
huayan/
├── app.py                    # Flask 主入口：蓝图注册、CORS、限流、异常兜底
├── config.py                 # 配置：模式开关、第三方API、超时、限流参数
├── models.py                 # SQLAlchemy 模型（4张表）+ 预置站点数据
├── utils.py                  # 本地特征检测、风险判定、HTTP封装、限流器
├── verify_api.py             # 接口契约验证脚本（52项检查）
├── database.db               # SQLite 数据库（首次运行自动生成）
├── routes/
│   ├── url_api.py            # 短链展开 + 链接检测
│   ├── site_api.py           # 站点检索推荐
│   ├── feedback_api.py       # 反馈上报
│   └── history_api.py        # 历史记录 + 白名单
└── service/
    ├── expand_service.py     # 短链递归展开
    ├── detect_service.py     # 五维检测主流程
    ├── site_service.py       # 检索排序 + 强制复测
    └── icp_whois_service.py  # ICP / Whois 查询封装
```

---

## 七、业务规则要点

### 风险等级判定（后端固化）

判定优先级严格有序，**只允许升风险，不允许因某维度缺失而下调已有等级**：

1. **unknown** — 域名信息缺失、解析失败、页面无法访问
2. **danger** — 威胁库标记钓鱼/诈骗/恶意/赌博/色情，或页面存在仿登录表单，或直接指向可执行文件
3. **suspicious** — IP 直连、形近混淆、域名 <90 天、境外服务器、HTTP 明文、证书异常、多层子域名、敏感路径任一命中
4. **safe** — 无风险标记 + 证书正常 + 域名 ≥90 天 + 境内且已备案

### 推荐通道强制复测

即使站点库标记历史【安全】，每次推荐前仍会调用 `detect_url` 重新检测一遍。**复测后转为 danger 的站点直接过滤**，不返回给前端。防止站点被劫持后数据库记录失效。

### 违禁词库双端校验

前端 `engine.js` 与后端 `site_service.py` 各持一份违禁词库，命中即拒绝，不返回任何资源。

---

## 八、异常与降级

| 场景 | 行为 |
|---|---|
| 第三方 API 超时/失败 | 该维度标记 unavailable，`degradeNote` 说明原因，检测继续 |
| 页面抓取被反爬拦截 | `pageMeta: null`，`degradeNote` 标注"页面抓取失败" |
| 域名解析失败 | `level: unknown`，提示不建议访问 |
| 单条检测异常 | 该条降级为 unknown，不影响批量中其他结果 |
| 历史记录写入失败 | 回滚事务，不影响检测结果返回 |
| 未捕获异常 | 全局 handler 返回 500 统一格式，不返回堆栈 |

---

## 九、生产部署建议

当前使用 Flask 开发服务器，仅适用于本地联调。对外上线时建议：

```bash
pip install gunicorn waitress
gunicorn -w 4 -b 0.0.0.0:5000 app:app        # Linux
waitress-serve --port=5000 app:app            # Windows
```

同时需要：
- 关闭 `DEBUG`（默认已关闭）
- 限流器换成 Redis（当前为进程内存，多进程下不共享）
- HTTPS 部署
- `CORS_ORIGINS` 收敛为正式域名