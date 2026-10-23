// 人物站位系统：人物视频做成一张"卡"，在几个命名站位之间移动、变形、滑出画面。
// 竖屏人物放进横屏、人物左右换位、缩成角上的小方窗或圆头像、居中打照面……都用它；core/Presenter 只适合"全屏 ⇄ 右下角小窗"。
//
//   const POSES = {                                   // 每个站位：位置和大小（像素），圆角半径（像素，等于宽高一半就是圆）
//     R:    {x: 1187, y: 104, w: 583, h: 778, r: 0},
//     MINI: {x: 1666, y: 730, w: 190, h: 190, r: 22},
//     OFF:  {x: 2040, y: 104, w: 583, h: 778, r: 0},  // 出画也是一个站位：放到画面外
//   };
//   <PresenterCard poses={POSES} keys={[[0, 'R'], [T('所以我') - 0.25, 'MINI'], …]} focus={[0.5, 0.25]} decorate={…} />
//   logCardSpec(POSES, KEYS)                          // 在 Film.tsx 末尾调一次，质检脚本按它知道人物每段在哪
//
// 保证的事（和 core/Presenter 一样）：同一段人物视频、同一个时钟；静音（声音只来自 voice.wav）；等比裁切，不挤压人脸；
// 换站位用平滑的缓入缓出，脸不回弹。站位、形状、什么时候换，全由这条片子的设计决定（用户没说时，默认人物在右侧）。
import React from 'react';
import {OffthreadVideo, staticFile, useVideoConfig} from 'remotion';
import {clamp, useNow} from '../lib/motion';

export type Box = {x: number; y: number; w: number; h: number; r: number};
export type PoseKey<P> = [number, keyof P & string];

const ease = (x: number) => { x = clamp(x); return x === 0 ? 0 : x === 1 ? 1 : x < 0.5 ? Math.pow(2, 20 * x - 10) / 2 : (2 - Math.pow(2, -20 * x + 10)) / 2; };
const mix = (a: Box, b: Box, p: number): Box => ({x: a.x + (b.x - a.x) * p, y: a.y + (b.y - a.y) * p, w: a.w + (b.w - a.w) * p, h: a.h + (b.h - a.h) * p, r: a.r + (b.r - a.r) * p});

/** 这一刻卡片的位置和大小：每个 key 从它的时刻开始用 dur 秒换到新站位 */
export const cardBox = <P extends Record<string, Box>>(t: number, poses: P, keys: PoseKey<P>[], dur = 0.65): Box => {
  let cur: Box = poses[keys[0][1]];
  for (let i = 1; i < keys.length; i++) {
    const [at, k] = keys[i];
    if (t < at) break;
    cur = mix(cur, poses[k], ease((t - at) / dur));
  }
  return cur;
};

type Props<P extends Record<string, Box>> = {
  poses: P;
  keys: PoseKey<P>[];
  /** 换站位用多少秒 */
  dur?: number;
  /** 人物画面在卡里的取景中心（0–1）：竖屏人物裁成横向或方形卡时，围着头肩取景 */
  focus?: [number, number];
  /** 推近：返回 1 以上的缩放（强调处轻推 1.03–1.08） */
  zoom?: (t: number) => number;
  /** 卡片的边框、角标、标签、投影：拿到这一刻的 box */
  decorate?: (box: Box, t: number) => {back?: React.ReactNode; front?: React.ReactNode};
  src?: string;
};

export const PresenterCard = <P extends Record<string, Box>>({poses, keys, dur = 0.65, focus = [0.5, 0.25], zoom, decorate, src = 'presenter.mp4'}: Props<P>) => {
  const {width: W, height: H} = useVideoConfig();
  const t = useNow();
  const b = cardBox(t, poses, keys, dur);
  if (b.x > W || b.y > H || b.x + b.w < 0 || b.y + b.h < 0) return null; // 在画面外：不画（视频时钟不受影响）
  const z = zoom ? zoom(t) : 1;
  const d = decorate ? decorate(b, t) : {};
  return (
    <div style={{position: 'absolute', left: b.x, top: b.y, width: b.w, height: b.h}}>
      {d.back}
      <div style={{position: 'absolute', inset: 0, borderRadius: b.r, overflow: 'hidden', background: '#000'}}>
        <OffthreadVideo muted src={staticFile(src)} style={{width: '100%', height: '100%', objectFit: 'cover', objectPosition: `${focus[0] * 100}% ${focus[1] * 100}%`,
          transform: `scale(${z})`, transformOrigin: `${focus[0] * 100}% ${focus[1] * 100 + 10}%`}} />
      </div>
      {d.front}
    </div>
  );
};

/** 把每段的站位打印出来给质检脚本（remotion_spec.py → av_check.py）。在 Film.tsx 末尾调用一次 */
export const logCardSpec = <P extends Record<string, Box>>(poses: P, keys: PoseKey<P>[], total: number, dur = 0.65) => {
  const spans = keys.map(([at, k], i) => ({start: at + dur, end: i + 1 < keys.length ? keys[i + 1][0] : total, pose: k, box: poses[k]}))
    .filter((s) => s.end - s.start > 0.5);
  console.log('CARD_SPEC ' + JSON.stringify({spans, dur}));
};
