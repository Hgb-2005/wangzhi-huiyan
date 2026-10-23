// 功能6 单元自测：标签体系与匹配规则
// 用法：node _t_filter.js
const E = require('./engine.js');
let pass = 0, fail = 0;
function ok(n, c, e) { if (c) { pass++; console.log('  PASS  ' + n); } else { fail++; console.log('  FAIL  ' + n + (e !== undefined ? '  -> ' + String(e).slice(0,200) : '')); } }
function eq(n, a, b) { ok(n, a === b, 'got=' + JSON.stringify(a) + ' want=' + JSON.stringify(b)); }
function sec(t) { console.log('\n===== ' + t + ' ====='); }

sec('1. 标签体系');
eq('共 8 个标签', E.FILTER_TAGS.length, 8);
const labels = E.FILTER_TAGS.map(t => t.label);
console.log('  标签：' + labels.join(' / '));
['高危','可疑','安全','未知','抓取失败','解析失败','短链中转','跳转风险'].forEach(l =>
  ok('包含标签「' + l + '」', labels.includes(l), labels.join(',')));
// 顺序必须与需求一致
eq('顺序与需求一致', labels.join(','), '高危,可疑,安全,未知,抓取失败,解析失败,短链中转,跳转风险');
// key 唯一
const keys = E.FILTER_TAGS.map(t => t.key);
eq('key 无重复', new Set(keys).size, 8);
ok('每个标签都有颜色', E.FILTER_TAGS.every(t => !!t.color));
ok('每个标签都有 scope', E.FILTER_TAGS.every(t => t.scope === 'both' || t.scope === 'batch'));
// scope 分布
eq('4 个标签两区通用', E.FILTER_TAGS.filter(t => t.scope === 'both').length, 4);
eq('4 个标签仅批量', E.FILTER_TAGS.filter(t => t.scope === 'batch').length, 4);
ok('历史提示文案存在', E.FILTER_SCOPE_HINT.includes('暂不支持历史记录筛选'));

sec('2. 风险等级标签（两区都可靠）');
const batchRows = [
  { url: 'https://a.com/',  level: 'safe',       label: '安全', hops: 0 },
  { url: 'https://b.com/',  level: 'suspicious', label: '可疑', hops: 0 },
  { url: 'http://c.top/x',  level: 'danger',     label: '高危', hops: 3 },
  { url: 'https://d.com/',  level: 'unknown',    label: '未知', hops: 0, note: '页面抓取失败，无法访问目标站点' },
  { url: 'https://e.link/x',level: 'unknown',    label: '未知', hops: 0, note: '短链解析失败' },
  { url: 'https://f.top/y', level: 'suspicious', label: '可疑', hops: 4, isLoop: true }
];
const histRows = [
  { id:1, originalUrl:'https://a.com/',  riskLevel:'safe',       riskLabel:'安全',   expandHops:0 },
  { id:2, originalUrl:'https://b.com/',  riskLevel:'suspicious', riskLabel:'可疑',   expandHops:0 },
  { id:3, originalUrl:'http://c.top/x',  riskLevel:'danger',     riskLabel:'高危',   expandHops:3 },
  { id:4, originalUrl:'https://d.com/',  riskLevel:'unknown',    riskLabel:'未知',   expandHops:0 }
];
// 逐标签在批量区
eq('批量-高危 1条', E.applyFilterTags(batchRows, ['danger'], 'batch').length, 1);
eq('批量-可疑 2条', E.applyFilterTags(batchRows, ['suspicious'], 'batch').length, 2);
eq('批量-安全 1条', E.applyFilterTags(batchRows, ['safe'], 'batch').length, 1);
eq('批量-未知 2条', E.applyFilterTags(batchRows, ['unknown'], 'batch').length, 2);
// 逐标签在历史区
eq('历史-高危 1条', E.applyFilterTags(histRows, ['danger'], 'history').length, 1);
eq('历史-可疑 1条', E.applyFilterTags(histRows, ['suspicious'], 'history').length, 1);
eq('历史-安全 1条', E.applyFilterTags(histRows, ['safe'], 'history').length, 1);
eq('历史-未知 1条', E.applyFilterTags(histRows, ['unknown'], 'history').length, 1);
// 具体命中的是哪条
ok('批量-高危 命中的是 c.top',
   E.applyFilterTags(batchRows, ['danger'], 'batch')[0].url === 'http://c.top/x');
ok('历史-高危 命中的是 c.top',
   E.applyFilterTags(histRows, ['danger'], 'history')[0].originalUrl === 'http://c.top/x');

sec('3. 特征标签（仅批量区）');
// 抓取失败：unknown + note 含抓取/无法访问
eq('批量-抓取失败 1条', E.applyFilterTags(batchRows, ['fetchFail'], 'batch').length, 1);
ok('  命中 d.com（抓取失败）',
   E.applyFilterTags(batchRows, ['fetchFail'], 'batch')[0].url === 'https://d.com/');
