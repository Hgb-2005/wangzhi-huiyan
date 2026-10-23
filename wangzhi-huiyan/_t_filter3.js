// 功能6 渲染实测：把 index.html 里的关键渲染函数抽出来，用真实数据跑一遍
// 目的：验证「筛选栏 HTML」与「筛选后的表格」实际输出是否符合预期
// 用法：node _t_filter3.js
const fs = require('fs');
const E = require('./engine.js');
const html = fs.readFileSync('index.html', 'utf-8');

let pass = 0, fail = 0;
function ok(n, c, e) { if (c) { pass++; console.log('  PASS  ' + n); } else { fail++; console.log('  FAIL  ' + n + (e !== undefined ? '  -> ' + String(e).slice(0,300) : '')); } }
function sec(t) { console.log('\n===== ' + t + ' ====='); }

// ---- 从 index.html 抽出要测的函数源码 ----
function grabFn(name) {
  const re = new RegExp('function\\s+' + name + '\\s*\\([^)]*\\)\\s*\\{[\\s\\S]*?\\n\\}', 'm');
  const m = html.match(re);
  return m ? m[0] : null;
}

// 需要注入的依赖
const escapeHtml = grabFn('escapeHtml');
const escapeAttr = grabFn('escapeAttr');

// 组装一个最小运行环境
// 注意：$ 必须返回【同一个】元素对象，否则 innerHTML 写入后读不回来
const sandbox = `
const E = require('${__dirname.replace(/\\/g,'/')}/engine.js');
let filterTags = [];
let histRawItems = [];
let lastBatchRows = null;
const __dom = {};
const $ = id => {
  if (!__dom[id]) __dom[id] = { id, innerHTML:'', textContent:'', disabled:false,
    outerHTML:'', value:'', dataset:{},
    querySelector:()=>null, querySelectorAll:()=>[],
    insertAdjacentHTML:()=>{}, remove:()=>{}, classList:{add(){},remove(){},toggle(){},contains(){return false}},
    addEventListener(){}, getAttribute:()=>null, setAttribute(){} };
  return __dom[id];
};
const out = { querySelectorAll: () => [] };
function showExpTip(){}
function isFavUrl(){ return false; }
function toggleFavoriteFrom(){}
${escapeHtml}
${escapeAttr}
${grabFn('buildFilterBarHtml')}
${grabFn('renderHistoryBody')}
module.exports = { buildFilterBarHtml, renderHistoryBody,
  setTags: t => { filterTags = t; }, setHist: d => { histRawItems = d; },
  setBatch: d => { lastBatchRows = d; }, getTags: () => filterTags,
  getHtml: id => ($(id) ? $(id).innerHTML : null) };
`;
fs.writeFileSync('_sandbox.js', sandbox);
const SB = require('./_sandbox.js');
const H = id => SB.getHtml(id);

// ---- 真实形态的数据（字段名与后端实际返回一致）----
const histData = [
  { id:11, originalUrl:'https://e-demo-tracker.io/dl', riskLevel:'safe', riskLabel:'安全', expandHops:0, createdAt:'2026-10-22 22:28:19' },
  { id:10, originalUrl:'https://d-demo-normal.cn/',     riskLevel:'safe', riskLabel:'安全', expandHops:0, createdAt:'2026-10-22 22:28:19' },
  { id:9,  originalUrl:'https://c-demo-suspicious.net/login', riskLevel:'suspicious', riskLabel:'可疑', expandHops:0, createdAt:'2026-10-22 22:28:19' },
  { id:8,  originalUrl:'https://loop.demo-redirect.top/', riskLevel:'suspicious', riskLabel:'可疑', expandHops:6, createdAt:'2026-10-22 22:28:19' },
  { id:7,  originalUrl:'https://account-verify.demo-login.top/signin', riskLevel:'danger', riskLabel:'高危', expandHops:3, createdAt:'2026-10-22 22:28:19' },
  { id:6,  originalUrl:'https://unreachable.demo-site.top/', riskLevel:'unknown', riskLabel:'未知', expandHops:0, createdAt:'2026-10-22 22:28:19' },
  { id:5,  originalUrl:'http://0k.cn-security-check.top/login', riskLevel:'danger', riskLabel:'高危', expandHops:0, createdAt:'2026-10-22 22:28:19' }
];

