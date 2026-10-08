import React from 'react';
import {useVideoConfig} from 'remotion';
import {clamp, useNow, useU} from '../lib/motion';

export type Chapter = {
  /** 这一段的名字：用口播原话或“开场、收尾”这类中性标签 */
  name: string;
  /** 这一段从第几秒开始（用 T('…') 取） */
  at: number;
};

type Props = {
  chapters: Chapter[];
  /** 全片时长（秒），取 data/film.json 的 duration */
  total: number;
  /** 离画面底边多远（画面高度的比例） */
  bottom?: number;
  /** 文字和已走过部分的颜色、还没走到部分的颜色。压在人物画面上时换成浅色 */
  color?: string;
  track?: string;
  size?: number;
  font?: string;
};

/**
 * 底部的分段时间轴：全片分成几段，每段带编号和名字，宽度按时长；走过的填满，当前这一段显示段内进度。
 *
 * 这是最朴素的一版，只保证“有、读得出、位置对”。它长什么样（字体、颜色、粗细、当前段怎么强调）是这条片子设计的一部分，
 * 可以改这个文件，也可以照着它另写一个；有参考片时照参考片的样子做。
 */
export const ChapterBar: React.FC<Props> = ({chapters, total, bottom = 0.03, color = '#fff', track = 'rgba(255,255,255,.35)', size = 21, font = 'SubFont, "Microsoft YaHei", sans-serif'}) => {
  const {width: W, height: H} = useVideoConfig();
  const u = useU();
  const t = useNow();
  const pad = (n: number) => String(n).padStart(2, '0');
  return (
    <div style={{position: 'absolute', left: 0.028 * W, right: 0.028 * W, bottom: bottom * H, height: (size * 1.3 + 14) * u}}>
      {chapters.map((c, i) => {
        const end = i + 1 < chapters.length ? chapters[i + 1].at : total;
        const cur = t >= c.at && t < end;
        const p = t >= end ? 1 : cur ? clamp((t - c.at) / (end - c.at)) : 0;
        return (
          <div key={i} style={{position: 'absolute', left: `${(c.at / total) * 100}%`, width: `calc(${((end - c.at) / total) * 100}% - ${6 * u}px)`, top: 0, bottom: 0}}>
            <div style={{whiteSpace: 'pre', overflow: 'hidden', fontFamily: font, fontSize: size * u, lineHeight: 1.3, fontWeight: cur ? 800 : 600, color, opacity: cur ? 1 : p ? 0.85 : 0.6}}>
              {pad(i)} {c.name}
            </div>
            <div style={{position: 'absolute', left: 0, right: 0, bottom: 0, height: (cur ? 7 : 5) * u, borderRadius: 4 * u, background: track, overflow: 'hidden'}}>
              <div style={{width: `${p * 100}%`, height: '100%', background: color}} />
            </div>
          </div>
        );
      })}
    </div>
  );
};
