// 用 GSAP 排动画，但让画面只由时间决定：时间线建好后永远暂停，每一帧 seek 到当前秒数，再读出补间后的数值。
// 这样可以用 GSAP 的缓动、stagger、时间线嵌套、MotionPath 等全部能力，同时保证只渲一段、多页面并行渲染时结果一致。
//
//   const v = useTimeline((tl, s) => {          // s 是一个普通对象，补间作用在它的字段上
//     s.x = 0; s.o = 0;
//     tl.to(s, {x: 400, o: 1, duration: 0.6, ease: 'back.out(1.6)'}, T('关键字'));
//   });
//   <div style={{transform: `translateX(${v.x}px)`, opacity: v.o}} />
//
// 不要在时间线里直接改 DOM（不要用 tl.to(ref.current, …)），也不要用 gsap.ticker / delayedCall —— 那些依赖真实时间。
import {gsap} from 'gsap';
import {useMemo} from 'react';
import {useNow} from './motion';

export {gsap};

export const useTimeline = <S extends Record<string, number>>(build: (tl: gsap.core.Timeline, s: S) => void, deps: unknown[] = []): S => {
  const t = useNow();
  const {tl, s} = useMemo(() => {
    const s = {} as S;
    const tl = gsap.timeline({paused: true});
    build(tl, s);
    return {tl, s};
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  tl.seek(t, false);
  return {...s};
};
