import React from 'react';
import {OffthreadVideo, staticFile, useVideoConfig} from 'remotion';
import {clamp, easeInOut, useNow} from '../lib/motion';

export type Span = [number, number];

/** 人物缩小以后的样子。尺寸、边距是画面短边的比例 */
export type Corner = {
  /** 小窗的宽和高 */
  w: number;
  h: number;
  /** 离右边和下边多远；要分别设时用 marginX、marginY */
  margin: number;
  marginX?: number;
  marginY?: number;
  /** 圆角：0 是直角，1 是最圆（宽高相等时就是正圆） */
  radius: number;
  /** 缩小、放大各用多少秒 */
  transition: number;
  /** 小窗滑出、滑入画面各用多少秒 */
  slide: number;
  /** 头肩中心在人物画面里的位置（0–1）。小窗比画面窄或矮时围着这一点取景 */
  focus: [number, number];
  /** 小窗里的人物再放大多少（1 是刚好铺满小窗） */
  zoom: number;
};
export const CORNER_DEFAULTS: Corner = {w: 0.2, h: 0.2, margin: 0.03, radius: 1, transition: 0.5, slide: 0.45, focus: [0.5, 0.38], zoom: 1};

/** 小窗这一刻的位置和大小（像素）。p 是进度：0 全屏，1 完全缩到右下角 */
export type Box = {x: number; y: number; w: number; h: number; r: number; p: number};

type Props = {
  src?: string;
  /** 这些区间里人物缩到右下角，主画面让给内容；其余时间人物全屏 */
  runs?: Span[];
  /**
   * 这些区间里不显示人物（内容需要整个画面，或人物视频这几秒有问题要遮掉）。人物视频照常往前走，回来时仍然对着当前的口播。
   * 人物不会在原地凭空消失、凭空出现：缩在右下角时，是整张小窗向右滑出画面、再滑回来；全屏时才是很短的淡出淡入（所以全屏时的隐藏要放在换场的那一刻）。
   * 想让人物“先全屏打个照面再缩到角上”地回来：让隐藏区间结束在某个缩小区间开始之前一两秒。
   */
  hide?: Span[];
  corner?: Partial<Corner>;
  /** 给小窗加的东西：垫在后面的、盖在上面的（边框、投影、装饰），由这条片子的设计决定 */
  decorate?: (box: Box) => {back?: React.ReactNode; front?: React.ReactNode};
};

/**
 * 人物层：同一段正在播放的人物视频，全屏，或缩小后放在右下角。
 *
 * 这个组件只保证三件事，其余都留给这条片子的设计：
 * 1. 人物的画面永远对着当前的口播——缩小、隐藏、再出现都不会让视频重新计时。
 * 2. 全程等比缩放，不挤压人脸；缩小后的位置在右下角（这是用户定的唯一一条版式要求）。
 * 3. 人物消失和再出现有来路：在角上时滑出、滑入画面，不在原地淡入淡出。
 * 小窗的大小、形状、边框、什么时候缩、缩多久，都在调用的地方定。
 */
export const Presenter: React.FC<Props> = ({src = 'presenter.mp4', runs = [], hide = [], corner: over, decorate}) => {
  const {width: W, height: H} = useVideoConfig();
  const t = useNow();
  const c = {...CORNER_DEFAULTS, ...over};
  let p = 0;
  for (const [a, b] of runs) {
    if (t >= a && t < b) p = Math.min(easeInOut((t - a) / c.transition), easeInOut((b - t) / c.transition));
  }
  // 隐藏：vis 从 1 到 0 再回到 1。缩在角上时用来把小窗滑出画面，全屏时用来淡出
  let vis = 1;
  for (const [a, b] of hide) {
    const d = p > 0.5 ? c.slide : 0.15;
    if (t >= a && t < b) vis = Math.min(vis, 1 - Math.min(clamp((t - a) / d), clamp((b - t) / d)));
  }
  const shown = easeInOut(vis);
  const short = Math.min(W, H);
  const bw = W + (c.w * short - W) * p;
  const bh = H + (c.h * short - H) * p;
  const mx = (c.marginX ?? c.margin) * short;
  const my = (c.marginY ?? c.margin) * short;
  // 滑出去的距离：从小窗现在的位置到完全出画面，再多一点（把装饰的边和投影也带出去）
  const away = p > 0.5 ? (1 - shown) * (mx + c.w * short + 0.06 * W) : 0;
  const alpha = p > 0.5 ? (vis > 0 ? 1 : 0) : shown;
  const bx = (W - mx - c.w * short) * p + away;
  const by = (H - my - c.h * short) * p;
  const r = (p * c.radius * Math.min(bw, bh)) / 2;
  // 宽高用同一个缩放系数，所以人脸不会被挤压；取景围着 focus，并且不超出源画面
  const s = Math.max(bw / W, bh / H) * (1 + (c.zoom - 1) * p);
  const fx = 0.5 + (c.focus[0] - 0.5) * p;
  const fy = 0.5 + (c.focus[1] - 0.5) * p;
  const tx = clamp(bw / 2 - fx * W * s, bw - W * s, 0);
  const ty = clamp(bh / 2 - fy * H * s, bh - H * s, 0);
  const d = decorate && p > 0 ? decorate({x: bx, y: by, w: bw, h: bh, r, p}) : {};
  return (
    <div style={{position: 'absolute', left: 0, top: 0, width: bw, height: bh, transform: `translate(${bx}px, ${by}px)`, opacity: alpha}}>
      {d.back}
      <div style={{position: 'absolute', inset: 0, overflow: 'hidden', borderRadius: r}}>
        <OffthreadVideo muted src={staticFile(src)} style={{position: 'absolute', left: 0, top: 0, width: W, height: H, maxWidth: 'none', transformOrigin: '0 0', transform: `translate(${tx}px, ${ty}px) scale(${s})`}} />
      </div>
      {d.front}
    </div>
  );
};

/** 把小窗区间和参数打印出来，供 scripts/packaging/remotion_spec.py 取走（质检脚本要用）。在 Film.tsx 末尾调用一次 */
export const logSpec = (runs: Span[], corner?: Partial<Corner>) => {
  const c = {...CORNER_DEFAULTS, ...corner};
  const d = Math.min(c.w, c.h);
  // 质检脚本按“右下角一个圆”取小窗区域：用小窗里能放下的最大的圆，圆心和小窗中心重合
  const pip = {diameter: d, margin: c.margin, marginX: (c.marginX ?? c.margin) + (c.w - d) / 2, marginY: (c.marginY ?? c.margin) + (c.h - d) / 2, transition: c.transition, focus: c.focus, focusSize: 0.55};
  console.log('PIP_SPEC ' + JSON.stringify({runs, pip}));
};
