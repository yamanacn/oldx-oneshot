// 演示窗口（工具，不带设计）：浮起的窗口 + 运镜。里面可以是录屏（Demo），也可以是代码复刻的界面（Window 直接给 children）。
// - 变速：成片时间 → 录屏时间按锚点分段线性映射（加载、停顿快放；结果停稳时慢放到近乎定格；同一时刻两个锚点 = 跳过一段，例如加载白屏），只改录屏速度，不碰人声
// - 运镜：按关键帧推近到点击处，窗口里的覆盖层（遮挡胶囊、高亮框）跟着画面一起缩放
// - 光标：点击处光圈 + 按下波纹；代码复刻的界面用 Cursor 画光标
// 颜色、边框、角标由这条片子的设计决定：accent、frameStyle、tag 都从外面传。录屏先用 scripts/packaging/demo_prep.py 裁边、打码、转恒定帧率。
//
//   <Demo src="assets/demo_x.mp4" srcW={1920} srcH={952} show={[t0, t1]} rect={{x, y, w, h}}
//         anchors={[[T('点击创建'), 12.0], [T('点击创建') + 0.9, 13.4], …]}        // [成片秒, 录屏秒]
//         cam={[[t0, 960, 476, 1], [T('点击创建') - 0.6, 1600, 360, 1.9], …]}       // [成片秒, 中心 x, 中心 y, 放大倍数, 用时]
//         clicks={[[12.5, 1748, 352]]}                                             // [录屏秒, x, y]（录屏坐标）
//         accent="#c8fe37" />
import React from 'react';
import {OffthreadVideo, Sequence, staticFile, useVideoConfig} from 'remotion';
import {clamp, useNow} from '../lib/motion';

const prog = (t: number, a: number, d: number) => clamp((t - a) / d);
const lerp = (a: number, b: number, p: number) => a + (b - a) * p;
const expoOut = (x: number) => { x = clamp(x); return x === 1 ? 1 : 1 - Math.pow(2, -10 * x); };
const expoIn = (x: number) => { x = clamp(x); return x === 0 ? 0 : Math.pow(2, 10 * x - 10); };
const expoInOut = (x: number) => { x = clamp(x); if (x === 0 || x === 1) return x; return x < 0.5 ? Math.pow(2, 20 * x - 10) / 2 : (2 - Math.pow(2, -20 * x + 10)) / 2; };
export type Anchor = [film: number, video: number];
export type CamKey = [film: number, cx: number, cy: number, zoom: number, dur?: number];
export type Click = [at: number, x: number, y: number];
export type Rect = {x: number; y: number; w: number; h: number};

/** 成片时间 → 录屏时间 */
export const mapTime = (anchors: Anchor[], t: number) => {
  if (t <= anchors[0][0]) return anchors[0][1];
  for (let i = 1; i < anchors.length; i++) {
    const [f0, v0] = anchors[i - 1], [f1, v1] = anchors[i];
    if (t <= f1) return f1 - f0 < 1e-3 ? v1 : v0 + (v1 - v0) * ((t - f0) / (f1 - f0));
  }
  return anchors[anchors.length - 1][1];
};
/** 录屏时间 → 成片时间（录屏里的点击发生在成片哪一刻） */
export const unmapTime = (anchors: Anchor[], v: number) => {
  for (let i = 1; i < anchors.length; i++) {
    const [f0, v0] = anchors[i - 1], [f1, v1] = anchors[i];
    if (v >= v0 && v <= v1 && v1 > v0) return f0 + (f1 - f0) * ((v - v0) / (v1 - v0));
  }
  return anchors[anchors.length - 1][0];
};

const camAt = (keys: CamKey[], t: number) => {
  let cur = {cx: keys[0][1], cy: keys[0][2], z: keys[0][3]};
  for (let i = 1; i < keys.length; i++) {
    const [at, cx, cy, z, d = 0.8] = keys[i];
    if (t < at) break;
    const p = expoInOut(prog(t, at, d));
    cur = {cx: lerp(cur.cx, cx, p), cy: lerp(cur.cy, cy, p), z: lerp(cur.z, z, p)};
  }
  return cur;
};

