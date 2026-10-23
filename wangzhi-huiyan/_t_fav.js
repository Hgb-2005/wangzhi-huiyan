// 功能9：本地收藏 —— 自测
// 覆盖：星标按钮、收藏 CRUD、去重、收藏面板、筛选联动、导出、存储结构
// 用法：node _t_fav.js
const fs = require('fs');
const E = require('./engine.js');
const html = fs.readFileSync('index.html', 'utf-8');

let pass = 0, fail = 0;
function ok(n, c, e) { if (c) { pass++; console.log('  PASS  ' + n); } else { fail++; console.log('  FAIL  ' + n + (e !== undefined ? '  -> ' + String(e).slice(0,300) : '')); } }
function sec(t) { console.log('\n===== ' + t + ' ====='); }

sec('1. 引擎纯函数（收藏逻辑）');
const favs = [{ originalUrl: 'https://a.com/', riskLabel: '高危', finalUrl: 'https://a.com/x', createdAt: '2026-01-01 00:00:00' }];
ok('favoriteView 补 riskLevel', E.favoriteView(favs[0]).riskLevel === 'danger');
ok('favoriteView 补 expandHops=0', E.favoriteView(favs[0]).expandHops === 0);
ok('未知标签 → unknown', E.favoriteView({ riskLabel: '奇怪' }).riskLevel === 'unknown');
ok('isFavorited 命中', E.isFavorited(favs, 'https://a.com/') === true);
ok('isFavorited 未命中', E.isFavorited(favs, 'https://b.com/') === false);
ok('isFavorited 空 url', E.isFavorited(favs, '') === false);
ok('removeFavorite 返回新数组', E.removeFavorite(favs, 'https://a.com/').length === 0);
ok('removeFavorite 不修改原数组', E.removeFavorite(favs, 'https://a.com/').length === 0 && favs.length === 1);
// favoriteView 输出可被 applyFilterTags('history') 消费
const view = E.favoriteView(favs[0]);
ok('收藏视图可参与标签筛选', E.applyFilterTags([view], ['danger'], 'history').length === 1);

sec('2. 星标按钮（三处入口）');
ok('批量 renderBatchRows 有星标', (html.match(/toggleFavoriteFrom\(this\)/g) || []).length >= 3, (html.match(/toggleFavoriteFrom\(this\)/g) || []).length);
ok('历史行有星标（含 data-final/data-created）', html.includes('data-final="${escapeHtml(it.finalUrl || \'\')}"'));
ok('星标空心☆/实心★由 isFavUrl 决定', /isFavUrl\([^)]*\) \? '★' : '☆'/.test(html));
ok('fav-btn 类名存在', html.includes('class="fav-btn') || html.includes('fav-btn on'));

sec('3. 收藏 CRUD（localStorage）');
ok('FAVORITES_KEY 约定', E.FAVORITES_KEY === 'wzh_favorites');
ok('getFavorites 读 localStorage', /function getFavorites\(\)[\s\S]*?getItem\(E\.FAVORITES_KEY\)/.test(html));
ok('saveFavorites 写 localStorage', /function saveFavorites\([\s\S]*?setItem\(E\.FAVORITES_KEY/.test(html));
ok('addFavorite 去重（已存在返回 false）', /function addFavorite\([\s\S]*?E\.isFavorited\(list, url\)/.test(html));
ok('收藏结构含 4 字段', /originalUrl: url[\s\S]*?riskLabel:[\s\S]*?finalUrl:[\s\S]*?createdAt:/.test(html));
ok('removeFavoriteByUrl 存在', /function removeFavoriteByUrl\(/.test(html));
ok('toggleFavorite 切换', /function toggleFavorite\([\s\S]*?isFavUrl\(url\)/.test(html));

sec('4. 收藏面板');
ok('dtabs 切换标签', html.includes('switchDataTab(\'history\')') && html.includes('switchDataTab(\'favorites\')'));
ok('收藏面板容器存在', html.includes('id="panelFavorites"'));
ok('收藏列表容器', html.includes('id="favBody"'));
ok('收藏筛选栏', html.includes('id="favFilterBar"'));
ok('一键清空收藏', html.includes('clearFavorites()'));
ok('导出收藏按钮', html.includes("doExport('favorites')"));
ok('switchDataTab 函数', /function switchDataTab\(tab\)/.test(html));
ok('renderFavorites 函数', /function renderFavorites\(\)/.test(html));
ok('收藏行有取消收藏★', /unfavFromPanel\(/.test(html));
ok('收藏行有复制/二维码/AI解读', /renderFavorites[\s\S]*?copyLinkFrom[\s\S]*?openQrFrom[\s\S]*?aiInterpretFromAttr/.test(html));

sec('5. 收藏面板复用筛选');
ok('refreshFilterViews 支持 favorites', /area === 'favorites'[\s\S]*?renderFavFilterBar[\s\S]*?renderFavorites/.test(html));
ok('renderFavFilterBar 复用标签体系', /function renderFavFilterBar\([\s\S]*?buildFilterBarHtml\('history'\)/.test(html));
ok('收藏筛选 onclick area 替换为 favorites', /split\("'history'"\)\.join\("'favorites'"\)/.test(html));

sec('6. 导出收藏');
ok('doExport 有 favorites 分支', /scope === 'favorites'[\s\S]*?buildReportText/.test(html));
ok('收藏导出走 history source', /buildReportText\(fRows, \{ source: 'history' \}\)/.test(html));
ok('导出菜单有收藏夹选项', html.includes('id="expFav"'));

sec('7. 联动');
ok('取消收藏同步刷新历史/批量', /removeFavoriteByUrl\(url\);[\s\S]*?renderHistoryBody\(\)[\s\S]*?rerenderBatchTable\(\)/.test(html));
ok('刷新页面不丢（localStorage 持久化）', /localStorage\.setItem\(E\.FAVORITES_KEY/.test(html));

sec('8. 红线');
ok('收藏不传后端（无 requestJSON/postJSON）', !/function addFavorite\((?:(?!\n\})[\s\S])*?(requestJSON|postJSON)\(/.test(html) && !/function saveFavorites\((?:(?!\n\})[\s\S])*?(requestJSON|postJSON)\(/.test(html));
ok('历史/批量/导出/AI解读/复制/二维码关键函数仍在', ['loadHistory','batchDetect','doExport','aiInterpretFromRow','copyLink','openQrModal','buildQrMatrix','applyFilterTags'].every(k => html.includes(k) || E[k]));
ok('引擎只新增收藏纯函数导出', ['favoriteView','isFavorited','removeFavorite','LABEL_TO_LEVEL'].every(k => typeof E[k] === 'function' || E[k] !== undefined));

console.log('\n' + '='.repeat(50));
console.log('  通过 ' + pass + ' 项，失败 ' + fail + ' 项');
console.log('='.repeat(50));
process.exit(fail ? 1 : 0);
