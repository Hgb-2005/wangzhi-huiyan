/**
 * 网址慧眼 · 前端核心引擎
 * ---------------------------------------------------------------
 * 检测结论（渠道A）与站点推荐（渠道B）均来自后端真实接口：
 *   POST /api/v1/url/detect    链接风险检测
 *   POST /api/v1/site/search   靠谱站点推荐
 *   POST /api/v1/feedback/submit   用户反馈
 *   POST /api/v1/history/whitelist 白名单
 *
 * 本文件只保留可离线执行的纯逻辑层：违禁词库校验、URL 语法校验、
 * 输入分流、本地 URL 特征筛查——用于请求前预筛与后端结果交叉参考，
 * 不参与最终评级（评级红线：不得下调后端返回的等级）。
 */

// ============================================================
// 一、违禁词库（内置规则，可离线运行，不依赖接口）
// ============================================================
const PROHIBITED = [
  { cat: '盗版影视', keys: ['盗版', '盗版电影', '未删减', '免费看大片全集', '片库'] },
  { cat: '破解软件', keys: ['破解', '破解版', 'crack', '绿色版', '免授权', '激活工具', '去广告破解'] },
  { cat: '外挂', keys: ['外挂', '辅助器', '自动瞄准', '游戏作弊'] },
  { cat: '翻墙', keys: ['翻墙', '梯子', '机场节点', '代理工具', '科学上网'] },
  { cat: '赌博', keys: ['赌博', '博彩', '百家乐', '澳门赌场', '彩票预测'] },
  { cat: '色情', keys: ['色情', '黄色网站', '成人视频', '情色'] },
  { cat: '钓鱼工具', keys: ['钓鱼网站', '钓鱼链接生成', '盗号', '盗取密码'] },
  { cat: '虚假证件', keys: ['假证', '办假证', '代开发票', '假身份证', '假学历'] },
  { cat: '刷单', keys: ['刷单', '刷好评', '刷信誉', '代刷'] },
  { cat: '隐私爬虫', keys: ['爬取隐私', '手机号查询', '身份证信息查询', '户籍查询', '开盒'] },
  { cat: '盗号工具', keys: ['盗号', '撞库', '密码字典', '社工库'] },
  { cat: '网盘非法资源', keys: [' 网盘资源', '磁力链接', 'BT种子下载', '内网资源泄露'] }
];

const REFUSE_TEXT = '很抱歉，我无法为你提供该类站点推荐，该需求不符合规范，请更换合法合规需求。';

function checkProhibited(text) {
  const lower = text.toLowerCase();
  for (const group of PROHIBITED) {
    for (const k of group.keys) {
      if (lower.includes(k.toLowerCase())) {
        return { hit: true, category: group.cat, keyword: k };
      }
    }
  }
  return { hit: false };
}

