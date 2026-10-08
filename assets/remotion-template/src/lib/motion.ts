// 几个和设计无关的小工具：当前时间、尺寸单位、固定种子的随机数、夹值和两条最基本的缓动。
// 动效怎么动由每条片子自己写——需要回弹、弹簧、路径、噪声，就在这条片子的代码里现写，不在这里攒“默认动效”。
import {useCurrentFrame, useVideoConfig} from 'remotion';

export const clamp = (x: number, a = 0, b = 1) => Math.min(b, Math.max(a, x));
export const easeOut = (x: number) => 1 - Math.pow(1 - clamp(x), 3);
export const easeInOut = (x: number) => {
  x = clamp(x);
  return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2;
};

/** 固定种子的随机数：同一个种子每次渲染得到同一串数，这样只渲一段和渲全片结果一致 */
export const rng = (seed: number) => {
  let a = seed >>> 0;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
};

/** 成片时间（秒）。要在整条片子的时间轴上用，所以不要把用到它的组件包进 <Sequence from=…>（那里面的帧号会从 0 重新数） */
export const useNow = () => useCurrentFrame() / useVideoConfig().fps;

/** 尺寸单位：按 1080 短边设计的 1 像素，在当前画面里是多少像素。尺寸都乘它，横竖屏和不同分辨率共用一套排版 */
export const useU = () => {
  const {width, height} = useVideoConfig();
  return Math.min(width, height) / 1080;
};
