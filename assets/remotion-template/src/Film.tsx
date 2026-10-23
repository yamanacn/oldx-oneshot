// 这条片子的画面。每条片子从这张白纸写起：没有预设的风格、容器、进场方式和版式，全部按这条片子的内容和用户要的风格现设计、现写。
// 可以把组件拆到别的文件里，文件怎么组织也由你定。目标只有一个：动画做到尽可能好。
//
// 手边有的工具（都和设计无关，用不用随你）：
//   T('口播里的几个字', 第几次) / TT(…)   lib/words   说到这几个字的时刻；TT 给每个字各自的时刻（逐字出现的大标题用）。识别错字会模糊匹配，可在 data/aliases.json 加别名
//   snap(t) / bar(t) / beatInfo(t)       lib/beats   背景音乐的节拍（remotion_prepare.py --bgm 测出来的）：吸半拍、找小节第一拍、做节拍脉冲
//   useTimeline((tl, s) => …)            lib/gsap    用 GSAP 排时间线（缓动、stagger、嵌套），每帧 seek 到当前秒数，结果只由时间决定
//   <Stage3D> / <ShaderPlane> / GLSL_NOISE lib/gl    three.js 场景、全屏片元着色器、噪声函数
//   useNow() / useU() / rng()            lib/motion  成片时间、尺寸单位、固定种子的随机数
//   <PresenterCard poses keys …>         core/PresenterCard  人物卡：在命名站位之间移动、变形、滑出画面（竖屏人物进横屏、左右换位、角上小窗都用它）
//   <Presenter runs hide corner …>       core/Presenter      只需要"全屏 ⇄ 右下角小窗"时用的简单版
//   <Sfx kind at> / <ChapterBar …> / <Subtitles>  core/      音效、最朴素的分段时间轴、字幕
//   guardGaps / assertNoFrozen / GAPS    core/gaps   空档里人物是定格的：自动让讲者在空档里离场、空档一结束再回来；渲染时逐帧查，露面就报错（插了空档就必须用）
//   <Demo> / <Window> / <Cursor>         core/Demo   演示窗口：录屏按口播变速（可跳过加载）、点击推近、光标光圈；代码复刻的界面也放进 Window
//   data/captions.json、words.json、film.json、beats.json   字幕、逐词时间、尺寸帧率时长和音乐音量、节拍
//
// 已经装好、直接 import 的库（所有工程共用）：
//   three、@react-three/fiber、@react-three/drei、@react-three/postprocessing + postprocessing（辉光、景深、色差、噪点）、@remotion/three
//   gsap、flubber（形状变形）、simplex-noise、@remotion/noise、d3（图表、路径、比例尺）、@remotion/paths、@remotion/shapes
//   @remotion/transitions（场景转场）、@remotion/motion-blur、@remotion/lottie + lottie-web（Lottie 动画）、pixi.js（大量 2D 精灵/滤镜）
//   用法和约束见技能的 references/packaging/remotion-build.md 第 2 节。
//
// 两样每条片子都要有的框架（细则见 references/packaging/design.md）：
//   底部字幕——带标点的原文、一句一条、位置固定在底部居中；分段时间轴——全片分成带编号和名字的几段。
//   画面底部约 15–20% 留给它们：人物卡和道具都不伸进来。
//
// 不能动的只有这几条（原因见 references/packaging/remotion-build.md）：
//   画面只由当前帧决定；时间从逐词时间取；原声原样放、只放一条；hook 写在组件最前面（render.cmd 会先检查）；末尾的 logSpec / logCardSpec 不要删。
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