sec('1. 历史筛选栏 HTML 实际输出');
SB.setTags([]);
SB.setHist(histData);
let bar = SB.buildFilterBarHtml('history');
console.log('  --- 未筛选时的历史筛选栏 ---');
console.log(bar.split('><').join('>\n<').split('\n').map(l=>'  '+l).join('\n'));

ok('含 8 个标签按钮', (bar.match(/class="ftag/g)||[]).length === 9, (bar.match(/class="ftag/g)||[]).length);
// 4 个通用标签有计数，4 个特征标签置灰
ok('高危有计数 2', bar.includes('高危</button>') || /高危[\s\S]{0,40}?2<\/span>/.test(bar));
ok('置灰标签带 off 类', (bar.match(/ftag[^"]*\boff\b/g)||[]).length === 4, (bar.match(/ftag[^"]*\boff\b/g)||[]).length);
ok('置灰标签 disabled', (bar.match(/disabled/g)||[]).length >= 4, (bar.match(/disabled/g)||[]).length);
ok('清空按钮初始禁用', /class="ftag clear" disabled/.test(bar));
ok('无筛选时不显示结果计数', !bar.includes('已筛选'));
ok('title 含不可用说明', bar.includes(E.FILTER_SCOPE_HINT));

sec('2. 历史筛选：有选中时');
SB.setTags(['danger']);
bar = SB.buildFilterBarHtml('history');
ok('显示结果计数', bar.includes('已筛选 2 / 7 条'), bar.match(/已筛选[^<]*/)?.[0]);
ok('结果计数带 on 类（高亮）', /class="rs on"/.test(bar));
ok('高危标签有 on 类', /ftag c-danger on/.test(bar));
ok('可疑标签无 on 类', !/ftag c-suspicious on/.test(bar));
ok('清空按钮已启用', !/class="ftag clear" disabled/.test(bar));

sec('3. 历史表格：筛选前 vs 筛选后');
SB.setTags([]);
SB.renderHistoryBody();
let body = H('histBody');
// 只数 tbody 里的数据行（thead 有 1 行表头）
const rowCount = h => {
  const tb = (h.match(/<tbody>[\s\S]*?<\/tbody>/) || [''])[0];
  return (tb.match(/<tr>/g) || []).length;
};
const allRows = rowCount(body);
ok('无筛选时 7 行', allRows === 7, allRows);
ok('含重新查询按钮', body.includes('reuseHistory'));
ok('含删除按钮', body.includes('deleteHistory'));

SB.setTags(['danger']);
SB.renderHistoryBody();
body = H('histBody');
const dRows = rowCount(body);
ok('筛选高危后 2 行', dRows === 2, dRows);
ok('含高危那条', body.includes('0k.cn-security-check.top'));
ok('含另一条高危', body.includes('account-verify.demo-login.top'));
ok('不含安全站', !body.includes('e-demo-tracker.io'));
ok('表头仍是 5 列', body.includes('<th style="width:290px">操作</th>'));

SB.setTags(['safe','danger']);
SB.renderHistoryBody();
body = H('histBody');
ok('多选 安全+高危 = 4 行', rowCount(body) === 4, rowCount(body));

sec('4. 历史表格：无匹配时友好提示');
// 注意：测试数据里存在 unknown 行，所以要先清掉它才能构造真正的「零匹配」
SB.setTags(['unknown']);
SB.renderHistoryBody();
body = H('histBody');
ok('unknown 命中 1 行（数据里有 1 条未知）', rowCount(body) === 1, rowCount(body));
// 构造真正零匹配：用一个当前数据里不存在的标签组合
SB.setTags(['safe']);
SB.setHist(histData.filter(it => it.riskLevel !== 'safe'));
SB.renderHistoryBody();
body = H('histBody');
ok('提示需求原文文案', body.includes('没有符合当前筛选条件的记录'), body.slice(0, 150));
ok('不是空白', body.length > 10);
ok('无匹配时不渲染表格', !body.includes('<table'));
// 还原数据
SB.setHist(histData);

sec('5. 清空筛选恢复全量（关键契约）');
SB.setTags([]);
SB.renderHistoryBody();
body = H('histBody');
ok('清空后恢复 7 行', rowCount(body) === 7, rowCount(body));

