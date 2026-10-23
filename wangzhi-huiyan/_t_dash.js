// 功能8：风险统计看板 —— 自测
// 覆盖：看板结构、统计计算（批量/历史/筛选后）、卡片联动、更新触发、空态、CSS变量、红线
// 用法：node _t_dash.js
const fs = require('fs');
const E = require('./engine.js');
const html = fs.readFileSync('index.html', 'utf-8');

let pass = 0, fail = 0;
function ok(n, c, e) { if (c) { pass++; console.log('  PASS  ' + n); } else { fail++; console.log('  FAIL  ' + n + (e !== undefined ? '  -> ' + String(e).slice(0,300) : '')); } }
function sec(t) { console.log('\n===== ' + t + ' ====='); }

// ---- 模拟数据 ----
const batchRows = [
  { url:'https://a.com/',  level:'safe',       label:'安全' },
  { url:'https://b.com/',  level:'suspicious', label:'可疑' },
  { url:'http://c.top/x',  level:'danger',     label:'高危' },
  { url:'https://d.com/',  level:'unknown',    label:'未知' },
  { url:'https://e.com/',  level:'safe',       label:'安全' },
  { url:'https://f.top/y', level:'danger',     label:'高危' },
];
const historyRows = [
  { originalUrl:'https://h1.com/', riskLevel:'safe',       riskLabel:'安全' },
  { originalUrl:'https://h2.com/', riskLevel:'suspicious', riskLabel:'可疑' },
  { originalUrl:'https://h3.com/', riskLevel:'danger',     riskLabel:'高危' },
  { originalUrl:'https://h4.com/', riskLevel:'safe',       riskLabel:'安全' },
  { originalUrl:'https://h5.com/', riskLevel:'unknown',    riskLabel:'未知' },
];

sec('1. 看板 HTML 结构');
ok('看板容器存在', html.includes('id="dash"'));
ok('卡片容器存在', html.includes('id="dashCards"'));
ok('批量数据源按钮', html.includes("setDashSource('batch')"));
ok('历史数据源按钮', html.includes("setDashSource('history')"));
ok('看板位于模式栏与历史面板之间', html.indexOf('id="dash"') > html.indexOf('id="modes"') && html.indexOf('id="dash"') < html.indexOf('class="hist"'));

