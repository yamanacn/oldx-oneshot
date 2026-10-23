// 背景音乐的节拍（成片时间轴上的秒）。remotion_prepare.py --bgm 会用 librosa 测出来写进 data/beats.json；
// 没给音乐时数组是空的，下面的函数原样返回，不影响画面。
//   snap(t)       吸到最近的半拍上（偏移超过 tol 就不吸，以口播为准）
//   bar(t)        t 之后最近的小节第一拍（窗口内找不到就用 t）——满屏换场、大动作放在这里
//   beatInfo(t)   现在是第几拍、离上一拍多久——做节拍灯、每拍的小脉冲
import data from '../data/beats.json';

const B = data as {tempo: number; offset: number; beats: number[]; downbeats: number[]};
const HALF = B.beats.flatMap((b, i) => (i + 1 < B.beats.length ? [b, (b + B.beats[i + 1]) / 2] : [b]));

export const tempo = B.tempo;
export const snap = (t: number, tol = 0.12) => {
  let best = t, d = tol;
  for (const h of HALF) if (Math.abs(h - t) < d) { d = Math.abs(h - t); best = h; }
  return best;
};
export const bar = (t: number, win = 1.2) => {
  for (const b of B.downbeats) if (b >= t - 0.05 && b - t <= win) return b;
  return t;
};
export const beatInfo = (t: number) => {
  let i = -1;
  while (i + 1 < B.beats.length && B.beats[i + 1] <= t) i++;
  let j = -1;
  while (j + 1 < B.downbeats.length && B.downbeats[j + 1] <= t) j++;
  // 这一拍是小节里的第几拍（0–3）：从上一个小节第一拍数起
  const inBar = j >= 0 ? B.beats.filter((b) => b >= B.downbeats[j] - 0.02 && b <= t).length - 1 : -1;
  return {beat: i, sinceBeat: i >= 0 ? t - B.beats[i] : Infinity, bar: j, sinceBar: j >= 0 ? t - B.downbeats[j] : Infinity, inBar};
};