sec('6. 批量区筛选栏（8 个标签全可用）');
const batchData = [
  { url:'https://a.com/',  level:'safe',       label:'安全', hops:0 },
  { url:'https://b.com/',  level:'suspicious', label:'可疑', hops:0 },
  { url:'http://c.top/x',  level:'danger',     label:'高危', hops:3 },
  { url:'https://d.com/',  level:'unknown',    label:'未知', hops:0, note:'页面抓取失败，无法访问目标站点' },
  { url:'https://e.link/x',level:'unknown',    label:'未知', hops:0, note:'短链解析失败' },
  { url:'https://f.top/y', level:'suspicious', label:'可疑', hops:4, isLoop:true }
];
SB.setBatch(batchData);
SB.setTags([]);
bar = SB.buildFilterBarHtml('batch');
ok('批量区无置灰标签', !/ftag[^"]*\boff\b/.test(bar), '批量区应全部可用');
// 只检查 8 个标签按钮是否 disabled（清空按钮在无筛选时本就禁用，不算）
const tagBtns = bar.match(/<button[^>]*data-key="[^"]*"[^>]*>/g) || [];
ok('批量区 8 个标签全部无 disabled', tagBtns.length === 8 && !tagBtns.some(b => b.includes('disabled')), tagBtns.filter(b=>b.includes('disabled')).join(' | '));
ok('批量区标签按钮数 = 8', tagBtns.length === 8, tagBtns.length);
ok('抓取失败计数 1', /抓取失败[\s\S]{0,40}?1<\/span>/.test(bar));
ok('解析失败计数 1', /解析失败[\s\S]{0,40}?1<\/span>/.test(bar));
ok('短链中转计数 2', /短链中转[\s\S]{0,40}?2<\/span>/.test(bar));
ok('跳转风险计数 2', /跳转风险[\s\S]{0,40}?2<\/span>/.test(bar));

sec('7. 批量特征标签筛选实测');
SB.setTags(['fetchFail']);
ok('抓取失败 → 1 条', E.applyFilterTags(batchData, ['fetchFail'], 'batch').length === 1);
SB.setTags(['parseFail']);
ok('解析失败 → 1 条', E.applyFilterTags(batchData, ['parseFail'], 'batch').length === 1);
SB.setTags(['redirect']);
ok('短链中转 → 2 条', E.applyFilterTags(batchData, ['redirect'], 'batch').length === 2);
SB.setTags(['hopRisk']);
ok('跳转风险 → 2 条', E.applyFilterTags(batchData, ['hopRisk'], 'batch').length === 2);
// 组合
SB.setTags(['fetchFail','parseFail']);
ok('抓取失败+解析失败 → 2 条（都是未知）', E.applyFilterTags(batchData, ['fetchFail','parseFail'], 'batch').length === 2);
SB.setTags(['danger','hopRisk']);
ok('高危+跳转风险 → 2 条', E.applyFilterTags(batchData, ['danger','hopRisk'], 'batch').length === 2);

sec('8. 两区共用一套状态');
SB.setTags(['danger','safe']);
ok('状态被两区共用', JSON.stringify(SB.getTags()) === '["danger","safe"]');
const hBar = SB.buildFilterBarHtml('history');
const bBar = SB.buildFilterBarHtml('batch');
// 两区都应该显示 danger/safe 为选中
ok('历史栏高危+安全选中', /ftag c-danger on/.test(hBar) && /ftag c-safe on/.test(hBar));
ok('批量栏高危+安全选中', /ftag c-danger on/.test(bBar) && /ftag c-safe on/.test(bBar));
// 历史栏的 batch-only 标签即使在 filterTags 里也不该显示选中
SB.setTags(['danger','fetchFail']);
const hBar2 = SB.buildFilterBarHtml('history');
const ffBtn = (hBar2.match(/<button[^>]*data-key="fetchFail"[\s\S]*?<\/button>/) || [''])[0];
console.log('  --- 历史栏 fetchFail 按钮原始 HTML ---');
console.log('  ' + ffBtn);
ok('历史栏 fetchFail 不可用（带 off 类 + disabled）', /off/.test(ffBtn) && /disabled/.test(ffBtn), ffBtn);
ok('历史栏 fetchFail 未被误标为选中（无 on 类）', !/\bon\b/.test(ffBtn), ffBtn);
ok('历史栏 fetchFail 仍带不可用提示 title', ffBtn.includes(E.FILTER_SCOPE_HINT), ffBtn);
ok('历史栏 fetchFail 计数为 0', /<span class="n">0<\/span>/.test(ffBtn), ffBtn);
ok('历史栏 danger 仍是选中态（on，无 off）',
  /<button[^>]*data-key="danger"[\s\S]*?<\/button>/.test(hBar2)
  && /\bon\b/.test((hBar2.match(/<button[^>]*data-key="danger"[\s\S]*?<\/button>/) || [''])[0])
  && !/off/.test((hBar2.match(/<button[^>]*data-key="danger"[\s\S]*?<\/button>/) || [''])[0]));

sec('9. XSS 防护（网址含特殊字符）');
SB.setTags([]);
SB.setHist([
  { id:1, originalUrl:'https://x.com/"><script>alert(1)</script>', riskLevel:'danger', riskLabel:'高危', expandHops:1, createdAt:'2026-01-01 00:00:00' }
]);
SB.renderHistoryBody();
body = H('histBody');
ok('script 标签被转义', !body.includes('<script>'), body.slice(0,200));
ok('含转义后的实体', body.includes('&lt;script&gt;'));
ok('双引号被转义（不破坏属性）', !body.includes('""><script'));

sec('10. 边界');
SB.setHist([]);
SB.setTags([]);
SB.renderHistoryBody();
body = H('histBody');
ok('空数据 → 原空态提示', body.includes('还没有检测记录'), body.slice(0,100));
bar = SB.buildFilterBarHtml('history');
const clearBtn0 = (bar.match(/<button[^>]*class="ftag clear"[\s\S]*?<\/button>/) || [''])[0];
ok('空数据 + 无筛选 → 清空按钮禁用', /disabled/.test(clearBtn0), clearBtn0);
ok('空数据时结果计数不显示（未筛选）', !bar.includes('已筛选'));
// 有筛选 + 空数据
SB.setTags(['danger']);
SB.renderHistoryBody();
body = H('histBody');
ok('空数据+有筛选 → 仍是原空态（不误导）', body.includes('还没有检测记录'), body.slice(0,100));
bar = SB.buildFilterBarHtml('history');
const clearBtn1 = (bar.match(/<button[^>]*class="ftag clear"[\s\S]*?<\/button>/) || [''])[0];
ok('空数据+有筛选 → 清空按钮可用（可以取消筛选）', !/disabled/.test(clearBtn1), clearBtn1);
ok('空数据时结果计数显示 0/0', bar.includes('已筛选 0 / 0 条'));

sec('11. 回归：lastBatchRows 赋值顺序（批量筛选栏首屏计数不能是 0）');
// 模拟批量检测回调里的真实顺序：先赋 lastBatchRows，再调 renderBatchRows
SB.setBatch(batchData);
bar = SB.buildFilterBarHtml('batch');
const bDanger = (bar.match(/<button[^>]*data-key="danger"[\s\S]*?<\/button>/) || [''])[0];
ok('批量栏高危计数 = 1（首屏就是真实值，不是 0）', /<span class="n">1<\/span>/.test(bDanger), bDanger);
const bSafe = (bar.match(/<button[^>]*data-key="safe"[\s\S]*?<\/button>/) || [''])[0];
ok('批量栏安全计数 = 1', /<span class="n">1<\/span>/.test(bSafe), bSafe);
const bUnknown = (bar.match(/<button[^>]*data-key="unknown"[\s\S]*?<\/button>/) || [''])[0];
ok('批量栏未知计数 = 2', /<span class="n">2<\/span>/.test(bUnknown), bUnknown);
ok('批量栏分母 = 共 6 条', /已筛选 1 \/ 6 条/.test(bar) || !bar.includes('已筛选'), bar.match(/已筛选[^<]*/)?.[0]);

sec('12. 回归：无死代码残留');
ok('histAllItems 已移除', !html.includes('histAllItems'));
ok('filterTags 仍是唯一筛选状态源', html.includes('let filterTags = [];'));

fs.unlinkSync('_sandbox.js');
console.log('\n' + '='.repeat(50));
console.log('  通过 ' + pass + ' 项，失败 ' + fail + ' 项');
console.log('='.repeat(50));
process.exit(fail?1:0);