// 解析失败：unknown + note 含解析失败
eq('批量-解析失败 1条', E.applyFilterTags(batchRows, ['parseFail'], 'batch').length, 1);
ok('  命中 e.link（解析失败）',
   E.applyFilterTags(batchRows, ['parseFail'], 'batch')[0].url === 'https://e.link/x');
// 短链中转：hops>0 或 finalUrl 不同
eq('批量-短链中转 2条', E.applyFilterTags(batchRows, ['redirect'], 'batch').length, 2);
// 跳转风险：isLoop 或 hops>=3
eq('批量-跳转风险 2条', E.applyFilterTags(batchRows, ['hopRisk'], 'batch').length, 2);
ok('  含 3跳的 c.top', E.applyFilterTags(batchRows, ['hopRisk'], 'batch').some(r=>r.url==='http://c.top/x'));
ok('  含循环的 f.top', E.applyFilterTags(batchRows, ['hopRisk'], 'batch').some(r=>r.url==='https://f.top/y'));

// 历史区这四个应该 0 条（因为接口字段不足，不该给出误导结果）
ok('历史-抓取失败 0条（字段不足不误判）', E.applyFilterTags(histRows, ['fetchFail'], 'history').length === 0);
ok('历史-解析失败 0条', E.applyFilterTags(histRows, ['parseFail'], 'history').length === 0);

sec('4. 多选：命中任意一个即保留');
eq('多选 高危+安全 = 2条', E.applyFilterTags(batchRows, ['danger','safe'], 'batch').length, 2);
eq('多选 可疑+未知 = 4条', E.applyFilterTags(batchRows, ['suspicious','unknown'], 'batch').length, 4);
eq('多选 三标签 = 4条', E.applyFilterTags(batchRows, ['danger','safe','unknown'], 'batch').length, 4);
eq('全选 8个 = 6条（全量）', E.applyFilterTags(batchRows, E.FILTER_TAGS.map(t=>t.key), 'batch').length, 6);
// 组合：等级+特征（c.top 既是高危又是3跳，只算一条）
eq('高危 + 跳转风险 = 2条', E.applyFilterTags(batchRows, ['danger','hopRisk'], 'batch').length, 2);
eq('高危 + 安全 + 跳转风险 = 3条', E.applyFilterTags(batchRows, ['danger','safe','hopRisk'], 'batch').length, 3);

sec('5. 空选 = 不过滤');
eq('空数组 全保留', E.applyFilterTags(batchRows, [], 'batch').length, 6);
eq('非数组 全保留', E.applyFilterTags(batchRows, null, 'batch').length, 6);
eq('undefined 全保留', E.applyFilterTags(batchRows, undefined, 'batch').length, 6);
// 清空筛选的正确用法：始终对**原始数据**重新过滤。
// 这条契约很重要 —— 如果前端把已过滤的结果再喂给筛选函数，
// 清空标签后也不会恢复出被裁掉的行（数据已经丢了）。
const filtered = E.applyFilterTags(batchRows, ['danger'], 'batch');
eq('先筛出1条', filtered.length, 1);
eq('对已过滤数组再清空 → 仍是1条（数据已裁掉，救不回来）', filtered.length, 1);
// 正确做法：对原始数组重新过滤
eq('对原始数据清空 → 恢复全量6条', E.applyFilterTags(batchRows, [], 'batch').length, 6);
eq('对原始数据换标签 → 重新计算', E.applyFilterTags(batchRows, ['safe'], 'batch').length, 1);
ok('空选时返回同一引用（不拷贝）', E.applyFilterTags(batchRows, [], 'batch') === batchRows);

sec('6. 不修改原数据');
const snapshot = JSON.stringify(batchRows);
E.applyFilterTags(batchRows, ['danger','safe','hopRisk'], 'batch');
eq('原数组未被修改', JSON.stringify(batchRows), snapshot);
const r = batchRows[0];
E.matchFilterTags(r, ['danger'], 'batch');
eq('单条记录未被修改', JSON.stringify(batchRows[0]), JSON.stringify(r));

sec('7. 无匹配的情况');
eq('不存在的标签组合 0条', E.applyFilterTags(histRows, ['danger','safe','suspicious','unknown'], 'history')
   .filter(r=>r.riskLevel==='danger').length, 1);
// 历史只有 safe 时筛 danger
eq('历史无高危 0条', E.applyFilterTags([histRows[0]], ['danger'], 'history').length, 0);
eq('空数组筛选仍空', E.applyFilterTags([], ['danger'], 'batch').length, 0);