type WinProps = {
  srcW: number;
  srcH: number;
  show: [number, number];
  cam: CamKey[];
  rect: Rect | ((t: number) => Rect);
  /** 点击（成片时间、界面坐标） */
  clicks?: Click[];
  children?: (t: number) => React.ReactNode;
  /** 窗口外、屏幕坐标的标注：给出这一刻界面坐标 → 屏幕坐标的换算 */
  outside?: (t: number, toScreen: (x: number, y: number) => [number, number]) => React.ReactNode;
  from?: 'below' | 'right' | 'none';
  /** 左上角的小标签（代码复刻的界面一定要标，例如"界面演示"） */
  tag?: string;
  bg?: string;
  /** 强调色：边光、光标光圈、标签 */
  accent?: string;
  /** 窗口外框的样式（边光、投影），不给就用 accent 画一圈细边光 */
  frameStyle?: React.CSSProperties;
  radius?: number;
};

export const Window: React.FC<WinProps> = ({srcW, srcH, show, cam, rect: rectIn, clicks = [], children, outside, from = 'below', tag, bg = '#000', accent = '#ffffff', frameStyle, radius = 18}) => {
  const t = useNow();
  const [s0, s1] = show;
  if (t < s0 - 0.01 || t > s1 + 0.01) return null;
  const rect = typeof rectIn === 'function' ? rectIn(t) : rectIn;
  const pin = from === 'none' ? 1 : expoOut(prog(t, s0, 0.9));
  const pout = from === 'none' ? 0 : expoIn(prog(t, s1 - 0.5, 0.5));
  const c = camAt(cam, t);
  const s = (rect.w / srcW) * c.z;
  // 画面平移：让 (cx, cy) 落在窗口中心，但不露出界面以外的地方
  const tx = clamp(rect.w / 2 - c.cx * s, rect.w - srcW * s, 0);
  const ty = clamp(rect.h / 2 - c.cy * s, rect.h - srcH * s, 0);
  const toScreen = (x: number, y: number): [number, number] => [rect.x + tx + x * s, rect.y + ty + y * s];
  const enterY = from === 'below' ? (1 - pin) * 160 : 0;
  const enterX = from === 'right' ? (1 - pin) * 300 : 0;
  return (
    <>
      <div style={{position: 'absolute', left: rect.x, top: rect.y, width: rect.w, height: rect.h, perspective: 1800, opacity: Math.min(1, pin * 1.6) * (1 - pout)}}>
        <div style={{position: 'absolute', inset: 0, transformStyle: 'preserve-3d',
          transform: `translate(${enterX}px, ${enterY + pout * 60}px) rotateX(${(1 - pin) * 14}deg) scale(${lerp(0.94, 1, pin) - pout * 0.04})`}}>
          <div style={{position: 'absolute', inset: -1, borderRadius: radius, boxShadow: `0 0 0 1px ${accent}59, 0 0 40px ${accent}1f, 0 50px 90px rgba(0,0,0,0.55)`, ...frameStyle}} />
          <div style={{position: 'absolute', inset: 0, borderRadius: radius, overflow: 'hidden', background: bg}}>
            <div style={{position: 'absolute', left: tx, top: ty, width: srcW, height: srcH, transformOrigin: '0 0', transform: `scale(${s})`}}>
              {children?.(t)}
              {clicks.map(([at, x, y], i) => <ClickFx key={i} at={at} x={x} y={y} scale={1 / s} color={accent} />)}
            </div>
          </div>
          {tag && (
            <div style={{position: 'absolute', left: 18, top: 16, display: 'flex', alignItems: 'center', gap: 8, padding: '6px 12px', borderRadius: 999, background: 'rgba(8,9,12,0.78)',
              border: `1px solid ${accent}73`, fontSize: 16, color: accent}}>
              <span style={{width: 6, height: 6, borderRadius: 3, background: accent}} />{tag}
            </div>
          )}
        </div>
      </div>
      {outside && <div style={{position: 'absolute', inset: 0, opacity: 1 - pout}}>{outside(t, toScreen)}</div>}
    </>
  );
};

type DemoProps = Omit<WinProps, 'children' | 'clicks'> & {
  src: string;
  anchors: Anchor[];
  /** 录屏里的点击：录屏时间、录屏坐标（自动换算成成片时间） */
  clicks?: Click[];
  inside?: (t: number) => React.ReactNode;
};

