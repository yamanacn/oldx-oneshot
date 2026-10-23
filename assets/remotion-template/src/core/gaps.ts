// 空档里的人物是定格的：timeline_edit.py 插空档时，人物视频补的是切点前一帧的定格画面。
// 讲者在空档里露面，观众就会看到"人卡了一下又动起来"（实测用户看样段一眼就指出来了）。这里给两样工具：
//
//   const KEYS = guardGaps(myKeys, POSES, {off: 'OFF', dur: 0.75, lead: 0.3});   // 自动在每个空档前让讲者离场、空档结束那一刻回到原站位
//   assertNoFrozen(POSES, KEYS, {dur: 0.75, covered: (t, g) => …});              // 渲染时逐帧查：空档里讲者在画面上又没被盖住就直接报错
//
// covered：空档里哪些时刻画面被满屏转场盖住了（盖住时讲者在不在画面上都看不见）。不给就当完全没盖，空档里讲者必须在画外。
// 片尾定格（--tail）也算一个空档（note 为 'tail'），结束后不再回场。
import words from '../data/words.json';
import {Box, cardBox, PoseKey} from './PresenterCard';

export type Gap = {start: number; end: number; dur: number; hide_captions: boolean; note: string};
type Edit = {gaps: Gap[]; tail: number; total: number};
const EDIT = (words as unknown as {edit?: Edit}).edit;

/** 全部空档（含片尾定格），按时间排好 */
export const GAPS: Gap[] = [
  ...(EDIT?.gaps ?? []),
  ...(EDIT && EDIT.tail > 0 ? [{start: EDIT.total - EDIT.tail, end: EDIT.total, dur: EDIT.tail, hide_captions: true, note: 'tail'}] : []),
];

/** t 时刻生效的站位：at ≤ t 的键里时间最晚的那个（键的顺序不要求排好） */
const poseAt = <P extends Record<string, Box>>(keys: PoseKey<P>[], t: number) => {
  let best: PoseKey<P> | null = null;
  for (const key of keys) if (key[0] <= t && (!best || key[0] >= best[0])) best = key;
  return (best ?? [...keys].sort((a, b) => a[0] - b[0])[0])[1];
};

type Opts<P> = {
  /** 画面外的那个站位名 */
  off: keyof P & string;
  /** 换站位用多少秒（和 PresenterCard 的 dur 一致） */
  dur?: number;
  /** 满屏转场比空档早多少秒盖住画面；有转场的空档（hide_captions）讲者在这一刻离场，被转场盖着走 */
  lead?: number;
};

/**
 * 把站位表改成"空档里讲者不出现"：
 * - 带转场的空档（hide_captions）：讲者在转场盖住画面时离场，空档结束那一刻回到原站位（这时人物视频已经在动）；
 * - 演示等待之类的空档：讲者在空档开始前就完全滑出画面；
 * - 落在"离场 → 空档结束"之间的原有站位，都推迟到空档结束再生效。
 */
export const guardGaps = <P extends Record<string, Box>>(keys: PoseKey<P>[], _poses: P, {off, dur = 0.75, lead = 0.3}: Opts<P>): PoseKey<P>[] => {
  let out: PoseKey<P>[] = [...keys];
  for (const g of GAPS) {
    const before = poseAt(out, Math.max(0, g.start - 0.01));
    const after = poseAt(out, g.end + 0.01);
    if (before === off && after === off) continue;
    const leave = g.hide_captions ? g.start - lead + 0.1 : g.start - dur - 0.05;
    out = out.map(([at, k]): PoseKey<P> => (at >= leave && at < g.end ? [g.end, k] : [at, k]));
    if (before !== off) out.push([leave, off]);
    if (g.note !== 'tail' && !out.some(([at]) => at === g.end)) out.push([g.end, after]);
  }
  const seen = new Map<number, keyof P & string>();
  for (const [at, k] of out) seen.set(at, k);
  return [...seen.entries()].sort((a, b) => a[0] - b[0]).map(([at, k]) => [at, k]);
};

/** 逐帧查空档：讲者在画面上、画面又没被盖住，就抛错（渲染直接失败，免得交出定格的人） */
export const assertNoFrozen = <P extends Record<string, Box>>(poses: P, keys: PoseKey<P>[], {dur = 0.75, width = 1920, height = 1080, covered = () => false}:
  {dur?: number; width?: number; height?: number; covered?: (t: number, g: Gap) => boolean}) => {
  const bad: string[] = [];
  for (const g of GAPS) {
    for (let t = g.start + 0.01; t < g.end; t += 1 / 30) {
      const b = cardBox(t, poses, keys, dur);
      const visible = b.x + b.w > 0 && b.x < width && b.y + b.h > 0 && b.y < height;
      if (visible && !covered(t, g)) { bad.push(`${g.start.toFixed(2)}–${g.end.toFixed(2)}（${g.note}）在 ${t.toFixed(2)} 秒`); break; }
    }
  }
  if (bad.length) throw new Error('空档里人物视频是定格的，讲者却出现在画面上：' + bad.join('；'));
};
