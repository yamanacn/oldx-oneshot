// 逐词时间：T("第一件") 返回口播里说到这几个字的时刻（秒）。画面上的东西什么时候出现，都从这里取。
//
// 识别结果有同音错字（实测：zcode 识别成 ccode、"他"识别成"它"）。查找顺序：
//   1. 原样找；
//   2. 查 data/aliases.json（{"写在代码里的字": "识别出来的字"}），把别名换进去再找；
//   3. 模糊找：同样长度、错字不超过三分之一的位置（至少要对上一半以上的字），在控制台提示用了哪段识别文字；
//   4. 都找不到才报错，错误里给出最接近的一段识别文字，照着改 key 或加别名。
import aliasData from '../data/aliases.json';
import data from '../data/words.json';

type Word = {text?: string; word?: string; start: number; end: number};

const STRIP = /[\s，。、！？!?；;：:“”"'（）()\-—…,.·]/g;
const chars: string[] = [];
const times: number[] = [];
for (const w of (data as {words: Word[]}).words) {
  const txt = (w.text ?? w.word ?? '').replace(STRIP, '');
  [...txt].forEach((ch, k) => {
    chars.push(ch.toLowerCase());
    times.push(w.start + ((w.end - w.start) * k) / Math.max(1, txt.length));
  });
}
const joined = chars.join('');
const ALIASES = aliasData as Record<string, string>;
const norm = (s: string) => s.replace(STRIP, '').toLowerCase();

/** 第 n 次出现的位置；找不到返回 -1。原样找不到时依次试别名和模糊匹配 */
const locate = (key: string, n: number): number => {
  const k = norm(key);
  const exact = (s: string) => {
    let i = -1;
    for (let c = 0; c < n; c++) { i = joined.indexOf(s, i + 1); if (i < 0) return -1; }
    return i;
  };
  let i = exact(k);
  if (i >= 0) return i;
  let a = k;
  for (const [from, to] of Object.entries(ALIASES)) a = a.split(norm(from)).join(norm(to));
  if (a !== k && (i = exact(a)) >= 0) return i;
  // 模糊：逐个位置数错字
  const kc = [...k], L = kc.length;
  const maxMiss = Math.max(1, Math.floor(L / 3));
  const hits: number[] = [];
  for (let p = 0; p + L <= chars.length; p++) {
    let miss = 0;
    for (let q = 0; q < L && miss <= maxMiss; q++) if (chars[p + q] !== kc[q]) miss++;
    if (miss <= maxMiss && miss < L / 2) { hits.push(p); p += L - 1; }
  }
  if (hits.length >= n) {
    const p = hits[n - 1];
    console.warn(`T("${key}") 没有原样找到，用了识别文字「${chars.slice(p, p + L).join('')}」（${times[p].toFixed(2)} 秒）`);
    return p;
  }
  return -1;
};
const nearest = (key: string) => {
  const kc = [...norm(key)], L = kc.length;
  let best = 0, bestScore = -1;
  for (let p = 0; p + L <= chars.length; p++) {
    let s = 0;
    for (let q = 0; q < L; q++) if (chars[p + q] === kc[q]) s++;
    if (s > bestScore) { bestScore = s; best = p; }
  }
  return `最接近的识别文字是「${chars.slice(best, best + L).join('')}」（${(times[best] ?? 0).toFixed(2)} 秒）`;
};

/** 口播里第 n 次说到 key 的时刻（key 第一个字开始说的时间）。找不到直接报错，避免时间悄悄错位。 */
export const T = (key: string, n = 1): number => {
  const i = locate(key, n);
  if (i < 0) throw new Error(`逐词时间里找不到「${key}」（第 ${n} 次）。${nearest(key)}；改 key 或在 data/aliases.json 里加别名`);
  return times[i];
};

/** key 里每个字（去掉标点空格后）各自开始的时刻：逐字出现的大标题用 */
export const TT = (key: string, n = 1): number[] => {
  const i = locate(key, n);
  if (i < 0) throw new Error(`逐词时间里找不到「${key}」（第 ${n} 次）。${nearest(key)}`);
  return times.slice(i, i + [...norm(key)].length);
};

/** 给字幕逐字高亮用：从 from 位置往后找一句话的起点；charTime 取第 i 个字开始的时刻 */
export const findFrom = (text: string, from: number) => joined.indexOf(norm(text), from);
export const charTime = (i: number) => times[i];
