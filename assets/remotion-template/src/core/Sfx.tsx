import React from 'react';
import {Html5Audio, Sequence, staticFile, useVideoConfig} from 'remotion';
import film from '../data/film.json';

/**
 * 在成片第 at 秒响一声。kind 是 public/sfx 下的文件名（不带 .wav）。
 * 用不用音效、用什么音色、配在哪，是这条片子的设计；需要的音色用代码现合成（峰值归一到 1、双声道）放进 public/sfx。
 * 这里只管一件和设计无关的事：响度。整体音量取 film.json 的 sfxVolume（remotion_prepare.py 按原声算出，默认让音效比人声低 15 分贝），
 * gain 是这一声相对别的音效的大小，不要用它把音效推到盖过人声。
 */
export const Sfx: React.FC<{kind: string; at: number; gain?: number; seconds?: number}> = ({kind, at, gain = 1, seconds = 0.6}) => {
  const {fps} = useVideoConfig();
  return (
    <Sequence name={`音效 ${kind}`} from={Math.max(0, Math.round(at * fps))} durationInFrames={Math.ceil(seconds * fps)} layout="none">
      <Html5Audio src={staticFile(`sfx/${kind}.wav`)} volume={film.sfxVolume * gain} />
    </Sequence>
  );
};