// ============================================================
// 二、URL 语法校验与预处理（真实可运行的纯逻辑层）
// ============================================================
function extractUrls(raw) {
  if (!raw) return [];
  const matched = raw.match(/((?:https?:\/\/)?[a-zA-Z0-9][a-zA-Z0-9\-._~:/?#@!$&'()*+,;=%]*(?:\.[a-zA-Z]{2,})+(?::\d{2,5})?(?:\/[^\s]*)?)/g) || [];
  const seen = new Set();
  const out = [];
  for (const m of matched) {
    let u = m.trim();
    if (!/^https?:\/\//i.test(u)) u = 'https://' + u;
    if (!seen.has(u)) { seen.add(u); out.push(u); }
  }
  return out;
}

function isValidUrl(u) {
  try {
    const parsed = new URL(u);
    if (!parsed.hostname.includes('.') && !/^\d{1,3}(\.\d{1,3}){3}$/.test(parsed.hostname)) return false;
    return ['http:', 'https:'].includes(parsed.protocol);
  } catch (e) {
    return false;
  }
}

// ============================================================
// 三、输入分流（规范第四节）
// ------------------------------------------------------------
// 判定顺序（先判网址、再判需求、都不中才算无法识别）：
//   1) matchWholeUrl：整条输入是否"就是一个网址"（带协议或裸域名）
//      → 是则走通道 A（链接风险检测）
//   2) 文本里含"带协议的链接"（http/https 开头）= 用户明确粘贴了待检测链接
//      → 剩余文字是有效需求走 conflict（让用户选）；否则直接检测
//   3) 没有协议链接，只有"裸域名片段"：
//      - 夹在中文句子里（你好.baidu.com / 测试abc.def乱码）→ 无效输入 garbage
//      - 纯英文文本且域名占比 ≥ 60%（是主体）→ conflict / url；占比低视为夹带
//   4) 不含任何网址 → looksLikeDemand 判断是否有效需求
//      → 是则走通道 B（需求推荐站点）
//   5) 都不命中 → garbage（提示"无法识别…"）
//
// 关键防误判（本次修复的两个 bug）：
//   - 乱码文本里凑巧出现 "xxx.cn" 片段：含中文 → 整条判 garbage，
//     不再误套检测报告模板；
//   - "听音乐""看电影"这类短需求：不依赖窄词表，
//     凡含真实中文/英文语义且非无意义重复 → 判为需求，正常走推荐。
// ============================================================

// 主机名是否"像真的域名"。
// 两个坑都要防：
//   1) WHATWG URL 对主机名很宽容，'!' 不在 forbidden host code point 列表里，
//      new URL('https://!!!abc.def!!!') 不抛错，hostname 就是 '!!!abc.def!!!'；
//   2) 纯数字会被按整数 IP 解析：new URL('https://123456').hostname === '0.1.226.64'。
function saneHost(u) {
  try {
    const h = new URL(u).hostname;
    // IPv4 点分十进制原样允许（用户粘 IP 来检测是合理意图）
    if (/^\d{1,3}(\.\d{1,3}){3}$/.test(h)) return true;
    // 其余只允许 字母/数字/点/连字符，且至少含一个字母
    return /^[a-zA-Z0-9.\-]+$/.test(h) && /[a-zA-Z]/.test(h);
  } catch (e) {
    return false;
  }
}

// 整条输入是否"就是一个网址"（严格：不接受夹在文字里的域名片段）
function matchWholeUrl(raw) {
  const t = String(raw || '').trim();
  if (!t) return null;
  // 含中文等非 ASCII 字符 → 不是网址（用户不会手输 punycode），
  // 这一条直接把"乱码里凑巧的 xxx.cn"挡在门外
  if (/[^\x00-\x7F]/.test(t)) return null;
  // 纯数字串不是网址（new URL 会把它解析成整数 IP，isValidUrl 会被骗过）
  if (/^\d+$/.test(t)) return null;
  // 带协议：协议后必须是无空白的连续串，且域名合法、主机名像真的
  const m = t.match(/^(https?:\/\/)(\S+)$/i);
  if (m) return isValidUrl(t) && saneHost(t) ? t : null;
  // 裸域名：整体必须是无空格的单个 token（含空格说明是句子，不是域名）
  if (/\s/.test(t)) return null;
  const withProto = 'https://' + t;
  return isValidUrl(withProto) && saneHost(withProto) ? withProto : null;
}

// 网址在整条输入中的占比（去掉空白后，各域名主体长度之和 / 输入长度）
function urlDominance(text, urls) {
  const stripped = String(text || '').replace(/\s+/g, '');
  if (!stripped.length) return 0;
  let covered = 0;
  for (const u of urls) {
    covered += u.replace(/^https?:\/\//i, '').length;
  }
  return covered / stripped.length;
}

// 是否"有效的自然语言需求"（宽松：覆盖"听音乐""看电影"这类短需求）
function looksLikeDemand(raw) {
  const t = String(raw || '').trim();
  if (!t) return false;
  // 先把可能的网址片段剔掉，只看文字部分
  const textPart = t.replace(/https?:\/\/\S+/gi, ' ').trim();
  if (!textPart) return false;
  // 必须含真实文字：中文或有意义的英文单词（≥2 字母）
  const hasCJK = /[\u4e00-\u9fa5]/.test(textPart);
  const hasWord = /[a-zA-Z]{2,}/.test(textPart);
  if (!hasCJK && !hasWord) return false;
  // 去掉标点/符号/空白后的"实质内容"至少 2 个字符
  const core = textPart.replace(/[\s\p{P}\p{S}]/gu, '');
  if (core.length < 2) return false;
  // 纯数字不算需求
  if (/^\d+$/.test(core)) return false;
  // 纯英文（无中文）必须含元音字母：挡住 "zX9#kL2" 这类无音节乱码。
  // "php""vpn"这类缩写会被误伤，但单独输缩写更像乱码而非需求，可接受
  if (!hasCJK && !/[aeiou]/i.test(core)) return false;
  // 无意义重复字符（啊啊啊、哈哈哈）：字符种类过少。
  // 只拦长度≥3的，"画画""么么"这类 2 字叠词是正常需求，放行
  const uniq = new Set(core.toLowerCase().split(''));
  if (uniq.size <= 1 && core.length >= 3) return false;
  // 无意义重复片段（乱码乱码乱码乱码、哈哈哈哈）：整体是短片段的循环。
  // 只拦长度≥4的，"听听""看看"这类 2 字叠词放行
  if (core.length >= 4 && /^(.{2,}?)\1+$/.test(core)) return false;
  return true;
}

function routeInput(raw) {
  const text = String(raw || '').trim();
  if (!text) return { type: 'garbage' };

  // 1) 整体就是一个网址 → 直接走链接检测
  const wholeUrl = matchWholeUrl(text);
  if (wholeUrl) return { type: 'url', urls: [wholeUrl] };

  // 2) 文本里含"带协议的链接"：用户明确粘贴了待检测链接，网址是主体。
  //    剩下的文字若构成有效需求 → conflict 让用户二选一；否则直接检测
  if (/https?:\/\/\S+/i.test(text)) {
    const urls = extractUrls(text);
    if (urls.length > 0) {
      return looksLikeDemand(text)
        ? { type: 'conflict', urls }
        : { type: 'url', urls };
    }
  }

  // 3) 没有协议链接，但文本里含"裸域名片段"
  const frags = extractUrls(text);
  if (frags.length > 0) {
    // 夹在中文句子里（你好.baidu.com、测试abc.def乱码）：
    // 没有协议的域名只是句子的一部分，不是待检测链接 → 无效输入
    if (/[\u4e00-\u9fa5]/.test(text)) return { type: 'garbage' };
    // 纯英文文本：域名占比 ≥ 60% 才算主体，否则视为夹带的小片段
    if (urlDominance(text, frags) >= 0.6) {
      return looksLikeDemand(text)
        ? { type: 'conflict', urls: frags }
        : { type: 'url', urls: frags };
    }
    return { type: 'garbage' };
  }

  // 4) 不含任何网址 → 判断是否有效需求
  if (looksLikeDemand(text)) return { type: 'intent', text };

  // 5) 既不是网址也识别不出需求
  return { type: 'garbage' };
}

const GARBAGE_TEXT = '无法识别，请输入网址检测风险，或者描述你的网站查找需求';


// ============================================================
// 四、本地 URL 特征检测（纯规则，真实可运行，不依赖接口）
// ============================================================
function localFeatures(url) {
  let parsed;
  try { parsed = new URL(url); } catch (e) { return null; }
  const host = parsed.hostname;
  const features = [];

  const isIp = /^\d{1,3}(\.\d{1,3}){3}$/.test(host);
  if (isIp) features.push('IP 直连访问：直接以 IP 访问网站，正规企业官网极少采用');

  // 形近混淆：域名标签里混入数字与字母混合的可疑组合
  const labels = host.split('.');
  let suspiciousLabel = labels.find(l => /\d/.test(l) && /[a-z]/.test(l));
  if (suspiciousLabel && !isIp) {
    features.push(`域名形近混淆风险：域名片段 "${suspiciousLabel}" 含数字与字母混合，易为仿冒（如 0/o、1/l 替换）`);
  }

  const domainLen = labels.filter(l => l !== 'www').join('.').length;
  if (domainLen > 30) features.push(`域名长度偏长（${domainLen} 字符）：增加仿冒与混淆难度`);

  const subDepth = labels.length - (labels[labels.length - 2] === 'com' || labels[labels.length - 2] === 'net' || labels[labels.length - 2] === 'org' || labels[labels.length - 2] === 'top' || labels[labels.length - 2] === 'cn' ? 2 : 1);
  if (subDepth >= 3) features.push(`多层子域名结构（共 ${subDepth} 级）：常见于短链中转或仿冒路径`);

  if (parsed.protocol === 'http:') features.push('HTTP 无加密：页面输入的信息传输过程可能被窃听');

  const sensitiveKeys = ['token', 'password', 'download', 'login', 'verify', 'account', 'pay', '.exe', '.apk', '.bat', '.scr', '.zip'];
  const pathLower = (parsed.pathname + parsed.search).toLowerCase();
  const hitSensitive = sensitiveKeys.filter(k => pathLower.includes(k));
  if (hitSensitive.length) features.push(`敏感路径关键词：命中 ${hitSensitive.map(k => '「' + k + '」').join('、')}，可能诱导输入隐私信息或下载可执行文件`);

  return { parsed, host, isIp, features, domainLen, subDepth, sensitiveHits: hitSensitive };
}

function classifyLocalFeatures(features, caseData) {
  // 本地特征评级：与后端返回结果交叉参考，任一高危则整体不可降级（红线：不私自降低后端评级）
  if (!features) return 'unknown';
  const f = features.features.join(' ').toLowerCase();
  const hasDanger = f.includes('ip 直连') && (f.includes('敏感路径') || f.includes('形近混淆'));
  if (hasDanger) return 'danger';
  return caseData.level || 'unknown';
}

// ============================================================
// 五、检测主流程（已对接后端 /api/v1/url/detect）
// ============================================================
// API 地址自适应：
//   - 本地开发：前端跑在 8899（serve.py），后端跑在 5000 → 显式指向本机 5000
//   - 线上部署：前后端合并为同一端口（同源）→ 用相对路径 /api/v1
// 用 typeof location 守卫，保证在 Node 里 require 做单测时不会抛 ReferenceError。
const API_BASE = (typeof location !== 'undefined' && location.port === '8899')
  ? 'http://127.0.0.1:5000/api/v1'
  : '/api/v1';
const API_TIMEOUT = 15000;   // 单次请求超时（毫秒），超时按后端不可用处理

// 通道B 联网检索专用超时：后端要抓搜索引擎 + 逐个站点做 DNS/HTTP 实连，
// 实测中单个站点最慢可能耗 5 秒、候选最多 12 个，15 秒会误判超时。
// 放到 45 秒，既能容纳慢网络，又不至于让用户无限等待。
const RECOMMEND_TIMEOUT = 45000;

/**
 * 带超时的 POST JSON 请求。
 * 后端未启动 / 跨域被阻断 / 请求挂起时都能在超时内返回，避免页面卡死。
 *
 * @param {object} opts 可选第三参数
 *        opts.timeout 覆盖默认 API_TIMEOUT（通道B 联网检索需要更长）
 */
async function postJSON(path, payload, opts = {}) {
  const timeout = opts.timeout || API_TIMEOUT;
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeout) : null;
  try {
    const resp = await fetch(API_BASE + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: ctrl ? ctrl.signal : undefined
    });
    return await resp.json();
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * 通用请求方法：支持 GET / POST / DELETE，带 15 秒超时。
 * 功能1（历史记录）需要 GET 列表和 DELETE 删除，postJSON 只支持 POST，所以补这个通用封装。
 * 返回值统一是后端的 {code, msg, data}；网络异常会抛出去由调用方 try/catch。
 */
async function requestJSON(path, { method = 'GET', payload = null } = {}) {
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), API_TIMEOUT) : null;
  try {
    const opts = {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      signal: ctrl ? ctrl.signal : undefined
    };
    // GET 请求不能带 body，否则部分浏览器会报错
    if (payload !== null && method !== 'GET') opts.body = JSON.stringify(payload);
    const resp = await fetch(API_BASE + path, opts);
    return await resp.json();
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * 批量检测 —— 输入预处理（纯逻辑，不发请求，可单独测试）
 * ------------------------------------------------------------
 * 约定：一行一个网址。
 * 处理步骤：
 *   1. 按行拆分
 *   2. 去掉每行首尾空白
 *   3. 丢弃空行
 *   4. 补全协议（用户只写 example.com 时自动补 https://）
 *   5. 用已有语法校验过滤掉明显无效的行
 *   6. 去重（同一个网址重复写只查一次）
 * 返回：{ urls: 有效网址数组, invalid: [{raw, reason}] 无效行及原因, total: 输入总行数 }
 */
function parseBatchInput(rawText) {
  const lines = String(rawText || '').split(/\r?\n/);
  const urls = [];
  const invalid = [];
  const seen = new Set();
  let total = 0;

  for (let line of lines) {
    line = line.trim();                 // 去掉首尾空白
    if (!line) continue;                // 空行直接跳过，不计入总数
    total++;

    // 补全协议：只写了域名没写 http/https 时补 https
    if (!/^https?:\/\//i.test(line)) line = 'https://' + line;

    // 明显无效：含空格（说明用户把多个网址写在一行了）
    if (/\s/.test(line)) {
      invalid.push({ raw: line, reason: '一行内含多个网址，请一行只填一个' });
      continue;
    }
    if (!isValidUrl(line)) {
      invalid.push({ raw: line, reason: '不是有效网址' });
      continue;
    }
    if (seen.has(line)) continue;       // 去重
    seen.add(line);
    urls.push(line);
  }

  return { urls, invalid, total };
}

/**
 * 调用后端检测接口。
 * 返回结构已适配为渲染层原有期望的形状，index.html 渲染逻辑无需改动。
 */
async function detect(rawInput, opts = {}) {
  const degrade = opts.degrade || null; // 'L1' | 'L2' | 'L3' | 'L4' | null
  const urls = extractUrls(rawInput);

  if (urls.length === 0) return { type: 'invalid', msg: '识别到不是有效网址，请检查链接，确认有没有多打空格、缺https' };
  if (urls.length > 5) return { type: 'limit', msg: '单次最多支持同时检测5条链接，剩余链接请分批提交' };

  const invalid = urls.filter(u => !isValidUrl(u));
  const valid = urls.filter(u => isValidUrl(u));
  if (valid.length === 0) {
    return { type: 'invalid', msg: '识别到不是有效网址，请检查链接，确认有没有多打空格、缺https' };
  }

  const results = [];
  // 语法非法的链接直接标注，不发给后端
  for (const u of invalid) {
    results.push({ input: u, invalid: true, msg: '识别到不是有效网址，请检查链接，确认有没有多打空格、缺https' });
  }

  // ---------- 调用后端 ----------
  let payload;
  try {
    payload = await postJSON('/url/detect', { url: valid, saveHistory: true });
  } catch (e) {
    // 网络层失败（后端未启动 / 跨域阻断 / 请求超时）
    return {
      type: 'backendDown',
      msg: '无法连接后端检测服务（' + API_BASE + '），请确认 Flask 后端已启动。本地仅能做基础 URL 特征筛查，风险识别能力受限。'
    };
  }

  // 后端统一响应 {code, msg, data}
  if (payload.code !== 200) {
    return {
      type: 'backendError',
      code: payload.code,
      msg: payload.msg || '后端检测服务返回错误'
    };
  }

  const data = payload.data || {};
  const list = Array.isArray(data.results) ? data.results : [data.results].filter(Boolean);

  for (const item of list) {
    const input = item.url || item.finalUrl;
    const local = localFeatures(item.finalUrl || input);   // 仅用于展示协议，不参与评级
    const demo = adaptDetectResponse(item, degrade);
    results.push({
      input,
      local,
      demo,
      degradeNote: demo.degradeNote,
      loopExceeded: demo.loopExceeded
    });
  }

  return { type: 'report', results };
}

/**
 * 适配层：把后端返回的标准 JSON 转成渲染层期望的结构。
 * ★ 风险等级与风险清单一律采用后端返回值，前端不做任何改写或降级。
 */
function adaptDetectResponse(item, degrade) {
  const di = item.domainInfo || {};
  const icp = item.icp || {};
  const lf = item.localFeatures || {};

  // 风险等级：后端给什么就是什么
  const level = ['safe', 'suspicious', 'danger', 'unknown'].includes(item.level)
    ? item.level : 'unknown';
  const labelMap = { safe: '✅ 安全', suspicious: '⚠️ 可疑', danger: '❌ 高危', unknown: '🤷 未知' };

  // 风险清单：直接采用后端返回，前端不追加、不删改
  const risks = Array.isArray(item.risks) ? item.risks.slice() : [];

  // ICP 展示
  let icpStatus, icpNote;
  if (icp.hasIcp === true) {
    icpStatus = `已备案 · ${icp.subjectName || '主体未知'}${icp.subjectType ? ' · ' + icp.subjectType : ''}${icp.auditDate ? ' · 审核 ' + icp.auditDate : ''}`;
    icpNote = icpStatus;
  } else if (icp.hasIcp === false) {
    icpStatus = icp.note || '未查询到ICP备案';
    icpNote = icpStatus;
  } else {
    icpStatus = icp.note || '备案信息查询服务不可用';
    icpNote = icpStatus;
  }

  // 降级提示：优先用后端返回的 degradeNote，其次叠加本地降级开关的说明
  let degradeNote = item.degradeNote || null;
  if (degrade === 'L1') {
    degradeNote = '当前安全威胁库服务不可用，仅做基础特征筛查，识别能力大幅下降';
  } else if (degrade === 'L4') {
    degradeNote = '短链解析暂时失效，仅能检测你提交的原始链接，无法查看真实跳转目标';
  } else if (degrade === 'L2' && !item.pageMeta) {
    degradeNote = '页面抓取失败，暂无站点介绍';
  }

  // 站点说明
  let summary = item.siteSummary || '页面抓取失败，暂无站点介绍';
  if (degrade === 'L2' || !item.pageMeta) summary = '页面抓取失败，暂无站点介绍';

  return {
    label: labelMap[level],
    color: level,
    level,
    // 跳转链：来自后端 expand 解析结果，展示完整多级链路
    jumpChain: buildJumpChain(item),
    loopExceeded: !!(item.isLoop || (item.expand && item.expand.isLoop)),
    domain: {
      name: di.domain || item.domain || '未知',
      regDate: di.regDate || '未知',
      expireDate: di.expireDate || '未知',
      registrar: di.registrar || '未知',
      serverIp: di.serverIp || '未知',
      serverArea: di.serverArea || '未知',
      // 实时查询来源与是否部分失败（后端新增字段），用于报告单标注
      source: di.whoisSource || '',
      partial: !!di.partial
    },
    icp: { status: icpStatus, note: icpNote },
    summary,
    // 站点用途识别（大模型一句话总结，通道A 报告新增板块）
    sitePurpose: item.sitePurpose || '暂时无法获取页面信息，无法识别站点用途',
    risks,
    ssl: lf.sslStatus || '未检测',
    html: item.pageMeta,
    degradeNote
  };
}

/**
 * 用后端返回的跳转链路拼出展示文本。
 * 优先使用 expand.chain（完整多级链路），后端未返回时回退到 url/finalUrl 拼接。
 */
function buildJumpChain(item) {
  const chain = item.expandChain || (item.expand && item.expand.chain) || [];

  if (Array.isArray(chain) && chain.length > 0) {
    const lines = chain.map(step => {
      const hop = step.hop != null ? `hop${step.hop}` : '跳转';
      const status = step.status ? ` (HTTP ${step.status})` : '';
      return `${hop}: ${step.url}${status}`;
    });
    const exp = item.expand || {};
    if (exp.isLoop) {
      lines.push(`⚠ ${exp.note || '检测到循环跳转，已终止解析'}`);
    } else if (exp.finalUrl && exp.finalUrl !== item.url) {
      lines.push(`→ 最终目标: ${exp.finalUrl}`);
    } else if (exp.note) {
      lines.push(`· ${exp.note}`);
    }
    return lines;
  }

  // 回退方案：用原始地址与最终地址拼接
  const o = item.url || '';
  const f = item.finalUrl || '';
  if (o && f && o !== f) {
    return [`用户提交链接 → hop1: ${o}`, `hop1 → 最终目标: ${f}`];
  }
  return [o ? `用户提交链接 → 直接访问，无跳转层（${o}）` : '用户提交链接 → 无数据'];
}

// 保留：独立短链解析调用（供展开链路单独查询与降级 L4 场景使用）
async function expandViaBackend(url) {
  try {
    const payload = await postJSON('/url/expand', { url });
    if (payload.code !== 200) return null;
    return payload.data;
  } catch (e) {
    return null;
  }
}

/**
 * 批量检测 —— 调用后端接口
 * ------------------------------------------------------------
 * 后端 POST /api/v1/url/detect 原生支持一次传入多个网址（url 传数组），
 * 不需要也不应该循环调用单条接口。
 *
 * 注意：后端限制单次最多 5 条（config.MAX_URLS_PER_REQUEST），
 * 所以这里按 5 条一批顺序发送，避免整批被拒。
 *
 * 返回：{ rows: [{url, level, label, hops, reason}], failed: 整批失败的批次信息 }
 *   rows 里每条对应一行表格；level 缺失时用 unknown 兜底。
 */
const BATCH_SIZE = 5;

async function batchDetect(urls, opts = {}) {
  const saveHistory = opts.saveHistory !== false;   // 默认顺带存历史
  const rows = [];
  let failedBatches = 0;

  // 按 5 条一批顺序发送
  for (let i = 0; i < urls.length; i += BATCH_SIZE) {
    const chunk = urls.slice(i, i + BATCH_SIZE);

    let payload;
    try {
      payload = await postJSON('/url/detect', { url: chunk, saveHistory: saveHistory });
    } catch (e) {
      // 网络层失败（后端没起/超时），本批全部标记失败，不影响已完成的批次
      failedBatches++;
      chunk.forEach(u => rows.push({
        url: u, level: 'unknown', label: '未知', hops: 0,
        note: '无法连接检测服务，请确认 Flask 已启动'
      }));
      continue;
    }

    if (!payload || payload.code !== 200) {
      // 后端整体返回错误码（如超限/限流），本批全部标记
      failedBatches++;
      const msg = (payload && payload.msg) || '检测服务返回错误';
      chunk.forEach(u => rows.push({ url: u, level: 'unknown', label: '未知', hops: 0, note: msg }));
      continue;
    }

    // 后端只有 1 条时返回对象，多条时返回数组，两种都要兼容
    const data = payload.data || {};
    let list = data.results;
    if (!Array.isArray(list)) list = [list].filter(Boolean);

    // 后端逐条 try/catch，单条异常会返回 level=unknown + reason，不会让整批崩
    list.forEach(item => {
      const level = item.level || 'unknown';
      rows.push({
        url: item.url || '',
        level: level,
        label: item.label || '未知',
        hops: (item.expand && item.expand.hopCount) || item.expandHops || 0,
        isLoop: !!(item.isLoop || (item.expand && item.expand.isLoop)),
        // 站点用途识别（大模型一句话总结），供导出 TXT 同步带上
        purpose: item.sitePurpose || '',
        // 注意：后端的 reason 字段是「结论说明」（如「未检出风险特征」「命中恶意特征」），
        // 正常结果也会有，不能当错误处理。
        // 真正的异常只出现在 level=unknown 且 reason 以「检测服务异常」开头的情况。
        // 所以这里只在 unknown 时把它带出来，作为补充提示。
        note: level === 'unknown' ? (item.reason || item.degradeNote || '') : ''
      });
    });

    // 批次之间稍作间隔，避免连续请求被后端限流（60次/分钟）
    if (i + BATCH_SIZE < urls.length) {
      await new Promise(r => setTimeout(r, 300));
    }
  }

  return { rows, failedBatches };
}

// ============================================================
// 六、需求推荐（通道 B）辅助配置
// ============================================================
const CLARIFY_QUESTIONS = {
  '画图': '请问你需要的是 AI 文生图网站、流程图绘图网站、矢量设计网站，还是手绘在线工具？',
  '转换': '请问你主要想转换的是文件格式、图片格式，还是音视频格式？',
  '笔记': '请问你侧重的是个人随手记，还是需要多人协作与团队共享？',
  '截图': '请问你需要的是网页长截图工具，还是本地电脑端截图标注工具？'
};

/**
 * 需求推荐（通道 B）—— 已对接后端 /api/v1/site/search
 * 违禁词在前端做一次快速拦截，同时后端也会独立校验（双端防护）。
 */
async function recommend(rawText, opts = {}) {
  const degrade = opts.degrade || null;

  const prohibit = checkProhibited(rawText);
  if (prohibit.hit) {
    return { type: 'refuse', category: prohibit.category, keyword: prohibit.keyword };
  }

  if (degrade === 'L3') {
    return { type: 'degrade', msg: '站点推荐服务暂时维护，链接检测功能可正常使用。' };
  }

  let payload;
  try {
    // ★ 通道B 走联网检索，后端要逐个候选做 DNS 解析 + HTTP 实连，
    // 最坏情况（多个站点超时 + 搜索引擎抓取）会超过通用 15 秒超时，
    // 所以这里单独用更长的超时，避免被误判成"后端不可用"
    payload = await postJSON('/site/search', {
      keyword: rawText,
      limit: 5,
      constraints: {}
    }, { timeout: RECOMMEND_TIMEOUT });
  } catch (e) {
    // 区分"连不上后端"与"后端在长时间联网检索后超时"
    const aborted = e && (e.name === 'AbortError' ||
      /abort/i.test(String(e.message || '')));
    if (aborted) {
      return {
        type: 'searchFailed',
        msg: '联网检索超时（超过 ' + Math.round(RECOMMEND_TIMEOUT / 1000) +
          ' 秒），暂时无法推荐站点，请稍后重试。'
      };
    }
    return {
      type: 'backendDown',
      msg: '无法连接后端推荐服务（' + API_BASE + '），请确认 Flask 后端已启动。链接检测功能不受影响，可正常使用。'
    };
  }

  if (payload.code !== 200) {
    return { type: 'backendError', code: payload.code, msg: payload.msg || '后端推荐服务返回错误' };
  }

  const data = payload.data || {};

  // 后端独立拦截违禁需求（前端已拦一次，这里做双端校验）
  if (data.refused) {
    return {
      type: 'refuse',
      category: data.category,
      keyword: data.keyword,
      fromBackend: true
    };
  }

  // ★ 联网检索失败（网络不通 / 无可用候选）。
  // 后端此时不会返回任何站点（绝不用演示假域名兜底），
  // 这里单独走一种类型，让页面明确提示而不是显示空卡片列表
  if (data.searchFailed) {
    return {
      type: 'searchFailed',
      msg: data.msg || '联网检索失败，暂时无法推荐站点'
    };
  }

  if (!data.sites || data.sites.length === 0) {
    return {
      type: 'empty',
      msg: data.msg || '站点库暂未收录满足该需求的网站，你可以尝试修改描述关键词。'
    };
  }

  // 字段适配：后端结构 → 渲染层期望结构
  const sites = data.sites.map(s => ({
    id: s.id,
    name: s.name,
    url: s.url,
    desc: s.summary || s.name,
    scene: s.scene || '',
    free: !!s.free,
    ad: !!s.hasAd,
    history: (s.historyLevel === 'safe' ? '✅ 安全'
      : s.historyLevel === 'suspicious' ? '⚠️ 可疑'
        : s.historyLevel === 'danger' ? '❌ 高危' : '🤷 未知'),
    live: (s.liveLevel === 'safe' ? '✅ 安全'
      : s.liveLevel === 'suspicious' ? '⚠️ 可疑'
        : s.liveLevel === 'danger' ? '❌ 高危' : '🤷 未知'),
    liveRisks: s.liveRisks || [],
    rechecked: !!s.rechecked
  }));

  return {
    type: 'cards',
    sites,
    recheckCount: data.recheckCount || 0,
    note: data.note || null
  };
}

// ============================================================
// 七、固定文案
// ============================================================
const DISCLAIMER = '免责声明：本工具检测结果仅作为风险参考，无法识别刚上线、未录入安全库的新型钓鱼、诈骗网站；HTTPS加密不代表网站绝对安全。本工具不对推荐站点、检测结果做信用背书，请你自主审慎判断，不要向陌生网站提交银行卡、验证码、账号密码等隐私信息，因访问网站造成的损失由用户自行承担。';

const ERROR_MSGS = {
  timeout: '后端检测服务请求超时，本次仅完成本地URL特征简易检查，无法调用第三方安全库，结果参考性有限。',
  quota: '安全检测接口额度已用尽，仅可展示域名基础信息，风险识别能力下降。',
  crawl: '无法获取网页简介，该站点页面信息抓取失败。'
};

const RISK_SCIENCE = {
  '形近混淆': '骗子使用外形几乎一样的字符仿冒官网，例如把字母o换成数字0、字母l换成数字1，肉眼很容易看错，是最常见的钓鱼手段。',
  '短链': '短链接会隐藏真实目标地址，你第一眼无法判断最终打开是什么页面，很多诈骗、木马链接都会使用短链包装。',
  'HTTP无加密': '网站没有SSL加密，你在页面输入的账号、密码、手机号在传输过程中有可能被窃听截取。',
  'SSL异常': '证书过期、证书域名不匹配、自签名证书，代表网站身份无法正常核验，风险升高。',
  'IP直连': '直接用IP地址访问网站，一般正规企业官网很少直接使用IP访问，大量恶意、赌博、钓鱼站点偏好这种方式。',
  '新建域名': '刚注册不久的域名，没有长期运营记录，黑产经常注册新域名搭建临时钓鱼站，短时间收割后直接丢弃。',
  '境外服务器': '服务器部署在海外，国内监管很难覆盖，很多违规、诈骗站点会选用境外服务器。',
  '敏感路径': '链接里带有 password、token、download、exe/apk 等关键词，大概率会要求你输入隐私信息或者下载可执行文件，容易中毒。',
  'HTTPS不等于绝对安全': 'HTTPS仅仅代表传输数据加密，任何人都可以低成本申请证书，钓鱼网站一样可以拥有HTTPS绿色锁标，不能当成安全凭证。'
};

// ============================================================
// 八、主题切换（深色 / 浅色）
// ------------------------------------------------------------
// 页面所有颜色都来自 index.html 里的 CSS 变量，
// 所以这里只负责三件事：读 localStorage、切换 html 属性、同步按钮文案。
// 注意：index.html 的 <head> 里已有一段内联脚本会在 CSS 加载前先读一次 localStorage，
//      避免刷新时先闪一下默认深色再切回浅色；本函数是给运行时切换用的。
// ============================================================
const THEME_KEY = 'wzh_theme';   // localStorage 键名

function getTheme() {
  return document.documentElement.getAttribute('data-theme') === 'light' ? 'light' : 'dark';
}

function setTheme(theme) {
  const t = (theme === 'light') ? 'light' : 'dark';
  if (t === 'light') {
    document.documentElement.setAttribute('data-theme', 'light');
  } else {
    document.documentElement.removeAttribute('data-theme');
  }
  try {
    localStorage.setItem(THEME_KEY, t);
  } catch (e) { /* 隐私模式写不进去也不影响本次切换 */ }
  return t;
}

function toggleTheme() {
  return setTheme(getTheme() === 'light' ? 'dark' : 'light');
}

// ============================================================
// 九、导出 TXT 报告（功能4）
// ------------------------------------------------------------
// 设计原则：**这一层全部是纯逻辑，不碰 DOM、不发请求**，只负责
//   1) 把结构化条目拼成 TXT 文本
//   2) 生成带时间戳的文件名
// 这样就能用 Node 直接 require 出来做单元测试，
// 而"读数据"和"触发下载"分别由 index.html 里的两条通道负责：
//   模式A 数据源 = 本次批量检测的结果（已在内存里，无需请求）
//   模式B 数据源 = 后端 history/list 接口（现成的 GET，不新增接口）
//
// 关于编码：Blob 的 type 参数写 'text/plain;charset=utf-8'，
// 并且**必须加 BOM**（\uFEFF）。原因：Windows 记事本用 ANSI(GBK) 猜测
// 无 BOM 的 UTF-8 文本，中文会变成乱码；加 BOM 后记事本会正确识别为 UTF-8。
// ============================================================

// 风险等级 → TXT 里显示的中文标签（与页面 .lv 徽章语义保持一致）
const LEVEL_TEXT = {
  safe: '安全',
  suspicious: '可疑',
  danger: '高危',
  unknown: '未知'
};

// TXT 里用来分隔每条记录的分割线（纯 ASCII，任何编辑器都不会乱）
const TXT_DIVIDER = '----------------------------------------';

/**
 * 把两位数的数字补零，例如 3 → '03'。
 * 文件名要求 HHMM 是四位，所以小时/分钟必须补零。
 */
function pad2(n) {
  return String(n).padStart(2, '0');
}

/**
 * 生成报告文件名：网址慧眼报告_20261022_2130.txt
 * @param {Date} date 可选，默认取当前时间（方便测试时传固定时间）
 * @returns {string} 文件名
 */
function buildReportFileName(date) {
  const d = date instanceof Date ? date : new Date();
  const y = d.getFullYear();
  const m = pad2(d.getMonth() + 1);
  const day = pad2(d.getDate());
  const hh = pad2(d.getHours());
  const mm = pad2(d.getMinutes());
  return '网址慧眼报告_' + y + m + day + '_' + hh + mm + '.txt';
}

/**
 * 把时间对象格式化成 '2026-10-22 21:30:05'。
 * 直接用 toISOString() 会变成 UTC 时间（差 8 小时），所以手工取本地时间字段。
 */
function formatDateTime(d) {
  const dt = d instanceof Date ? d : new Date();
  const date = dt.getFullYear() + '-' + pad2(dt.getMonth() + 1) + '-' + pad2(dt.getDate());
  const time = pad2(dt.getHours()) + ':' + pad2(dt.getMinutes()) + ':' + pad2(dt.getSeconds());
  return date + ' ' + time;
}

/**
 * 把一组条目规范化成导出用的统一结构。
 * 两个数据源的字段名不一样（批量用 label，历史用 riskLabel），这里抹平差异，
 * 让后面的 buildReportText 只面对一种形状。
 *
 * @param {Array} rows 原始条目数组
 * @param {'batch'|'history'} source 数据源标记，决定读哪套字段名
 * @returns {Array} [{ url, level, label, hops, time, note }]
 */
function normalizeExportRows(rows, source) {
  const list = Array.isArray(rows) ? rows : [];
  return list.map(function (r) {
    r = r || {};
    // 模式A（批量）字段：url / label / hops / note
    // 模式B（历史）字段：originalUrl / riskLabel / expandHops / createdAt
    const isHistory = source === 'history';
    const url = isHistory ? (r.originalUrl || '') : (r.url || '');
    // rawLevel 保留后端给的原始等级字符串。
    // 万一后端将来新增了等级（比如 medium），我们要如实显示出来，
    // 而不是悄悄算成"未知"让人看不出真实情况。
    const rawLevel = (isHistory ? r.riskLevel : r.level) || '';
    const known = LEVEL_TEXT[rawLevel] ? rawLevel : 'unknown';
    const label = (isHistory ? r.riskLabel : r.label) || LEVEL_TEXT[known] || '未知';
    const hops = isHistory ? (r.expandHops || 0) : (r.hops || 0);
    const time = isHistory ? (r.createdAt || '') : '';
    return {
      url: url,
      level: known,
      rawLevel: rawLevel,
      label: label,
      hops: hops,
      time: time,
      note: r.note || '',
      // 站点用途识别结果（批量检测有、历史记录无此字段）
      purpose: r.purpose || r.sitePurpose || ''
    };
  });
}

/**
 * 生成 TXT 报告全文。
 *
 * 排版规范：
 *   第 1 行   报告标题（批量检测报告 / 历史记录报告）
 *   第 2 行   导出时间
 *   第 3 行   条目总数 + 风险分布统计
 *   空行 + 分割线
 *   每条记录：序号. 网址 / 风险结论 / 跳转层数 / 检测时间 / 备注
 *   记录之间用分割线隔开，末尾附免责声明
 *
 * @param {Array} rows 原始条目数组
 * @param {Object} opts { source: 'batch'|'history', date: Date }
 * @returns {string} 完整 TXT 文本
 */
function buildReportText(rows, opts) {
  const options = opts || {};
  const source = options.source === 'history' ? 'history' : 'batch';
  const date = options.date instanceof Date ? options.date : new Date();

  const items = normalizeExportRows(rows, source);

  // 空数据直接返回空字符串，调用方据此给"没有可导出数据"的提示，
  // 绝不下发一个空文件。
  if (items.length === 0) return '';

  const title = source === 'history' ? '历史记录报告' : '批量检测报告';

  // ---- 统计各风险等级的条数，方便一眼看完整体情况 ----
  const counter = { safe: 0, suspicious: 0, danger: 0, unknown: 0 };
  items.forEach(function (it) {
    if (counter[it.level] === undefined) counter[it.level] = 0;
    counter[it.level]++;
  });
  const summary =
    '安全 ' + counter.safe +
    '　可疑 ' + counter.suspicious +
    '　高危 ' + counter.danger +
    '　未知 ' + counter.unknown;

  const lines = [];
  lines.push('【网址慧眼 · ' + title + '】');
  lines.push('导出时间：' + formatDateTime(date));
  lines.push('报告条目：共 ' + items.length + ' 条　（' + summary + '）');
  lines.push('');
  lines.push(TXT_DIVIDER);

  items.forEach(function (it, i) {
    // 序号右对齐到两位，条目多时更好读
    const no = String(i + 1).padStart(2, '0');
    lines.push(no + '. 网址：' + (it.url || '（无）'));

    // 后端返回的 label 通常已经是中文（安全/可疑/高危/未知），
    // 这时再补一个 LEVEL_TEXT 括号会变成「安全（安全）」这种重复，所以不补。
    // 但如果后端给了我们不认识的等级（如 medium），要如实标出原始代码，
    // 否则读者只会看到"未知"，以为系统没检测出来。
    const stdText = LEVEL_TEXT[it.level];
    let levelNote = '';
    if (it.rawLevel && it.rawLevel !== it.level) {
      levelNote = '（原始等级：' + it.rawLevel + '）';
    }
    lines.push('    风险结论：' + it.label + levelNote);

    // 站点用途识别：有结果才写，历史数据无此字段时跳过；
    // 剥掉后端加的【站点用途】前缀，因为本行已有"站点用途："标签
    if (it.purpose) {
      lines.push('    站点用途：' + String(it.purpose).replace(/^【站点用途】\s*/, ''));
    }

    // 跳转层数只在有跳转时才写，避免每行都多一句"0 跳"
    if (it.hops) lines.push('    跳转层数：' + it.hops + ' 跳');

    // 检测时间：批量模式的数据来自内存，没有时间戳就不写这行
    if (it.time) lines.push('    检测时间：' + it.time);

    // 备注：只有 unknown（检测不出结论）才有内容，用中性描述
    if (it.note) lines.push('    备注：' + it.note);

    // 最后一条后面不再画分割线，避免结尾多一条无意义的线
    if (i < items.length - 1) {
      lines.push('');
      lines.push(TXT_DIVIDER);
    }
  });

  lines.push('');
  lines.push(TXT_DIVIDER);
  lines.push('免责声明：本报告由网址慧眼自动生成，检测结果仅供参考。');
  lines.push('HTTPS 加密不代表网站绝对安全，请自行审慎判断，勿向陌生网站提交账号密码等隐私信息。');

  return lines.join('\n');
}

/**
 * 触发浏览器原生下载（Blob + a[download]）。
 * **浏览器专属**：Node 环境下没有 document，所以函数开头做了判断，
 * 这样 engine.js 整体仍可被 require 做单测。
 *
 * @param {string} text 文件内容
 * @param {string} fileName 文件名（.txt）
 * @returns {boolean} 是否成功触发下载
 */
function downloadTxtFile(text, fileName) {
  // 环境守卫：不在浏览器里就直接返回 false，不抛异常
  if (typeof document === 'undefined' || typeof Blob === 'undefined' || !text) return false;

  try {
    // 前面的 \uFEFF 是 UTF-8 BOM，保证 Windows 记事本不乱码
    const blob = new Blob(['\uFEFF' + text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = fileName;
    a.style.display = 'none';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    // 立刻释放会让部分浏览器下载失败，延迟一拍再回收
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
    return true;
  } catch (e) {
    return false;
  }
}

// ============================================================
// 十、AI 风险解读（功能5）
// ------------------------------------------------------------
// 设计思路：把「解读内容」和「取内容的方式」分开。
//
//   fetchInterpret()  —— 调后端 AI 解读接口（约定路径 POST /ai/interpret）。
//                        接口未部署时返回 backendDown，前端自动降级到本地解读。
//   buildLocalInterpret() —— 纯规则版解读。用项目已有的风险等级 + 风险线索，
//                        组织成规范的四段结构（风险类型/作案手法/危害/防护建议）。
//                        完全离线、零依赖，保证任何时候都有内容可看。
//
// 为什么要有本地兜底：
//   后端 AI 接口是后续补上的（当前 /health 的 7 个接口里还没有它）。
//   如果只依赖接口，接口没部署时按钮就是死的；有了兜底，
//   功能当场可用，等接口上线后自动切换为真正的 AI 解读，前端不用再改。
// ============================================================

// 四段结构的段名。固定顺序，前端渲染和自测都按这个来。
const INTERPRET_SECTIONS = [
  { key: 'type',    title: '风险类型说明', icon: '🔍' },
  { key: 'method',  title: '典型作案手法', icon: '🎭' },
  { key: 'harm',    title: '可能造成的危害', icon: '⚠️' },
  { key: 'protect', title: '防护建议',     icon: '🛡️' }
];

// 各风险等级的解读文案库。
// 每一项对应四段结构。文案基于项目 detect_service / utils 的真实判定规则整理，
// 与页面上「风险科普」（RISK_SCIENCE）互补：科普讲概念，这里讲这条链接具体情况。
const INTERPRET_LIB = {
  safe: {
    type: '本次检测未发现明显风险特征。该链接未命中恶意威胁库，域名注册时长、备案状态、证书状态均正常，请求协议为加密传输。',
    method: '暂无已知的针对性作案手法。需要提醒的是，「未检出风险」不等于「一定安全」：新型钓鱼站点可能尚未录入威胁库，或使用合法备案域名承载诈骗内容，检测工具存在识别滞后。',
    harm: '直接风险较低。但如果该站点属于你未接触过的陌生平台，仍需留意是否存在「先诱导注册、再引导付费」类的慢线套路。',
    protect: '可以正常浏览，但请遵守三条底线：不在陌生站点输入银行卡、验证码、身份证号；不安装站点提示下载的 exe/apk 文件；涉及支付时核对域名是否与官方一致。'
  },
  suspicious: {
    type: '本次检测发现多项可疑特征组合，判定为可疑。常见组合包括：域名注册时间较短、服务器位于境外、跳转层级异常、或使用了与知名品牌形近的域名。',
    method: '攻击者常用「短链隐藏真实地址」+「形近域名仿冒官网」+「多层跳转规避检测」的组合拳。用户在地址栏看到的是极短的链接，实际落地页可能是任意内容，且短链随时可以被替换成新的目标。',
    harm: '可能导致账号凭据被窃取（钓鱼登录页）、支付信息外泄，或下载到恶意程序。若该站伪装成金融、电商或政务类网站，损失可能不可逆。',
    protect: '如确需访问，请务必做到：不输入手机号、银行卡、验证码、账号密码；不下载任何文件；不授权摄像头、麦克风、通讯录权限；关闭后清除浏览器缓存与 Cookie。'
  },
  danger: {
    type: '该链接被判定为高危风险站点，多项检测维度同时告警。建议直接放弃访问，不要抱有侥幸心理。',
    method: '高危站点最典型的手法是域名形近混淆钓鱼——把字母 o 换成数字 0、字母 l 换成数字 1、用连字符拼接品牌名，制造与官网几乎一样的地址。其次是仿冒登录页：页面高度还原真实网站，诱导输入账号密码；部分还会配合 HTTP 明文传输，便于中间人窃听。',
    harm: '直接后果是账号密码、银行卡信息、短信验证码被窃取。由于用户是在「看起来完全正常的页面」上输入信息，心理上不会产生警惕，防范难度很大。资金损失往往在用户察觉前已经发生。',
    protect: '请立即关闭该页面，不要继续操作。如已在该站输入过任何凭据，请立刻：① 修改对应账号密码并开启二次验证；② 冻结相关银行卡并联系银行说明情况；③ 检查设备是否被安装了不明程序；④ 向国家反诈中心（96110）举报该网址。'
  },
  unknown: {
    type: '由于缺少足够的研判数据，本次未能给出明确结论。常见原因：目标站点无法访问、响应超时、跳转链路解析失败、或域名基础信息缺失。',
    method: '「无法检测」本身就是一个需要警惕的信号。正常经营 websites 通常可正常访问并返回页面，而刻意设置反爬、限制境外访问或随时下线的站点，更容易落入这一类。',
    harm: '结论未知意味着风险不可控。在明确安全之前，应当按「可疑」甚至「高危」的强度对待。',
    protect: '不建议贸然访问。如必须访问，请按可疑站点处理：不输入任何个人信息、不下载文件、不授权任何权限，并在访问后检查浏览器是否有异常弹窗或下载。'
  }
};

// 后端 AI 解读接口的约定路径。
// 当前后端尚未部署该接口（/health 的 7 个接口里没有它），
// 补上后前端无需改动即可自动切换。
const AI_INTERPRET_PATH = '/ai/interpret';

/**
 * 调用后端 AI 解读接口。
 *
 * 约定：POST /api/v1/ai/interpret
 *   请求体 { url, level, label, risks }
 *   期望响应 data.interpret = { type, method, harm, protect }
 *
 * 返回结构已统一成渲染层直接可用的形状：
 *   { ok:true,  source:'ai'|'local', data:{type,method,harm,protect}, msg }
 *   { ok:false, source:'ai',    msg }   ← 只有接口明确失败时才走这里
 *
 * 注意：接口不可用（未部署/超时/网络不通）时**不返回 ok:false**，
 * 而是返回 ok:true + source:'local'，由本地规则解读兜底。
 * 这样前端只需处理一种成功路径。
 */
async function fetchInterpret(url, level, label, risks, opts) {
  const options = opts || {};
  const timeout = options.timeout || 20000;   // AI 生成比普通检测慢，给 20 秒

  // 控制器：超时后主动中断，避免弹窗一直转圈
  const ctrl = typeof AbortController !== 'undefined' ? new AbortController() : null;
  const timer = ctrl ? setTimeout(() => ctrl.abort(), timeout) : null;

  try {
    const resp = await fetch(API_BASE + AI_INTERPRET_PATH, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        url: url,
        level: level || 'unknown',
        label: label || '',
        risks: Array.isArray(risks) ? risks : []
      }),
      signal: ctrl ? ctrl.signal : undefined
    });

    const payload = await resp.json();

    // 后端 HTTP 层恒 200，业务错误码在 body.code。
    // 404 表示「接口未部署」——这是当前后端的实际情况（/health 的 7 个接口里没有它），
    // 不该报错弹窗，而应静默降级到本地规则解读。
    const code = payload ? payload.code : null;

    // 业务失败：接口存在但这次调用失败了。
    // **不降级**，因为这时说明后端已部署，只是这次没成功，用户应该看到真实情况。
    if (!payload || code !== 200) {
      if (code === 404) {
        // 接口未部署 → 本地解读兜底，功能当场可用
        return Object.assign(
          { ok: true, source: 'local' },
          { data: buildLocalInterpret(url, level, risks), msg: '' }
        );
      }
      return {
        ok: false,
        source: 'ai',
        msg: (payload && payload.msg) || 'AI 解读服务返回异常',
        code: code
      };
    }

    const d = payload.data || {};
    // 后端可能把四段放在 data.interpret 里，也可能直接摊在 data 上，两种都兼容
    const raw = d.interpret || d;
    const content = {
      type: raw.type || raw.riskType || '',
      method: raw.method || raw.technique || '',
      harm: raw.harm || raw.damage || '',
      protect: raw.protect || raw.advice || ''
    };

    // 四段全空说明后端返回的不是预期结构，当作不可用，降级到本地
    const empty = INTERPRET_SECTIONS.every(s => !content[s.key]);
    if (empty) {
      return Object.assign(
        { ok: true, source: 'local' },
        { data: buildLocalInterpret(url, level, risks), msg: '' }
      );
    }

    return { ok: true, source: 'ai', data: content, msg: '' };

  } catch (e) {
    // 网络层失败：接口未部署 / 超时 / 后端没启动。
    // 这是最常见的情况，静默降级到本地解读，用户仍能看到完整内容。
    return Object.assign(
      { ok: true, source: 'local' },
      { data: buildLocalInterpret(url, level, risks), msg: '' }
    );
  } finally {
    if (timer) clearTimeout(timer);
  }
}

/**
 * 本地规则版解读：用风险等级 + 实际命中的风险线索生成四段内容。
 * 完全离线，不发任何请求。
 *
 * @param {string} url 网址
 * @param {string} level 风险等级
 * @param {Array} risks 风险线索清单（来自检测结果）
 * @returns {Object} { type, method, harm, protect }
 */
function buildLocalInterpret(url, level, risks) {
  const lv = LEVEL_TEXT[level] ? level : 'unknown';
  const lib = INTERPRET_LIB[lv] || INTERPRET_LIB.unknown;
  const list = Array.isArray(risks) ? risks.filter(Boolean) : [];

  // 把这条链接实际命中的线索拼进「风险类型说明」，
  // 让解读针对当前这条链接，而不是一段通用文案。
  let type = lib.type;
  if (list.length) {
    type += '\n\n本条链接命中的具体线索：\n' + list.map((r, i) => '· ' + r).join('\n');
  }

  return {
    type: type,
    method: lib.method,
    harm: lib.harm,
    protect: lib.protect
  };
}

/**
 * 把解读对象按固定四段结构拆成数组，供前端渲染。
 * 缺哪段就返回哪段，不做填充，避免用无意义的占位符充数。
 *
 * @param {Object} content { type, method, harm, protect }
 * @returns {Array} [{ key, title, icon, text }]
 */
function toInterpretSections(content) {
  const c = content || {};
  return INTERPRET_SECTIONS.map(function (s) {
    return {
      key: s.key,
      title: s.title,
      icon: s.icon,
      text: c[s.key] || ''
    };
  }).filter(function (s) {
    return s.text && String(s.text).trim() !== '';
  });
}

// ============================================================
// 十一、检测结果标签筛选（功能6）
// ------------------------------------------------------------
// 纯逻辑，不碰 DOM、不发请求。负责两件事：
//   1. 定义标签体系（含每个标签的判定规则与适用区域）
//   2. 判断一条记录是否命中选中的标签集合
//
// 【标签分两类的由来】
//   第一类「风险等级」：高危/可疑/安全/未知
//     —— 来自 riskLabel，两边数据都有，判定可靠。
//   第二类「跳转与降级特征」：抓取失败/解析失败/短链中转/跳转风险
//     —— 需要 hops、isLoop、note 这些明细字段。
//     批量检测的结果在内存里，字段齐全；但历史接口只返回
//        id/originalUrl/finalUrl/domain/riskLevel/riskLabel/riskCount/
//        expandHops/isOverseas/icpSubject/source/createdAt
//        —— 没有 risks 明细、没有 isLoop、没有 degradeNote，
//        硬猜会给出误导性结果。所以这四个标签在历史区域置灰不可点，
//        鼠标悬浮提示「该标签暂不支持历史记录筛选，仅批量结果可用」。
//        这是有意为之的诚实降级，不是没做完。
// ============================================================

// 标签体系。scope 字段决定该标签在哪些区域可用：
//   'both'   两个区域都可用
//   'batch'  仅批量结果可用（依赖明细字段，历史接口不返回）
const FILTER_TAGS = [
  { key: 'danger',     label: '高危',     color: 'danger',     scope: 'both' },
  { key: 'suspicious', label: '可疑',     color: 'warn',       scope: 'both' },
  { key: 'safe',       label: '安全',     color: 'safe',       scope: 'both' },
  { key: 'unknown',    label: '未知',     color: 'unknown',    scope: 'both' },
  { key: 'fetchFail',  label: '抓取失败', color: 'unknown',    scope: 'batch' },
  { key: 'parseFail',  label: '解析失败', color: 'unknown',    scope: 'batch' },
  { key: 'redirect',   label: '短链中转', color: 'suspicious', scope: 'batch' },
  { key: 'hopRisk',    label: '跳转风险', color: 'warn',       scope: 'batch' }
];

// 历史区域不可用的标签，悬浮时显示这句
const FILTER_SCOPE_HINT = '该标签暂不支持历史记录筛选，仅批量结果可用';

/**
 * 判断一条记录在指定区域是否命中给定的标签集合。
 *
 * @param {Object} row  原始记录（批量用 r.url/level/label/hops/note/isLoop；
 *                      历史用 it.originalUrl/riskLevel/riskLabel/expandHops/finalUrl）
 * @param {Array} keys 选中的标签 key 数组。为空表示不筛选，全部命中。
 * @param {'batch'|'history'} area 区域
 * @returns {boolean}
 */
function matchFilterTags(row, keys, area) {
  // 没选中任何标签 → 不筛选，全部保留
  if (!Array.isArray(keys) || keys.length === 0) return true;
  // 记录本身不存在时不匹配任何标签
  if (!row) return false;

  // ---------- 风险等级类：两边都可靠 ----------
  // 历史用 riskLevel，批量用 level
  const lv = area === 'history' ? (row.riskLevel || 'unknown') : (row.level || 'unknown');

  // ---------- 特征类：只有批量数据有明细 ----------
  const hops = area === 'history' ? (row.expandHops || 0) : (row.hops || 0);
  const note = String(row.note || '');
  const isLoop = !!(row.isLoop || row.loopExceeded);
  const url = (area === 'history' ? row.originalUrl : row.url) || '';
  const finalUrl = row.finalUrl || '';

  // 多选：命中任意一个即保留
  return keys.some(function (k) {
    switch (k) {
      case 'danger':     return lv === 'danger';
      case 'suspicious': return lv === 'suspicious';
      case 'safe':       return lv === 'safe';
      case 'unknown':    return lv === 'unknown';

      // ---- 以下四个仅批量区域可用 ----

      // 抓取失败：检测结论未知，且原因指向页面抓取/访问失败
      case 'fetchFail':
        return lv === 'unknown' &&
          /抓取失败|无法访问|连接失败|访问失败|目标站点连接失败|请求超时|超时/.test(note);

      // 解析失败：短链/跳转链路解析不出来
      case 'parseFail':
        return lv === 'unknown' &&
          (/解析失败|短链解析|展开失败|解析服务|解析不可用/.test(note) ||
           // 有可能是短链但没能展开出目标地址
           (/^https?:\/\/[^/]*\.(top|xyz|link|click|vip|wang)\b/i.test(url) && !finalUrl));

      // 短链中转：发生了跳转（原始地址与最终地址不同）
      case 'redirect':
        return hops > 0 || (!!finalUrl && !!url && finalUrl !== url);

      // 跳转风险：跳转层数偏多，或出现循环跳转
      case 'hopRisk':
        return isLoop || hops >= 3;

      default:
        return false;
    }
  });
}

/**
 * 对记录数组套用筛选。
 * 注意：这里只做**过滤**，不改动原数组，也不改记录内容。
 *
 * @param {Array} rows
 * @param {Array} keys 选中的标签 key
 * @param {'batch'|'history'} area
 * @returns {Array} 过滤后的新数组
 */
function applyFilterTags(rows, keys, area) {
  const list = Array.isArray(rows) ? rows : [];
  // 没选中标签时原样返回同一个引用，避免不必要的拷贝
  if (!Array.isArray(keys) || keys.length === 0) return list;
  return list.filter(function (r) {
    return matchFilterTags(r, keys, area);
  });
}

/**
 * 统计每个标签在给定数据里能命中多少条。
 * 用于在标签上显示条数，让用户点之前就知道哪个标签有货。
 *
 * @param {Array} rows
 * @param {'batch'|'history'} area
 * @returns {Object} { 标签key: 命中条数 }
 */
function countByFilterTags(rows, area) {
  const list = Array.isArray(rows) ? rows : [];
  const out = {};
  FILTER_TAGS.forEach(function (t) {
    // 历史区域不可用的标签不参与统计（前端也不显示它的条数）
    if (area === 'history' && t.scope !== 'both') { out[t.key] = 0; return; }
    out[t.key] = list.filter(function (r) {
      return matchFilterTags(r, [t.key], area);
    }).length;
  });
  return out;
}

/**
 * 校验一组标签 key 在指定区域是否都可用。
 * 用于历史区域：若用户切到历史前选中过 batch-only 标签，
 * 调用方据此提示或自动剔除，避免「点了没反应」。
 *
 * @param {Array} keys
 * @param {'batch'|'history'} area
 * @returns {Array} 该区域内可用的 key 列表
 */
function availableTagsFor(keys, area) {
  const list = Array.isArray(keys) ? keys : [];
  if (area !== 'history') return list.slice();
  return list.filter(function (k) {
    const t = FILTER_TAGS.find(function (x) { return x.key === k; });
    return t && t.scope === 'both';
  });
}

/**
 * 按中文标签名取 key。给「导出当前筛选结果」这类需要展示文案的地方用。
 * @returns {string} key，未找到返回 ''
 */
function filterTagKeyByLabel(label) {
  const t = FILTER_TAGS.find(function (x) { return x.label === label; });
  return t ? t.key : '';
}

// ============================================================
// 十一·补、本地收藏与预设模板的纯逻辑（功能9 / 功能10）
// ------------------------------------------------------------
// 纯逻辑，不碰 DOM、不发请求。localStorage 的读写放 index.html，
// 这里只提供与「收藏」「模板」相关的确定性计算，方便 Node 单测。
// ============================================================

// 风险等级中文标签 → 英文 key 的反向映射。
// 收藏项只存 riskLabel（中文），筛选时引擎按英文 key 判断，
// 这里把中文映射回英文，使收藏面板能直接复用标签筛选逻辑。
const LABEL_TO_LEVEL = {
  '安全': 'safe',
  '可疑': 'suspicious',
  '高危': 'danger',
  '未知': 'unknown'
};

// 收藏夹存储的 key（与 index.html 读写约定一致）
const FAVORITES_KEY = 'wzh_favorites';
// 自定义模板存储的 key
const TEMPLATES_KEY = 'wzh_templates';

/**
 * 把一条收藏项转成「可用于标签筛选 / 导出」的视图对象。
 * 收藏项本身只存 { originalUrl, riskLabel, finalUrl, createdAt }，
 * 这里补上 riskLevel（由中文标签反推）和 expandHops（收藏无跳转明细，补 0），
 * 使它能直接交给 E.applyFilterTags(..., 'history') 和 E.normalizeExportRows。
 *
 * @param {Object} item 收藏项 { originalUrl, riskLabel, finalUrl, createdAt }
 * @returns {Object} { originalUrl, riskLabel, riskLevel, finalUrl, createdAt, expandHops }
 */
function favoriteView(item) {
  const it = item || {};
  const label = it.riskLabel || '未知';
  return {
    originalUrl: it.originalUrl || '',
    riskLabel: label,
    riskLevel: LABEL_TO_LEVEL[label] || 'unknown',
    finalUrl: it.finalUrl || '',
    createdAt: it.createdAt || '',
    expandHops: 0
  };
}

/**
 * 收藏去重：按 originalUrl 判断是否已收藏。
 * @param {Array} favs 收藏数组
 * @param {string} url 原始网址
 * @returns {boolean}
 */
function isFavorited(favs, url) {
  if (!url) return false;
  const list = Array.isArray(favs) ? favs : [];
  return list.some(function (it) { return (it && it.originalUrl) === url; });
}

/**
 * 移除某条收藏（按 originalUrl），返回新数组（不修改原数组）。
 * @param {Array} favs
 * @param {string} url
 * @returns {Array}
 */
function removeFavorite(favs, url) {
  const list = Array.isArray(favs) ? favs : [];
  return list.filter(function (it) { return !(it && it.originalUrl === url); });
}

// ============================================================
// 十二、快捷复制与二维码预览（功能7）
// ------------------------------------------------------------
// 纯逻辑，不碰 DOM、不发请求。
// 二维码按 QR Code Model 2 规范本地实现：字节模式（UTF-8）、
// 版本 1~40 自动选择（长网址自动升级到更高版本）、
// 纠错级别默认 M（过长时自动降级到 L 换取更大容量）、
// 8 种掩码按罚分自动选最优。
// 这里只产出矩阵数据（true=深色模块），画到 canvas 由 index.html 负责。
// 复制到剪贴板涉及 navigator/document，放在 index.html，不在这里。
// ============================================================

// ---------- GF(256) 有限域（Reed-Solomon 纠错用）----------
// 本原多项式 0x11d。预建 exp/log 表，把乘法变成加法。
const QR_GF = (function () {
  const exp = new Array(512);
  const log = new Array(256);
  let x = 1;
  for (let i = 0; i < 255; i++) {
    exp[i] = x;
    log[x] = i;
    x <<= 1;
    if (x & 0x100) x ^= 0x11d;
  }
  for (let i = 255; i < 512; i++) exp[i] = exp[i - 255];
  return { exp: exp, log: log };
})();

function qrGfMul(a, b) {
  if (a === 0 || b === 0) return 0;
  return QR_GF.exp[QR_GF.log[a] + QR_GF.log[b]];
}

// Reed-Solomon 生成多项式（系数最高次在前）
function qrRsGenPoly(degree) {
  let poly = [1];
  for (let i = 0; i < degree; i++) {
    const next = new Array(poly.length + 1).fill(0);
    for (let j = 0; j < poly.length; j++) {
      next[j] ^= qrGfMul(poly[j], QR_GF.exp[i]);
      next[j + 1] ^= poly[j];
    }
    poly = next;
  }
  // 上面是「常数项在前」建出来的，翻转成「最高次在前」，
  // 让余数计算的 gen[j+1] 索引与 x^(degree-1-j) 一一对应。
  return poly.reverse();
}

// 由数据码字算出纠错码字
function qrRsRemainder(data, ecCount) {
  const gen = qrRsGenPoly(ecCount);
  const res = new Array(ecCount).fill(0);
  for (let i = 0; i < data.length; i++) {
    const factor = data[i] ^ res[0];
    res.shift();
    res.push(0);
    if (factor !== 0) {
      for (let j = 0; j < ecCount; j++) {
        res[j] ^= qrGfMul(gen[j + 1], factor);
      }
    }
  }
  return res;
}

// ---------- 纠错级别 ----------
// 索引用于 RS 分块表；QR_ECL_BITS 按索引给出格式信息里的 2 位编码
// （L=01, M=00, Q=11, H=10）。
const QR_ECL = { L: 0, M: 1, Q: 2, H: 3 };
const QR_ECL_BITS = [1, 0, 3, 2];

// ---------- RS 分块表（版本 1~40 × 纠错级别 L/M/Q/H）----------
// 每项 = [块数, 每块总码字数, 每块数据码字数]
const QR_RS_TABLE = [
  [[[1,26,19]],[[1,26,16]],[[1,26,13]],[[1,26,9]]],
  [[[1,44,34]],[[1,44,28]],[[1,44,22]],[[1,44,16]]],
  [[[1,70,55]],[[1,70,44]],[[2,35,17]],[[2,35,13]]],
  [[[1,100,80]],[[2,50,32]],[[2,50,24]],[[4,25,9]]],
  [[[1,134,108]],[[2,67,43]],[[2,33,15],[2,34,16]],[[2,33,11],[2,34,12]]],
  [[[2,86,68]],[[4,43,27]],[[4,43,19]],[[4,43,15]]],
  [[[2,98,78]],[[4,49,31]],[[2,32,14],[4,33,15]],[[4,39,13],[1,40,14]]],
  [[[2,121,97]],[[2,60,38],[2,61,39]],[[4,40,18],[2,41,19]],[[4,40,14],[2,41,15]]],
  [[[2,146,116]],[[3,58,36],[2,59,37]],[[4,36,16],[4,37,17]],[[4,36,12],[4,37,13]]],
  [[[2,86,68],[2,87,69]],[[4,69,43],[1,70,44]],[[6,43,19],[2,44,20]],[[6,43,15],[2,44,16]]],
  [[[4,101,81]],[[1,80,50],[4,81,51]],[[4,50,22],[4,51,23]],[[3,36,12],[8,37,13]]],
  [[[2,116,92],[2,117,93]],[[6,58,36],[2,59,37]],[[4,46,20],[6,47,21]],[[7,42,14],[4,43,15]]],
  [[[4,133,107]],[[8,59,37],[1,60,38]],[[8,44,20],[4,45,21]],[[12,33,11],[4,34,12]]],
  [[[3,145,115],[1,146,116]],[[4,64,40],[5,65,41]],[[11,36,16],[5,37,17]],[[11,36,12],[5,37,13]]],
  [[[5,109,87],[1,110,88]],[[5,65,41],[5,66,42]],[[5,54,24],[7,55,25]],[[11,36,12],[7,37,13]]],
  [[[5,122,98],[1,123,99]],[[7,73,45],[3,74,46]],[[15,43,19],[2,44,20]],[[3,45,15],[13,46,16]]],
  [[[1,135,107],[5,136,108]],[[10,74,46],[1,75,47]],[[1,50,22],[15,51,23]],[[2,42,14],[17,43,15]]],
  [[[5,150,120],[1,151,121]],[[9,69,43],[4,70,44]],[[17,50,22],[1,51,23]],[[2,42,14],[19,43,15]]],
  [[[3,141,113],[4,142,114]],[[3,70,44],[11,71,45]],[[17,47,21],[4,48,22]],[[9,39,13],[16,40,14]]],
  [[[3,135,107],[5,136,108]],[[3,67,41],[13,68,42]],[[15,54,24],[5,55,25]],[[15,43,15],[10,44,16]]],
  [[[4,144,116],[4,145,117]],[[17,68,42]],[[17,50,22],[6,51,23]],[[19,46,16],[6,47,17]]],
  [[[2,139,111],[7,140,112]],[[17,74,46]],[[7,54,24],[16,55,25]],[[34,37,13]]],
  [[[4,151,121],[5,152,122]],[[4,75,47],[14,76,48]],[[11,54,24],[14,55,25]],[[16,45,15],[14,46,16]]],
  [[[6,147,117],[4,148,118]],[[6,73,45],[14,74,46]],[[11,54,24],[16,55,25]],[[30,46,16],[2,47,17]]],
  [[[8,132,106],[4,133,107]],[[8,75,47],[13,76,48]],[[7,54,24],[22,55,25]],[[22,45,15],[13,46,16]]],
  [[[10,142,114],[2,143,115]],[[19,74,46],[4,75,47]],[[28,50,22],[6,51,23]],[[33,46,16],[4,47,17]]],
  [[[8,152,122],[4,153,123]],[[22,73,45],[3,74,46]],[[8,53,23],[26,54,24]],[[12,45,15],[28,46,16]]],
  [[[3,147,117],[10,148,118]],[[3,73,45],[23,74,46]],[[4,54,24],[31,55,25]],[[11,45,15],[31,46,16]]],
  [[[7,146,116],[7,147,117]],[[21,73,45],[7,74,46]],[[1,53,23],[37,54,24]],[[19,45,15],[26,46,16]]],
  [[[5,145,115],[10,146,116]],[[19,75,47],[10,76,48]],[[15,54,24],[25,55,25]],[[23,45,15],[25,46,16]]],
  [[[13,145,115],[3,146,116]],[[2,74,46],[29,75,47]],[[42,54,24],[1,55,25]],[[23,45,15],[28,46,16]]],
  [[[17,145,115]],[[10,74,46],[23,75,47]],[[10,54,24],[35,55,25]],[[19,45,15],[35,46,16]]],
  [[[17,145,115],[1,146,116]],[[14,74,46],[21,75,47]],[[29,54,24],[19,55,25]],[[11,45,15],[46,46,16]]],
  [[[13,145,115],[6,146,116]],[[14,74,46],[23,75,47]],[[44,54,24],[7,55,25]],[[59,46,16],[1,47,17]]],
  [[[12,151,121],[7,152,122]],[[12,75,47],[26,76,48]],[[39,54,24],[14,55,25]],[[22,45,15],[41,46,16]]],
  [[[6,151,121],[14,152,122]],[[6,75,47],[34,76,48]],[[46,54,24],[10,55,25]],[[2,45,15],[64,46,16]]],
  [[[17,152,122],[4,153,123]],[[29,74,46],[14,75,47]],[[49,54,24],[10,55,25]],[[24,45,15],[46,46,16]]],
  [[[4,152,122],[18,153,123]],[[13,74,46],[32,75,47]],[[48,54,24],[14,55,25]],[[42,45,15],[32,46,16]]],
  [[[20,147,117],[4,148,118]],[[40,75,47],[7,76,48]],[[43,54,24],[22,55,25]],[[10,45,15],[67,46,16]]],
  [[[19,148,118],[6,149,119]],[[18,75,47],[31,76,48]],[[34,54,24],[34,55,25]],[[20,45,15],[61,46,16]]]
];

// ---------- 对齐图案中心坐标（版本 1 无对齐图案）----------
const QR_ALIGN = [
  [],
  [6,18], [6,22], [6,26], [6,30], [6,34],
  [6,22,38], [6,24,42], [6,26,46], [6,28,50], [6,30,54],
  [6,32,58], [6,34,62], [6,26,46,66], [6,26,48,70], [6,26,50,74],
  [6,30,54,78], [6,30,56,82], [6,30,58,86], [6,34,62,90], [6,28,50,72,94],
  [6,26,50,74,98], [6,30,54,78,102], [6,28,54,80,106], [6,32,58,84,110], [6,30,58,86,114],
  [6,34,62,90,118], [6,26,50,74,98,122], [6,30,54,78,102,126], [6,26,52,78,104,130], [6,30,56,82,108,134],
  [6,34,60,86,112,138], [6,30,58,86,114,142], [6,34,62,90,118,146], [6,30,54,78,102,126,150], [6,24,50,76,102,128,154],
  [6,28,54,80,106,132,158], [6,32,58,84,110,136,162], [6,26,54,82,110,138,166], [6,30,58,86,114,142,170]
];

// UTF-8 编码（自实现，避免依赖环境里的 TextEncoder）
function qrUtf8Bytes(str) {
  const out = [];
  for (let i = 0; i < str.length; i++) {
    let c = str.codePointAt(i);
    if (c > 0xffff) i++;          // 跳过代理对的低半部分
    if (c < 0x80) out.push(c);
    else if (c < 0x800) out.push(0xc0 | (c >> 6), 0x80 | (c & 0x3f));
    else if (c < 0x10000) out.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
    else out.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 0x3f), 0x80 | ((c >> 6) & 0x3f), 0x80 | (c & 0x3f));
  }
  return out;
}

// 某版本 + 纠错级别下，数据码字总数
function qrDataCapacity(version, ecl) {
  const spec = QR_RS_TABLE[version - 1][ecl];
  return spec.reduce(function (s, b) { return s + b[0] * b[2]; }, 0);
}

// 某版本 + 纠错级别下，字节模式能装下的最大字节数
function qrMaxBytes(version, ecl) {
  const dataBits = qrDataCapacity(version, ecl) * 8;
  const countBits = version < 10 ? 8 : 16;
  return Math.floor((dataBits - 4 - countBits) / 8);
}

// 数据字节 → 完整的码字序列（含模式、长度、填充、分块、纠错、交织）
function qrBuildCodewords(bytes, version, ecl) {
  const spec = QR_RS_TABLE[version - 1][ecl];
  const totalData = qrDataCapacity(version, ecl);
  const bits = [];
  function pushBits(value, n) {
    for (let i = n - 1; i >= 0; i--) bits.push((value >> i) & 1);
  }
  // 字节模式指示符 0100
  pushBits(4, 4);
  // 字符计数（版本 1~9 用 8 位，10~40 用 16 位）
  pushBits(bytes.length, version < 10 ? 8 : 16);
  for (let i = 0; i < bytes.length; i++) pushBits(bytes[i], 8);

  const dataBitsCapacity = totalData * 8;
  // 终止符：最多 4 个 0
  const terminator = Math.min(4, dataBitsCapacity - bits.length);
  for (let i = 0; i < terminator; i++) bits.push(0);
  // 补到字节边界
  while (bits.length % 8 !== 0) bits.push(0);
  // 交替填充 0xEC / 0x11
  let padIdx = 0;
  while (bits.length < dataBitsCapacity) {
    const pad = (padIdx % 2 === 0) ? 0xEC : 0x11;
    for (let i = 7; i >= 0; i--) bits.push((pad >> i) & 1);
    padIdx++;
  }
  // 位 → 字节
  const dataCodewords = [];
  for (let i = 0; i < bits.length; i += 8) {
    let v = 0;
    for (let j = 0; j < 8; j++) v = (v << 1) | bits[i + j];
    dataCodewords.push(v);
  }

  // 分块 + 纠错
  const blocks = [];
  let di = 0;
  for (let k = 0; k < spec.length; k++) {
    const count = spec[k][0], total = spec[k][1], dataLen = spec[k][2];
    for (let m = 0; m < count; m++) {
      const d = dataCodewords.slice(di, di + dataLen);
      di += dataLen;
      blocks.push({ data: d, ec: qrRsRemainder(d, total - dataLen) });
    }
  }
  // 交织：先所有块的第 1 个数据码字、第 2 个…… 再所有块的纠错码字
  const out = [];
  const maxDataLen = blocks.reduce(function (m, b) { return Math.max(m, b.data.length); }, 0);
  for (let i = 0; i < maxDataLen; i++) {
    for (let b = 0; b < blocks.length; b++) if (i < blocks[b].data.length) out.push(blocks[b].data[i]);
  }
  const ecLen = blocks[0].ec.length;
  for (let i = 0; i < ecLen; i++) {
    for (let b = 0; b < blocks.length; b++) out.push(blocks[b].ec[i]);
  }
  return out;
}

// 格式信息（15 位）：5 位数据 + 10 位 BCH，再与 0x5412 异或
function qrFormatInfoValue(eclBits, mask) {
  const data = (eclBits << 3) | mask;
  let d = data << 10;
  const G = 0x537;
  for (let i = 14; i >= 10; i--) {
    if ((d >> i) & 1) d ^= (G << (i - 10));
  }
  return ((data << 10) | d) ^ 0x5412;
}

// 版本信息（18 位）：版本 >= 7 才有
function qrVersionInfoValue(version) {
  const G = 0x1f25;
  let d = version << 12;
  for (let i = 17; i >= 12; i--) {
    if ((d >> i) & 1) d ^= (G << (i - 12));
  }
  return (version << 12) | d;
}

// 掩码条件：返回 true 表示该位置要翻转
function qrMaskApplies(mask, r, c) {
  switch (mask) {
    case 0: return (r + c) % 2 === 0;
    case 1: return r % 2 === 0;
    case 2: return c % 3 === 0;
    case 3: return (r + c) % 3 === 0;
    case 4: return (Math.floor(r / 2) + Math.floor(c / 3)) % 2 === 0;
    case 5: return (r * c) % 2 + (r * c) % 3 === 0;
    case 6: return ((r * c) % 2 + (r * c) % 3) % 2 === 0;
    case 7: return ((r + c) % 2 + (r * c) % 3) % 2 === 0;
    default: return false;
  }
}

// 罚分（4 条规则）
function qrPenalty(matrix, size) {
  let p = 0;
  // 规则 1：连续同色 ≥ 5
  for (let r = 0; r < size; r++) {
    let run = 1;
    for (let c = 1; c < size; c++) {
      if (matrix[r][c] === matrix[r][c - 1]) run++;
      else { if (run >= 5) p += 3 + (run - 5); run = 1; }
    }
    if (run >= 5) p += 3 + (run - 5);
  }
  for (let c = 0; c < size; c++) {
    let run = 1;
    for (let r = 1; r < size; r++) {
      if (matrix[r][c] === matrix[r - 1][c]) run++;
      else { if (run >= 5) p += 3 + (run - 5); run = 1; }
    }
    if (run >= 5) p += 3 + (run - 5);
  }
  // 规则 2：2×2 同色块
  for (let r = 0; r < size - 1; r++) {
    for (let c = 0; c < size - 1; c++) {
      if (matrix[r][c] === matrix[r][c + 1] && matrix[r][c] === matrix[r + 1][c] && matrix[r][c] === matrix[r + 1][c + 1]) p += 3;
    }
  }
  // 规则 3：定位图案样（1011101 两侧各 4 个白）
  const PAT1 = [true, false, true, true, true, false, true, false, false, false, false];
  const PAT2 = [false, false, false, false, true, false, true, true, true, false, true];
  for (let r = 0; r < size; r++) {
    for (let c = 0; c < size - 10; c++) {
      let ok1 = true, ok2 = true;
      for (let k = 0; k < 11; k++) {
        if (matrix[r][c + k] !== PAT1[k]) ok1 = false;
        if (matrix[r][c + k] !== PAT2[k]) ok2 = false;
      }
      if (ok1 || ok2) p += 40;
    }
  }
  for (let c = 0; c < size; c++) {
    for (let r = 0; r < size - 10; r++) {
      let ok1 = true, ok2 = true;
      for (let k = 0; k < 11; k++) {
        if (matrix[r + k][c] !== PAT1[k]) ok1 = false;
        if (matrix[r + k][c] !== PAT2[k]) ok2 = false;
      }
      if (ok1 || ok2) p += 40;
    }
  }
  // 规则 4：深色占比偏离 50%
  let dark = 0;
  for (let r = 0; r < size; r++) for (let c = 0; c < size; c++) if (matrix[r][c]) dark++;
  p += Math.floor(Math.abs(dark / (size * size) * 100 - 50) / 5) * 10;
  return p;
}

// 写入格式信息（两处）
function qrPlaceFormatInfo(matrix, version, ecl, mask, size) {
  // ecl 是 QR_ECL 的索引（0=L,1=M,2=Q,3=H），QR_ECL_BITS 也按同一索引存 2 位编码
  const value = qrFormatInfoValue(QR_ECL_BITS[ecl], mask);
  const A = [[8,0],[8,1],[8,2],[8,3],[8,4],[8,5],[8,7],[8,8],[7,8],[5,8],[4,8],[3,8],[2,8],[1,8],[0,8]];
  const B = [[size-1,8],[size-2,8],[size-3,8],[size-4,8],[size-5,8],[size-6,8],[size-7,8],[8,size-8],[8,size-7],[8,size-6],[8,size-5],[8,size-4],[8,size-3],[8,size-2],[8,size-1]];
  for (let i = 0; i < 15; i++) {
    // i=0 是 MSB（放在 (8,0)），所以取第 (14-i) 位
    const bit = ((value >> (14 - i)) & 1) === 1;
    matrix[A[i][0]][A[i][1]] = bit;
    matrix[B[i][0]][B[i][1]] = bit;
  }
}

// 写入版本信息（版本 >= 7）
function qrPlaceVersionInfo(matrix, version, size) {
  const value = qrVersionInfoValue(version);
  for (let i = 0; i < 18; i++) {
    // 版本信息是 LSB-first：i=0（最低位）放在 (size-11,0)，与格式信息的位序相反
    const bit = ((value >> i) & 1) === 1;
    const a = size - 11 + (i % 3);
    const b = Math.floor(i / 3);
    matrix[a][b] = bit;
    matrix[b][a] = bit;
  }
}

// 组装矩阵（功能图案 + 数据 + 掩码选择）
function qrBuildMatrix(codewords, version, ecl) {
  const size = version * 4 + 17;
  const modules = Array.from({ length: size }, function () { return new Array(size).fill(false); });
  const reserved = Array.from({ length: size }, function () { return new Array(size).fill(false); });

  function set(r, c, v, isFunc) {
    if (r < 0 || c < 0 || r >= size || c >= size) return;
    modules[r][c] = v;
    if (isFunc) reserved[r][c] = true;
  }

  // 定位图案 + 分隔符
  function placeFinder(r, c) {
    for (let dr = -1; dr <= 7; dr++) {
      for (let dc = -1; dc <= 7; dc++) {
        const rr = r + dr, cc = c + dc;
        if (rr < 0 || cc < 0 || rr >= size || cc >= size) continue;
        if (dr >= 0 && dr <= 6 && dc >= 0 && dc <= 6) {
          const dark = dr === 0 || dr === 6 || dc === 0 || dc === 6 || (dr >= 2 && dr <= 4 && dc >= 2 && dc <= 4);
          set(rr, cc, dark, true);
        } else {
          set(rr, cc, false, true);   // 分隔符（白）
        }
      }
    }
  }
  placeFinder(0, 0);
  placeFinder(0, size - 7);
  placeFinder(size - 7, 0);

  // 时序图案
  for (let i = 8; i < size - 8; i++) {
    set(6, i, i % 2 === 0, true);
    set(i, 6, i % 2 === 0, true);
  }

  // 对齐图案
  const ac = QR_ALIGN[version - 1];
  for (let ai = 0; ai < ac.length; ai++) {
    for (let aj = 0; aj < ac.length; aj++) {
      const r = ac[ai], c = ac[aj];
      if ((r <= 8 && c <= 8) || (r <= 8 && c >= size - 9) || (r >= size - 9 && c <= 8)) continue;
      for (let dr = -2; dr <= 2; dr++) {
        for (let dc = -2; dc <= 2; dc++) {
          const dark = Math.max(Math.abs(dr), Math.abs(dc)) !== 1;
          set(r + dr, c + dc, dark, true);
        }
      }
    }
  }

  // 深色模块（固定）。先标记 reserved、值留 false ——
  // 掩码选择阶段的罚分**不含**它（与主流实现一致），选完掩码再置为深色。
  set(size - 8, 8, false, true);

  // 预留格式信息区域（值稍后填，先标记为 reserved 让数据跳过）
  const A = [[8,0],[8,1],[8,2],[8,3],[8,4],[8,5],[8,7],[8,8],[7,8],[5,8],[4,8],[3,8],[2,8],[1,8],[0,8]];
  const B = [[size-1,8],[size-2,8],[size-3,8],[size-4,8],[size-5,8],[size-6,8],[size-7,8],[8,size-8],[8,size-7],[8,size-6],[8,size-5],[8,size-4],[8,size-3],[8,size-2],[8,size-1]];
  for (let i = 0; i < 15; i++) {
    reserved[A[i][0]][A[i][1]] = true;
    reserved[B[i][0]][B[i][1]] = true;
  }

  // 预留版本信息区域（版本 >= 7）
  if (version >= 7) {
    for (let i = 0; i < 18; i++) {
      const a = size - 11 + (i % 3);
      const b = Math.floor(i / 3);
      reserved[a][b] = true;
      reserved[b][a] = true;
    }
  }

  // 数据放置（自右下向左上，Z 字；每两列换一次方向）
  let bitIndex = 0;
  let upward = true;         // 最右一列向上
  let col = size - 1;
  while (col > 0) {
    if (col === 6) col--;    // 跳过时序列
    for (let i = 0; i < size; i++) {
      const row = upward ? size - 1 - i : i;
      for (let dc = 0; dc < 2; dc++) {
        const c = col - dc;
        if (!reserved[row][c]) {
          const bit = (bitIndex < codewords.length * 8)
            ? ((codewords[bitIndex >> 3] >> (7 - (bitIndex & 7))) & 1)
            : 0;
          modules[row][c] = bit === 1;
          bitIndex++;
        }
      }
    }
    col -= 2;
    upward = !upward;
  }

  // 8 种掩码，选罚分最低。
  // ⚠️ 罚分在「尚未写入格式信息/版本信息/深色模块」时算，
  //    否则格式信息随掩码变化会反过来影响评分，导致选错掩码。
  let best = null;
  for (let mask = 0; mask < 8; mask++) {
    const m = modules.map(function (r) { return r.slice(); });
    for (let r = 0; r < size; r++) {
      for (let c = 0; c < size; c++) {
        if (reserved[r][c]) continue;
        if (qrMaskApplies(mask, r, c)) m[r][c] = !m[r][c];
      }
    }
    const p = qrPenalty(m, size);
    if (!best || p < best.penalty) best = { matrix: m, penalty: p, mask: mask };
  }
  // 选定掩码后，再补上深色模块 + 格式信息 + 版本信息
  best.matrix[size - 8][8] = true;
  qrPlaceFormatInfo(best.matrix, version, ecl, best.mask, size);
  if (version >= 7) qrPlaceVersionInfo(best.matrix, version, size);
  return { size: size, matrix: best.matrix, version: version, ecl: ecl, mask: best.mask };
}

/**
 * 本地生成二维码矩阵（无后端依赖）。
 * @param {string} text 要编码的内容（网址）
 * @param {Object} [opts] { ecl?: 'L'|'M'|'Q'|'H' } 纠错级别，默认自动（优先 M，过长降 L）
 * @returns {Object} 成功 { ok:true, size, matrix, version, ecl }
 *                   失败 { ok:false, msg }
 */
function buildQrMatrix(text, opts) {
  const opt = opts || {};
  const s = String(text == null ? '' : text);
  if (s.length === 0) return { ok: false, msg: '链接为空，无法生成二维码' };
  const bytes = qrUtf8Bytes(s);

  // 用户显式指定纠错级别时只试这一档；否则 M → L 自动降级
  const levels = opt.ecl ? [opt.ecl] : ['M', 'L'];
  for (let li = 0; li < levels.length; li++) {
    const ecl = QR_ECL[levels[li]];
    for (let v = 1; v <= 40; v++) {
      if (bytes.length <= qrMaxBytes(v, ecl)) {
        const codewords = qrBuildCodewords(bytes, v, ecl);
        const res = qrBuildMatrix(codewords, v, ecl);
        return { ok: true, size: res.size, matrix: res.matrix, version: v, ecl: levels[li] };
      }
    }
  }
  return { ok: false, msg: '链接过长（' + bytes.length + ' 字节），无法生成二维码' };
}

// ============================================================
// 十三、导出（浏览器环境挂载 window，Node 环境走 module.exports）
// ============================================================
const API = {
  DISCLAIMER, ERROR_MSGS, RISK_SCIENCE, API_BASE,
  checkProhibited, extractUrls, isValidUrl, routeInput, localFeatures,
  detect, recommend, expandViaBackend, adaptDetectResponse,
  parseBatchInput, batchDetect,
  postJSON, requestJSON,
  THEME_KEY, getTheme, setTheme, toggleTheme,
  // 功能5：AI 风险解读
  INTERPRET_SECTIONS, INTERPRET_LIB, AI_INTERPRET_PATH,
  fetchInterpret, buildLocalInterpret, toInterpretSections,
  // 功能6：标签筛选
  FILTER_TAGS, FILTER_SCOPE_HINT,
  matchFilterTags, applyFilterTags, countByFilterTags, availableTagsFor, filterTagKeyByLabel,
  // 功能7：快捷复制与二维码预览
  buildQrMatrix,
  // 功能9：本地收藏
  LABEL_TO_LEVEL, FAVORITES_KEY, TEMPLATES_KEY,
  favoriteView, isFavorited, removeFavorite,
  // 功能4：导出
  LEVEL_TEXT, TXT_DIVIDER, pad2, buildReportFileName, formatDateTime,
  normalizeExportRows, buildReportText, downloadTxtFile,
  GARBAGE_TEXT,
};

if (typeof window !== 'undefined') window.WZH = API;
if (typeof module !== 'undefined' && module.exports) module.exports = API;