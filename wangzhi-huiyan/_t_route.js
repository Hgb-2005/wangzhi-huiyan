const E = require('./engine.js');

// [输入, 期望类型, 说明]
const cases = [
  // ---- 用户报的两个 bug ----
  ['听音乐', 'intent', 'bug2：短需求应走推荐'],
  ['看电影', 'intent', '短需求'],
  ['asdf乱码qwe123.cn测试xyz', 'garbage', 'bug1：乱码含域名片段，应无法识别'],
  ['乱码乱码乱码乱码', 'garbage', '纯重复中文'],
  ['啊啊啊啊？？？', 'garbage', '无意义重复+标点'],

  // ---- 网址（通道 A）----
  ['baidu.com', 'url', '裸域名'],
  ['https://www.baidu.com', 'url', '完整URL'],
  ['http://0k.cn-security-check.top/login', 'url', '带路径URL'],
  ['www.baidu.com', 'url', 'www 前缀'],
  ['a.b', 'url', '短域名'],
  ['abc.def', 'url', '双字母TLD'],
  ['https://example.com/中文路径', 'url', 'URL里带中文(整体仍是网址)'],

  // ---- 需求（通道 B）----
  ['我想找个能在线听歌的网站', 'intent', '完整需求'],
  ['帮找个能听歌的网站', 'intent', '口语化'],
  ['免费音乐网站', 'intent', '无动词需求'],
  ['你好', 'intent', '简单中文'],
  ['qwerty', 'intent', '英文单词'],
  ['推荐在线图片压缩工具 无广告', 'intent', '原有功能用例'],
  ['哪里有生成二维码的在线工具', 'intent', '原有功能用例'],
  ['我想找免费画流程图的网站', 'intent', '原有功能用例'],
  ['我想找翻墙工具', 'intent', '违禁需求(交给推荐逻辑拦截)'],
  ['write music', 'intent', '英文需求'],

  // ---- 无法识别 ----
  ['zX9#kL2', 'garbage', '符号乱码'],
  ['123456', 'garbage', '纯数字（URL会按整数IP解析，必须挡住）'],
  ['。。。！！！', 'garbage', '纯标点'],
  ['啊', 'garbage', '单字(实质<2)'],
  ['', 'garbage', '空串'],

  // ---- 冲突（URL + 文字）----
  ['帮我检测 https://www.baidu.com 这个网站安全吗', 'conflict', 'URL是主体+需求文字'],
  ['https://baidu.com 帮我看看', 'conflict', 'URL+短文字'],

  // ---- 边界：IP 与裸域名 ----
  ['1.2.3.4', 'url', '裸IPv4'],
  ['http://1.2.3.4', 'url', '带协议IPv4'],
  ['qwe123.cn', 'url', '裸域名（合法域名即检测意图）'],
  ['zzz.zzz', 'url', '裸域名（合法域名即检测意图）'],
  ['我想访问 baidu.com', 'garbage', '中文句子里夹带裸域名→无效输入'],
  ['你好.baidu.com', 'garbage', '中文+裸域名混合→无效输入'],
];

let pass = 0, fail = 0;
const fails = [];
for (const [t, expect, desc] of cases) {
  const r = E.routeInput(t);
  const ok = r.type === expect;
  if (ok) pass++; else { fail++; fails.push({ t, expect, got: r.type, desc }); }
}
fails.forEach(f => console.log('  FAIL', JSON.stringify(f.t).padEnd(42), '=>', f.got, '(期望 ' + f.expect + ')', f.desc));
console.log('通过 ' + pass + ' / 失败 ' + fail);

// ---- 专项验证：乱码绝不能产出"url"类型 ----
// 注意：qwe123.cn / zzz.zzz 这类是语法合法的裸域名，用户输入即"检测这个域名"，
// 判 url 是正确行为，不属于乱码，已移到主用例的「裸域名」组
console.log('\n=== 专项：乱码绝不走网址检测 ===');
const garbages = [
  'asdf乱码qwe123.cn测试xyz', '测试abc.def乱码', 'xyz.abc纯乱码',
  '1a2b.cn乱码', 'aaa.bbb乱码xxx', '!!!abc.def!!!', '乱码乱码.cn',
  '中文abc.com中文', '你好.baidu.com', '123456', 'zX9#kL2',
  '乱码乱码乱码乱码', '啊啊啊', '哈哈哈哈'
];
let bad = 0;
for (const g of garbages) {
  const r = E.routeInput(g);
  const isUrlOrConflict = (r.type === 'url' || r.type === 'conflict');
  if (isUrlOrConflict) bad++;
  console.log(' ', (isUrlOrConflict ? 'BAD ' : 'ok  '), JSON.stringify(g).padEnd(30), '=>', r.type);
}
console.log(bad === 0 ? '✅ 乱码全部未误判为网址' : '❌ 有 ' + bad + ' 条乱码被误判为网址');

// ---- 专项：常见需求词全部走 intent ----
console.log('\n=== 专项：常见需求全部走推荐 ===');
const demands = ['听音乐', '看电影', '听歌', '看剧', '看书', '网购', '打车', '外卖',
  '学英语', '找工作', '租房', '旅游', '做饭', '健身', '拍照', '修图', '写代码',
  '在线翻译', '免费网盘', 'PDF转换', '视频剪辑', '画画', '做表格', '写文档', '查资料'];
let badD = 0;
for (const d of demands) {
  const r = E.routeInput(d);
  if (r.type !== 'intent') { badD++; console.log('  BAD', d, '=>', r.type); }
}
console.log(badD === 0 ? '✅ ' + demands.length + ' 个需求词全部正确走推荐' : '❌ ' + badD + ' 个需求词未走推荐');

// ---- GARBAGE_TEXT 文案 ----
console.log('\n=== 无法识别提示文案 ===');
console.log('  ', E.GARBAGE_TEXT);