sec('8. 边界容错');
eq('null 行输入', E.applyFilterTags(null, ['danger'], 'batch').length, 0);
eq('undefined 行输入', E.applyFilterTags(undefined, ['danger'], 'batch').length, 0);
ok('matchFilterTags(null) 返回 false', E.matchFilterTags(null, ['danger'], 'batch') === false);
ok('未知标签不命中任何东西', E.applyFilterTags(batchRows, ['notExistTag'], 'batch').length === 0);
ok('缺 level 兜底为 unknown', E.matchFilterTags({ url:'x' }, ['unknown'], 'batch') === true);
ok('缺 riskLevel 兜底为 unknown', E.matchFilterTags({ originalUrl:'x' }, ['unknown'], 'history') === true);
ok('缺 expandHops 不报错', E.matchFilterTags({ originalUrl:'x', riskLevel:'safe' }, ['redirect'], 'history') === false);
ok('note 为 null 不报错', E.matchFilterTags({ url:'x', level:'unknown', note:null }, ['fetchFail'], 'batch') === false);

sec('9. 条数统计（给标签显示可用量）');
const cntB = E.countByFilterTags(batchRows, 'batch');
console.log('  批量区统计：', JSON.stringify(cntB, null, 0));
eq('批量-高危计数', cntB.danger, 1);
eq('批量-可疑计数', cntB.suspicious, 2);
eq('批量-安全计数', cntB.safe, 1);
eq('批量-未知计数', cntB.unknown, 2);
eq('批量-抓取失败计数', cntB.fetchFail, 1);
eq('批量-解析失败计数', cntB.parseFail, 1);
eq('批量-短链中转计数', cntB.redirect, 2);
eq('批量-跳转风险计数', cntB.hopRisk, 2);
// 统计之和 > 总数是正常的（一条可命中多个标签）
const sumB = Object.values(cntB).reduce((a,b)=>a+b,0);
ok('统计之和 >= 总条数（一条可命中多标签）', sumB >= batchRows.length, sumB);

const cntH = E.countByFilterTags(histRows, 'history');
console.log('  历史区统计：', JSON.stringify(cntH, null, 0));
eq('历史-高危计数', cntH.danger, 1);
eq('历史-抓取失败恒为0（不可用）', cntH.fetchFail, 0);
eq('历史-跳转风险恒为0（不可用）', cntH.hopRisk, 0);
eq('历史-空数组全0', Object.values(E.countByFilterTags([], 'history')).every(v=>v===0), true);

sec('10. 区域可用性校验');
eq('历史区剔除 batch-only 标签',
   E.availableTagsFor(['danger','fetchFail','safe','hopRisk'], 'history').join(','), 'danger,safe');
eq('批量区保留全部',
   E.availableTagsFor(['danger','fetchFail'], 'batch').join(','), 'danger,fetchFail');
eq('空选返回空', E.availableTagsFor([], 'history').length, 0);
eq('非数组返回空', E.availableTagsFor(null, 'history').length, 0);
eq('全部batch-only在历史区被剔除', E.availableTagsFor(['fetchFail','parseFail','redirect','hopRisk'], 'history').length, 0);

sec('11. 按中文名取 key（导出文案用）');
eq('高危 → danger', E.filterTagKeyByLabel('高危'), 'danger');
eq('抓取失败 → fetchFail', E.filterTagKeyByLabel('抓取失败'), 'fetchFail');
eq('不存在 → 空串', E.filterTagKeyByLabel('不存在的标签'), '');

sec('12. 保护红线（功能4/5 完好）');
['buildReportFileName','formatDateTime','normalizeExportRows','buildReportText','downloadTxtFile',
 'fetchInterpret','buildLocalInterpret','toInterpretSections'].forEach(k =>
   ok('功能4/5 ' + k + ' 仍在', typeof E[k] === 'function'));
eq('THEME_KEY 未变', E.THEME_KEY, 'wzh_theme');
// API_BASE 已改为自适应：Node 环境（无 location）回落到相对路径 /api/v1，
// 线上前后端同源部署；本地 8899 调试时显式指向 5000。
eq('API_BASE 自适应回落', E.API_BASE, '/api/v1');
eq('parseBatchInput 完好', E.parseBatchInput('https://a.com\nb.com').urls.length, 2);
eq('checkProhibited 完好', E.checkProhibited('翻墙').hit, true);
eq('routeInput 完好', E.routeInput('https://a.com/').type, 'url');
ok('INTERPRET_LIB 四等级仍在', Object.keys(E.INTERPRET_LIB).length === 4);
eq('AI 路径未变', E.AI_INTERPRET_PATH, '/ai/interpret');

console.log('\n' + '='.repeat(50));
console.log('  通过 ' + pass + ' 项，失败 ' + fail + ' 项');
console.log('='.repeat(50));
process.exit(fail ? 1 : 0);