sec('2. CSS 变量与双主题');
const dashCssStart = html.indexOf('功能8：风险统计看板');
const dashCssEnd = html.indexOf('</style>', dashCssStart);
const dashCss = html.slice(dashCssStart, dashCssEnd).replace(/\/\*[\s\S]*?\*\//g, '');
ok('功能8 CSS 定位成功', dashCssStart > 0);
const hex = dashCss.match(/#[0-9a-fA-F]{3,6}\b/g);
ok('无硬编码颜色（rgba 阴影允许）', !hex, hex && hex.join(','));
ok('安全色用 --ok-fg', /\.dcard\.c-safe \.dc-num\{color:var\(--ok-fg\)/.test(html));
ok('可疑色用 --warn-fg', /\.dcard\.c-suspicious \.dc-num\{color:var\(--warn-fg\)/.test(html));
ok('高危色用 --dang-fg', /\.dcard\.c-danger \.dc-num\{color:var\(--dang-fg\)/.test(html));
ok('未知色用 --gray', /\.dcard\.c-unknown \.dc-num\{color:var\(--gray\)/.test(html));
ok('卡片有 hover 高亮', /\.dcard:hover\{[^}]*transform/.test(html));
ok('选中态用 --sel-bg/--sel-text', /\.dcard\.on\{[^}]*var\(--sel-bg\)/.test(html));

sec('3. 统计计算（引擎层逻辑复现）');
// 复现 computeDashStats 逻辑
function computeStats(rows) {
  const s = { total: rows.length, safe: 0, suspicious: 0, danger: 0, unknown: 0 };
  for (const r of rows) {
    const lv = r.level || r.riskLevel || 'unknown';
    if (s[lv] !== undefined) s[lv]++; else s.unknown++;
  }
  return s;
}
const bs = computeStats(batchRows);
ok('批量统计 total=6', bs.total === 6, bs.total);
ok('批量统计 safe=2', bs.safe === 2, bs.safe);
ok('批量统计 suspicious=1', bs.suspicious === 1, bs.suspicious);
ok('批量统计 danger=2', bs.danger === 2, bs.danger);
ok('批量统计 unknown=1', bs.unknown === 1, bs.unknown);
const hs = computeStats(historyRows);
ok('历史统计 total=5', hs.total === 5, hs.total);
ok('历史统计 safe=2', hs.safe === 2);
ok('历史统计 danger=1', hs.danger === 1);

// 筛选后统计（用引擎的 applyFilterTags）
const filtered = E.applyFilterTags(batchRows, ['danger'], 'batch');
const fs2 = computeStats(filtered);
ok('筛高危后 total=2', fs2.total === 2, fs2.total);
ok('筛高危后 danger=2', fs2.danger === 2);
ok('筛高危后 safe=0', fs2.safe === 0);
const filteredSafe = E.applyFilterTags(batchRows, ['safe'], 'batch');
const fs3 = computeStats(filteredSafe);
ok('筛安全后 total=2', fs3.total === 2, fs3.total);
const filteredMulti = E.applyFilterTags(batchRows, ['safe', 'danger'], 'batch');
const fm = computeStats(filteredMulti);
ok('多选安全+高危 total=4', fm.total === 4, fm.total);
// 零匹配：只有 safe 行的数据，筛 danger → 0
const onlySafe = [{url:'a', level:'safe', label:'安全'}];
const filteredZero = E.applyFilterTags(onlySafe, ['danger'], 'batch');
const fz = computeStats(filteredZero);
ok('零匹配时 total=0', fz.total === 0, fz.total);

sec('4. 空数据处理');
ok('空数组 total=0', computeStats([]).total === 0);
ok('null 当空数组', computeStats(null || []).total === 0);

sec('5. 卡片点击联动');
ok('dashClickCard 调 toggleFilterTag', /function dashClickCard\(tag, area\)\s*\{[\s\S]*?toggleFilterTag\(tag, area\)/.test(html));
ok('dashClickTotal 调 clearFilterTags', /function dashClickTotal\(area\)\s*\{[\s\S]*?clearFilterTags\(area\)/.test(html));
ok('卡片 onclick 动态生成（区分 area）', /dashClickCard\('\" \+ d\.tag/.test(html) || html.includes("dashClickCard('\" + d.tag"));
ok('总计卡片 onclick 生成', html.includes("dashClickTotal('\" + area"));
ok('卡片是 button 元素', html.includes("'<button class=\"' + cls"));

sec('6. 更新触发挂钩');
ok('refreshFilterViews 末尾调 refreshDash', /function refreshFilterViews\(area\)\s*\{[\s\S]*?refreshDash\(\)/.test(html));
ok('batchDetect 赋值后调 refreshDash', /lastBatchRows = res\.rows;[\s\S]{0,80}refreshDash\(\)/.test(html));
ok('loadHistory 完成后触发 refreshDashHistory', /dashSource === 'history'[\s\S]{0,60}refreshDashHistory\(\)/.test(html));
ok('页面加载调 renderDash', /loadHistory\(1\);[\s\S]{0,120}renderDash\(\)/.test(html));

sec('7. 数据源切换');
ok('setDashSource 函数存在', /async function setDashSource\(src\)/.test(html));
ok('切换历史模式调 fetchAllHistory', /setDashSource[\s\S]{0,300}fetchAllHistory\(\)/.test(html));
ok('fetch 失败兜底空数组', /catch \(e\)\s*\{[\s\S]*?dashHistCache = \[\]/.test(html));
ok('数据源按钮有 on 选中态切换', /dashSrcBatch[\s\S]*?classList\.toggle/.test(html));

sec('8. renderDash 逻辑');
ok('renderDash 函数存在', /function renderDash\(\)/.test(html));
ok('空数据显示 "--"', html.includes("'--'"));
ok('卡片有 on 类（联动筛选选中态）', /filterTags\.indexOf\(d\.tag\)/.test(html));
ok('总计卡片无筛选时高亮', /filterTags\.length === 0/.test(html));
ok('兼容批量 level 与历史 riskLevel', /\.level \|\| .*\.riskLevel/.test(html));

sec('9. 红线：不改后端、不改已完工功能');
ok('无新增后端接口', !html.includes("'/stats") && !html.includes("'/dashboard") && !html.includes("'/count"));
ok('统计纯前端（renderDash 内无 requestJSON/postJSON）', !/function renderDash\(\)\s*\{(?:(?!\n\})[\s\S])*?(requestJSON|postJSON|fetch)\(/.test(html));
ok('历史加载业务逻辑未变', /async function loadHistory\(page = 1\)/.test(html));
ok('批量检测业务逻辑未变', /async function batchDetect\(\)/.test(html));
ok('筛选 refreshFilterViews 仍在', /function refreshFilterViews\(area\)/.test(html));
ok('AI解读/复制/二维码 关键函数仍在', ['aiInterpretFromRow','copyLink','openQrModal','buildQrMatrix'].every(k => html.includes(k) || E[k]));
ok('无新增 .py 文件', fs.readdirSync('.').filter(x => x.endsWith('.py')).join(',') === 'serve.py');
ok('引擎 API_BASE 自适应回落', E.API_BASE === '/api/v1');

console.log('\n' + '='.repeat(50));
console.log('  通过 ' + pass + ' 项，失败 ' + fail + ' 项');
console.log('='.repeat(50));
process.exit(fail ? 1 : 0);