/** 录屏演示：Window 里放分段变速的录屏 */
export const Demo: React.FC<DemoProps> = ({src, anchors, clicks = [], inside, show, ...rest}) => {
  const {fps} = useVideoConfig();
  const [s0, s1] = show;
  // 分段变速：每段一个 Sequence，录屏按这一段的速度放（速度为 0 的段用极慢速度，看起来就是定格）
  const segs: {f0: number; f1: number; v0: number; rate: number}[] = [];
  const A: Anchor[] = [[s0 - 0.5, anchors[0][1]], ...anchors, [s1 + 0.5, anchors[anchors.length - 1][1]]];
  for (let i = 1; i < A.length; i++) {
    const [f0, v0] = A[i - 1], [f1, v1] = A[i];
    if (f1 - f0 < 1 / fps) continue;
    segs.push({f0, f1, v0, rate: Math.max(0.002, (v1 - v0) / (f1 - f0))});
  }
  const filmClicks: Click[] = clicks.map(([v, x, y]) => [unmapTime(anchors, v), x, y]);
  return (
    <Window show={show} clicks={filmClicks} {...rest}>
      {(t) => (
        <>
          {segs.map((g, i) => (
            <Sequence key={i} from={Math.round(g.f0 * fps)} durationInFrames={Math.max(1, Math.round((g.f1 - g.f0) * fps))} layout="none">
              <OffthreadVideo muted src={staticFile(src)} trimBefore={Math.round(g.v0 * fps)} playbackRate={g.rate}
                style={{position: 'absolute', left: 0, top: 0, width: rest.srcW, height: rest.srcH}} />
            </Sequence>
          ))}
          {inside?.(t)}
        </>
      )}
    </Window>
  );
};

/** 点击：提前 0.35 秒出现一圈光晕，按下时缩一下、放出一圈波纹 */
const ClickFx: React.FC<{at: number; x: number; y: number; scale: number; color: string}> = ({at, x, y, scale, color}) => {
  const t = useNow();
  if (t < at - 0.4 || t > at + 0.9) return null;
  const halo = expoOut(prog(t, at - 0.4, 0.3)) * (1 - prog(t, at + 0.5, 0.4));
  const press = t >= at ? 1 - Math.abs(prog(t, at, 0.16) * 2 - 1) : 0;
  const rip = expoOut(prog(t, at, 0.7));
  const k = scale; // 光圈大小按屏幕像素算，不跟着推近一起变大
  return (
    <>
      <div style={{position: 'absolute', left: x - 34 * k, top: y - 34 * k, width: 68 * k, height: 68 * k, borderRadius: '50%',
        background: `radial-gradient(circle, ${color}52, ${color}00 70%)`, border: `${2 * k}px solid ${color}`,
        opacity: halo, transform: `scale(${1 - press * 0.25})`}} />
      {t >= at && <div style={{position: 'absolute', left: x - 34 * k, top: y - 34 * k, width: 68 * k, height: 68 * k, borderRadius: '50%',
        border: `${2 * k}px solid ${color}`, opacity: 1 - rip, transform: `scale(${1 + rip * 1.8})`}} />}
    </>
  );
};

/** 代码复刻界面里的光标：按关键帧移动（expo-in-out），按下时缩一下 */
export const Cursor: React.FC<{t: number; path: [number, number, number][]; presses?: number[]}> = ({t, path, presses = []}) => {
  let x = path[0][1], y = path[0][2];
  for (let i = 1; i < path.length; i++) {
    const [at, nx, ny] = path[i];
    if (t < at - 0.55) break;
    const p = expoInOut(prog(t, at - 0.55, 0.55));
    x = lerp(x, nx, p); y = lerp(y, ny, p);
  }
  const press = presses.reduce((m, a) => Math.max(m, t >= a ? 1 - Math.abs(prog(t, a, 0.16) * 2 - 1) : 0), 0);
  return (
    <svg width={30} height={36} viewBox="0 0 30 36" style={{position: 'absolute', left: x - 3, top: y - 2, transform: `scale(${1 - press * 0.15})`, transformOrigin: '3px 2px',
      filter: 'drop-shadow(0 2px 3px rgba(0,0,0,0.6))', zIndex: 50}}>
      <path d="M3 2 L3 28 L10 21 L15 33 L20 31 L15 19 L25 19 Z" fill="#fff" stroke="#111" strokeWidth={1.6} strokeLinejoin="round" />
    </svg>
  );
};
