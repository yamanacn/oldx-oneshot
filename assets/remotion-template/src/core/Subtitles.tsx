import React from 'react';
import {continueRender, delayRender, staticFile, useVideoConfig} from 'remotion';
import captions from '../data/captions.json';
import {useNow, useU} from '../lib/motion';

// 字幕字体从文件加载，不依赖本机装了什么
const handle = delayRender('字幕字体');
new FontFace('SubFont', `url(${staticFile('fonts/sub.ttf')})`, {weight: '100 900'})
  .load()
  .then((f) => {
    document.fonts.add(f);
    continueRender(handle);
  });

type Props = {
  /** 中心位置（画面宽高的比例）。固定在底部居中、时间轴的正上方，全片不动；人物小窗在它上面，不让字幕去躲小窗 */
  x?: number;
  y?: number;
  size?: number;
  color?: string;
  stroke?: string;
};

/** 字幕：一次一行，白字深色描边。内容来自 data/captions.json（带标点的原文，一句一条）。 */
export const Subtitles: React.FC<Props> = ({x = 0.5, y = 0.85, size = 50, color = '#fff', stroke = '#222'}) => {
  const {width: W, height: H} = useVideoConfig();
  const u = useU();
  const ms = useNow() * 1000;
  const c = captions.find((s) => ms >= s.startMs && ms < s.endMs);
  if (!c) return null;
  return (
    <div
      style={{
        position: 'absolute', left: x * W, top: y * H, transform: 'translate(-50%, -50%)', whiteSpace: 'pre',
        fontFamily: 'SubFont, "Microsoft YaHei", sans-serif', fontWeight: 700, fontSize: size * u, lineHeight: 1.5, color,
        WebkitTextStroke: `${0.28 * size * u}px ${stroke}`, paintOrder: 'stroke fill', strokeLinejoin: 'round',
      }}
    >
      {c.text}
    </div>
  );
};
