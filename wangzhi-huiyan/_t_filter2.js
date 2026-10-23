// 功能6 结构 + 渲染逻辑自测
// 用法：node _t_filter2.js
const fs = require('fs');
const E = require('./engine.js');
const html = fs.readFileSync('index.html', 'utf-8');
const engine = fs.readFileSync('engine.js', 'utf-8');

let pass = 0, fail = 0;
function ok(n, c, e) { if (c) { pass++; console.log('  PASS  ' + n); } else { fail++; console.log('  FAIL  ' + n + (e !== undefined ? '  -> ' + String(e).slice(0,220) : '')); } }
function eq(n, a, b) { ok(n, a === b, 'got=' + JSON.stringify(a) + ' want=' + JSON.stringify(b)); }
function sec(t) { console.log('\n===== ' + t + ' ====='); }

sec('1. 控件存在性');
ok('历史筛选栏容器 #histFilterBar', html.includes('id="histFilterBar"'));
ok('筛选栏样式 .fbar', html.includes('.fbar{'));
ok('标签按钮样式 .ftag', html.includes('.ftag{'));
ok('清空按钮样式 .ftag.clear', html.includes('.ftag.clear{'));
ok('不可用态 .ftag.off', html.includes('.ftag.off{'));
ok('选中态 .ftag.on', html.includes('.ftag.on{'));
ok('条数样式 .ftag .n', html.includes('.ftag .n{'));
ok('结果提示 .fbar .rs', html.includes('.fbar .rs{'));

sec('2. JS 函数定义');
['buildFilterBarHtml','toggleFilterTag','clearFilterTags','refreshFilterViews',
 'rerenderBatchTable','renderHistoryBody'].forEach(fn =>
  ok('函数 ' + fn, new RegExp('function\\s+' + fn + '\\s*\\(').test(html)));

sec('3. 状态与缓存变量');
ok('filterTags 状态变量', /let filterTags = \[\];/.test(html));
ok('histRawItems 缓存', /let histRawItems = \[\];/.test(html));
ok('histAllItems 已彻底移除（无用死代码）', !/histAllItems/.test(html), (html.match(/histAllItems/g)||[]).length);
ok('两区共用同一 filterTags（状态保留的关键）',
   (html.match(/filterTags/g)||[]).length > 8, (html.match(/filterTags/g)||[]).length);

sec('4. 交互逻辑');
ok('点击标签切换（多选）', html.includes("if (i >= 0) filterTags.splice(i, 1);"));
ok('已选则取消', html.includes('else filterTags.push(key);'));
ok('清空筛选', /function clearFilterTags[\s\S]{0,200}filterTags = \[\];/.test(html));
ok('清空后两区都刷新', html.includes('refreshFilterViews(area);'));
ok('无匹配友好提示（需求原文）',
   html.includes('没有符合当前筛选条件的记录'), '缺少规定文案');
ok('历史空态提示未变', html.includes('还没有检测记录，去上面检测一个网址试试～'));
ok('批量空态提示未变', html.includes('没有可展示的结果'));
ok('不可用标签带 title 提示', html.includes('E.FILTER_SCOPE_HINT'));
ok('不可用标签 disabled', html.includes("(unusable ? ' disabled' : '')"));

