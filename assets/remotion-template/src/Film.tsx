// 这条片子的画面。每条片子从这张白纸写起：没有预设的风格、容器、进场方式和版式，全部按这条片子的内容和用户要的风格现设计、现写。
// 可以把组件拆到别的文件里，文件怎么组织也由你定。
//
// 手边有的工具（都和设计无关，用不用随你）：
//   T('口播里的几个字', 第几次)   lib/words     这几个字说出口的时刻（秒）。找不到会直接报错，不会悄悄错位
//   useNow() / useU() / rng()     lib/motion    成片时间、尺寸单位、固定种子的随机数
//   <Presenter runs hide corner decorate>  core/Presenter  人物层：全屏，或缩小后放在右下角；保证人物画面一直对着口播
//   <Sfx kind at>                 core/Sfx      在某一秒响一声，响度已经压在人声下面
//   <ChapterBar chapters total>   core/ChapterBar 底部的分段时间轴（最朴素的一版）
//   data/captions.json、data/words.json、data/film.json   字幕、逐词时间、尺寸帧率时长
//
// 两样每条片子都要有的框架（细则见 references/packaging/design.md）：
//   底部字幕——带标点的原文、一句一条、位置固定在底部居中；分段时间轴——全片分成带编号和名字的几段，在字幕下方。
//   画面底部约 15–20% 留给它们：人物小窗的下边距要设大（marginY 约 0.2），道具不伸进来。
//
// 不能动的只有这几条（原因见技能的 references/packaging/remotion-build.md）：
//   画面只由当前帧决定；时间从逐词时间取；原声原样放、只放一条；人物缩小后在右下角；末尾的 logSpec 不要删。
import React from 'react';
import {AbsoluteFill, Html5Audio, staticFile} from 'remotion';
import {Chapter, ChapterBar} from './core/ChapterBar';
import {Presenter, Span, logSpec} from './core/Presenter';
import {Subtitles} from './core/Subtitles';
import film from './data/film.json';

// 人物缩到右下角的区间，例如：[[T('它一共') - 0.3, T('第二件') - 0.4]]
const RUNS: Span[] = [];
// 全片的分段：按内容结构分成 5–10 段，名字用口播原话或中性标签，例如：[{name: '开场', at: 0}, {name: '第一步', at: T('第一步') - 0.3}]
const CHAPTERS: Chapter[] = [];

export const Film: React.FC = () => (
  <AbsoluteFill style={{background: '#000'}}>
    <Presenter runs={RUNS} />
    {/* 字幕长什么样也是设计的一部分：这个组件只是最朴素的一行白字，可以换成自己写的（数据在 data/captions.json） */}
    <Subtitles />
    <ChapterBar chapters={CHAPTERS} total={film.duration} />
    <Html5Audio src={staticFile('voice.wav')} />
  </AbsoluteFill>
);

logSpec(RUNS);
