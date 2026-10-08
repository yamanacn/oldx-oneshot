// 逐词时间：T("第一件") 返回口播里说到这几个字的时刻（秒）。画面上的东西什么时候出现，都从这里取。
import data from '../data/words.json';

type Word = {text?: string; word?: string; start: number; end: number};

const STRIP = /[\s，。、！？!?；;：:“”"'（）()\-—…,.]/g;
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

/** 口播里第 n 次说到 key 的时刻（key 第一个字开始说的时间）。找不到直接报错，避免时间悄悄错位。 */
export const T = (key: string, n = 1): number => {
  const k = key.replace(STRIP, '').toLowerCase();
  let i = -1;
  for (let c = 0; c < n; c++) {
    i = joined.indexOf(k, i + 1);
    if (i < 0) throw new Error(`逐词时间里找不到「${key}」（第 ${n} 次）`);
  }
  return times[i];
};