sec('5. 筛选是本地过滤（不调后端）');
// 筛选相关函数里不能出现后端请求
const filterFns = ['buildFilterBarHtml','toggleFilterTag','clearFilterTags','refreshFilterViews','renderHistoryBody'];
filterFns.forEach(fn => {
  const m = html.match(new RegExp('function\\s+' + fn + '[\\s\\S]*?(?=\\nfunction |\\n// =|\\nasync function )'));
  const body = m ? m[0] : '';
  ok(fn + ' 内无后端请求', !/requestJSON|postJSON|fetch\(/.test(body),
     body.match(/requestJSON|postJSON|fetch\(/));
});
ok('renderHistoryBody 无请求', !/function renderHistoryBody[\s\S]{0,2000}?requestJSON/.test(html));

sec('6. 联动规则');
// loadHistory 里缓存 + 重绘
ok('loadHistory 缓存原始数据', /histRawItems = items;/.test(html));
ok('loadHistory 后重绘筛选栏', /histFilterBar[\s\S]{0,40}outerHTML = buildFilterBarHtml\('history'\)/.test(html));
// 批量检测完成后应用筛选
ok('renderBatchRows 内套用筛选', /const shown = E\.applyFilterTags\(rows, filterTags, 'batch'\)/.test(html));
ok('批量无匹配返回提示', /shown\.length === 0[\s\S]{0,200}没有符合当前筛选条件的记录/.test(html));
// 单条检测后刷新历史（原有行为，loadHistory 被调用）
ok('单条检测后仍调 loadHistory', /type === 'url'[\s\S]{0,400}loadHistory\(1\)/.test(html));

sec('7. ⚠️ 筛选后 AI解读索引不错位（关键）');
// 必须用 rows.indexOf(r) 取原始下标，不能用 shown 的下标
ok('批量表格用 rows.indexOf(r) 取原始下标',
   /const i = rows\.indexOf\(r\)/.test(html), '必须按对象引用回原始数组取下标');
ok('批量重渲染同样用 indexOf',
   /const idx = rows\.indexOf\(r\)/.test(html));
ok('window.__wzhBatchRows 存的是原始数组（非过滤后）',
   /window\.__wzhBatchRows = rows;/.test(html));
// 验证逻辑正确性：构造筛选场景，确认下标映射对
const rows = [
  { url:'https://1.com/', level:'safe',       label:'安全' },
  { url:'https://2.com/', level:'danger',     label:'高危' },
  { url:'https://3.com/', level:'suspicious', label:'可疑' },
  { url:'https://4.com/', level:'safe',       label:'安全' }
];
const shown = E.applyFilterTags(rows, ['danger'], 'batch');
eq('筛出 1 条', shown.length, 1);
eq('若用 shown 下标会错位（所以必须用 indexOf）', shown[0], rows[1]);
eq('indexOf 拿到的原始下标 = 1', rows.indexOf(shown[0]), 1);
eq('原始数组拿到的是第2条（正确）', rows[rows.indexOf(shown[0])].url, 'https://2.com/');
// 多条筛选时同样验证
const shown2 = E.applyFilterTags(rows, ['safe'], 'batch');
eq('筛 safe 得 2 条', shown2.length, 2);
eq('第1条原始下标0', rows.indexOf(shown2[0]), 0);
eq('第2条原始下标3（不是1）', rows.indexOf(shown2[1]), 3);

sec('8. 导出联动');
ok('菜单新增第三项 #expFiltered', html.includes('id="expFiltered"'));
ok('第三项文案「当前筛选结果」', html.includes('当前筛选结果'));
ok('第三项调用 doExport(filtered)', html.includes("doExport('filtered')"));
ok('doExport 支持 filtered 分支', html.includes("if (scope === 'filtered')"));
ok('未启用筛选时友好提示', html.includes('当前未启用筛选条件'));
ok('filtered 复用 buildReportText', /scope === 'filtered'[\s\S]{0,2000}buildReportText/.test(html));
ok('filtered 批量用 source batch', /scope === 'filtered'[\s\S]{0,900}source: 'batch'/.test(html));
ok('filtered 历史用 source history', /scope === 'filtered'[\s\S]{0,2000}source: 'history'/.test(html));
ok('filtered 仍走 downloadTxtFile', /scope === 'filtered'[\s\S]{0,2500}downloadTxtFile/.test(html));
ok('refreshExportMenuCount 支持第三项', html.includes("filteredCnt.textContent"));
ok('导出菜单原有两项仍在', html.includes('id="expBatch"') && html.includes('id="expHistory"'));
ok('导出原有分支未被破坏', html.includes("if (scope === 'batch')") && html.includes("if (scope === 'history')"));
ok('fetchAllHistory 完好', /async function fetchAllHistory\(\)/.test(html));

sec('9. CSS 变量合规');
const cssStart = html.indexOf('功能6：检测结果标签筛选');
const cssEnd = html.indexOf('.fbar .rs.on');
ok('定位到功能6 CSS', cssStart > 0 && cssEnd > cssStart, cssStart+'->'+cssEnd);
const newCss = html.slice(cssStart, cssEnd + 200);
// 注释里举例子用的颜色不是实际样式，检查前先剥掉注释
const newCssNoComment = newCss.replace(/\/\*[\s\S]*?\*\//g, '');
const noShadow = newCssNoComment.replace(/box-shadow:[^;]+;/g,'').replace(/rgba\(0,0,0,\.\d+\)/g,'S');
const hex = noShadow.match(/#[0-9a-fA-F]{3,6}\b/g);
ok('无硬编码十六进制色', !hex, hex?hex.join(','):'');
ok('除阴影外无 rgba', !noShadow.match(/rgba?\(/), (noShadow.match(/rgba?\(/g)||[]).join(','));
const rootStart = html.search(/^\s*:root\{/m);
const lightStart = html.search(/^\s*html\[data-theme="light"\]\{/m);
const rootBlock = html.slice(rootStart, html.indexOf('}', rootStart));
const lightBlock = html.slice(lightStart, html.indexOf('\n}', lightStart));
const used = [...new Set((newCss.match(/var\(--[a-z0-9-]+\)/g)||[]))].map(s=>s.slice(4,-1));
console.log('  功能6 CSS 使用 ' + used.length + ' 个变量');
used.forEach(v => ok('变量 ' + v + ' 两套主题都定义',
   rootBlock.includes(v+':') && lightBlock.includes(v+':'), v));
const nR=(rootBlock.match(/--[a-z0-9-]+\s*:/g)||[]).length;
const nL=(lightBlock.match(/--[a-z0-9-]+\s*:/g)||[]).length;
ok('两套主题变量数一致（'+nR+' vs '+nL+'）', nR===nL, nR+' vs '+nL);
ok('功能6 未定义新变量（只消费）', (newCss.match(/--[a-z0-9-]+\s*:/g)||[]).length===0);

sec('10. 选中态对比度（WCAG）');
// 选中态沿用全站 .lv / .tag 的「浅底 + 深字」套路。
// ⚠️ 不要改回「实心状态色 + 白字」：状态色在深色主题下是亮色（如 #ff7b72），
//    白字压上去只有 2.5:1，达不到 WCAG AA。
//    具体对比度计算见 _t_style.js（两套主题全部 ≥4.5:1）。
ok('选中态不再用 --on-btn 白字压浅色底', !/\.ftag\.on\{[^}]*color:var\(--on-btn\)/.test(newCss));
ok('危险选中底用 --dang-bg（深色底）', newCss.includes('background:var(--dang-bg)'));
ok('可疑选中底用 --warn-bg（深色底）', newCss.includes('background:var(--warn-bg)'));
ok('安全选中底用 --ok-bg（深色底）', newCss.includes('background:var(--ok-bg)'));
ok('危险选中字用 --dang-fg', newCss.includes('color:var(--dang-fg)'));
ok('可疑选中字用 --warn-fg', newCss.includes('color:var(--warn-fg)'));
ok('安全选中字用 --ok-fg', newCss.includes('color:var(--ok-fg)'));
ok('未知选中字用 --text2（--gray 对比度不足）', /\.ftag\.on\.c-unknown\{[^}]*color:var\(--text2\)/.test(newCss));
ok('选中态有加粗区分', /\.ftag\.on\{[^}]*font-weight:600/.test(newCss));
ok('不可用态有 title 说明', html.includes('E.FILTER_SCOPE_HINT'));

sec('11. 布局不挤压已有组件');
// 筛选栏必须在 hist-hd 之后、histBody 之前
const histHd = html.indexOf('class="hist-hd"');
const fbar = html.indexOf('id="histFilterBar"');
const histBody = html.indexOf('id="histBody"');
ok('筛选栏在历史面板内（hist-hd 之后）', fbar > histHd, histHd+' -> '+fbar);
ok('筛选栏在表格上方（histBody 之前）', fbar < histBody, fbar+' -> '+histBody);
// 批量筛选栏由 renderBatchRows 生成，不在 sendbar 里
ok('筛选栏未塞进 sendbar', !html.slice(html.indexOf('class="sendbar"'), html.indexOf('class="sendbar"')+600).includes('fbar'));
// 主题按钮仍在 header，不受影响
ok('主题按钮仍在 header（未被筛选栏挤掉）', html.indexOf('id="themeBtn"') < histHd);
ok('导出按钮仍在历史标题栏', html.slice(histHd, fbar).includes('id="expBtn"'));
ok('批量按钮仍在 sendbar', html.includes('id="batchBtn"'));
ok('输入框仍在', html.includes('id="ta"'));
ok('.fbar 支持换行', /\.fbar\{[^}]*flex-wrap:wrap/.test(newCss));
ok('.fbar 有 gap', /\.fbar\{[^}]*gap:7px/.test(newCss));

sec('12. 保护红线（功能1~5 完好）');
// 功能1
ok('loadHistory 签名未变', /async function loadHistory\(page = 1\)/.test(html));
ok('loadHistory 接口调用未变', html.includes("E.requestJSON('/history/list?page=' + page + '&size=' + HIST_PAGE_SIZE)"));
ok('loadHistory 更新 histTotal', html.includes('histTotal = d.total || 0;'));
ok('loadHistory 更新 histCnt', html.includes("$('histCnt').textContent"));
ok('分页逻辑未变', html.includes('$(\'histPager\').style.display = \'flex\';'));
ok('deleteHistory 完好', /async function deleteHistory\(id\)/.test(html));
ok('clearHistory 完好', /async function clearHistory\(\)/.test(html));
ok('历史表头仍 5 列（操作列加宽以容纳收藏/复制/二维码按钮）', html.includes('<th style="width:290px">操作</th>'));
ok('历史行仍有重新查询+删除',
   html.includes("reuseHistory('${escapeAttr(it.originalUrl || '')}')") && html.includes('deleteHistory(${it.id})'));
ok('历史行未混入筛选按钮', !/reuseHistory[\s\S]{0,200}ftag/.test(html));
// 功能2
ok('getTheme/setTheme/toggleTheme 未破坏',
   /function getTheme\(\)/.test(engine) && /function setTheme\(theme\)/.test(engine) && /function toggleTheme\(\)/.test(engine));
ok('页面 currentTheme/syncThemeBtn 未破坏',
   /function currentTheme\(\)/.test(html) && /function syncThemeBtn\(\)/.test(html));
ok('THEME_KEY 未变', E.THEME_KEY==='wzh_theme');
// 功能3
ok('batchDetect 完好', /async function batchDetect\(\)/.test(html));
ok('引擎 batchDetect 完好', /async function batchDetect\(urls, opts = \{\}\)/.test(engine));
ok('lastBatchRows 仍赋值', html.includes('lastBatchRows = res.rows;'));
ok('批量表格仍 3 列（第 3 列「操作」容纳收藏/复制/二维码/AI解读）', html.includes('<th style="width:230px">操作</th>'));
ok('批量网址仍可点击回填', html.includes("onclick=\"reuseBatchUrl('${escapeAttr(r.url)}')\""));
// 功能4
ok('功能4 导出函数齐全', ['buildReportFileName','formatDateTime','normalizeExportRows','buildReportText','downloadTxtFile']
   .every(k=>typeof E[k]==='function'));
ok('showExpTip/finishExport 完好', /function showExpTip\(/.test(html) && /function finishExport\(/.test(html));
// 功能5
ok('功能5 引擎函数齐全', ['fetchInterpret','buildLocalInterpret','toInterpretSections']
   .every(k=>typeof E[k]==='function'));
ok('AI解读弹窗仍在', html.includes('id="aiModal"'));
ok('AI解读按钮仍在批量表', html.includes('class="aibtn"'));
ok('AI解读按钮仍在单条报告', html.includes('🤖 AI解读详情'));
ok('requestAiInterpret 完好', /async function requestAiInterpret\(/.test(html));

sec('13. 后端零改动');
ok('未新增后端接口调用', !html.includes("postJSON('/filter") && !html.includes("'/filter"));
ok('仅用已有 history/list', html.includes("'/history/list?page='"));
ok('API_BASE 自适应回落', E.API_BASE==='/api/v1');
ok('章节号顺延（十二、二维码 / 十三、导出）', engine.includes('// 十二、快捷复制与二维码预览') && engine.includes('// 十三、导出'));

sec('14. 引擎导出完整性');
['FILTER_TAGS','FILTER_SCOPE_HINT','matchFilterTags','applyFilterTags',
 'countByFilterTags','availableTagsFor','filterTagKeyByLabel'].forEach(k =>
  ok('引擎导出 ' + k, E[k]!==undefined, typeof E[k]));

sec('15. 文件规模');
ok('index.html <125KB（功能9收藏+功能10模板+功能13网址跳转+工具简介使体积增长）', html.length<125000, html.length+' 字节');
ok('engine.js <68KB（功能7 二维码编码器 + 功能11 输入分流规则使体积略增）', engine.length<68000, engine.length+' 字节');

console.log('\n' + '='.repeat(50));
console.log('  通过 ' + pass + ' 项，失败 ' + fail + ' 项');
console.log('='.repeat(50));
process.exit(fail?1:0);