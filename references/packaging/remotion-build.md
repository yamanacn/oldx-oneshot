# 用 Remotion 导出：工程、工具和技术约束

设计定下来之后读。这份文件只讲怎么落地和哪些地方不遵守会出错，不讲画面该长什么样——那是 [design.md](design.md) 和你自己的事。

## 目录

1. 起一个工程
2. 工程里有什么
3. 人物层
4. 不遵守就会出错的约束
5. 渲染
6. Node.js 装不上时的备用做法

## 1. 起一个工程

```bash
python scripts/packaging/timeline_edit.py --workdir <项目目录> --gap "after:下一个阶段:1.5" --gap "after:两段视频吧:20:hide" --tail 3   # 需要时
python scripts/packaging/remotion_new.py --workdir <项目目录> --size 1920x1080 --bgm 音乐.flac --bgm-offset 3.1
```

- `--size`：成片尺寸。竖屏人物做横屏片必须给；给过一次就记进 `film.json`（`lockSize`），以后重跑不会被改回人物视频的尺寸。
- `--fps`：成片帧率（例如 60）。给过一次就记进 `film.json`（`lockFps`）；不给时用人物视频的帧率（24）。以前手改 film.json 的帧率，重跑准备脚本就被打回 24。
- 背景音乐比成片短时自动按小节接长（开头原样、中段整小节循环、曲子原本的结尾对片尾），输出里 `bgm.looped` 说明怎么接的；节拍按接长后的音乐测。
- 手改过字幕又锁定（`captions.locked`）之后，如果逐词时间变了（timeline_edit.py 删插了空档），输出里 `captions_stale: true` 并在 stderr 提醒：字幕时间已经对不上。删掉锁、重跑生成，再把手改的地方重做一遍。
- `--bgm`、`--bgm-db`（默认 12：音乐比人声低 12 分贝；18 分贝实测偏小）、`--bgm-offset`（从音乐的第几秒开始，让音乐的高潮对上某句话）：写出 `public/bgm.wav`、`film.json` 的 `bgmGain` 和 `data/beats.json`。在 `Film.tsx` 里放 `<Html5Audio src={staticFile('bgm.wav')} volume={f => film.bgmGain * …}>`，插外部片段的段落再压低约 12 分贝、片尾淡出。
- `timeline_edit.py` 的产物在 `<项目目录>/package_src/`，`remotion_new.py --workdir` 会自动用它。
- 手改过 `captions.json` 后建一个空文件 `src/data/captions.locked`，以后重跑不会覆盖。

它做三件事：把模板（`assets/remotion-template`）复制到 `<项目目录>/package`；接上共用的依赖；调 `remotion_prepare.py` 放素材。已经存在的文件不会被覆盖，所以写好的画面是安全的；换了人物视频或配音后重跑它就能更新素材。

- **依赖只装一份**，在 `~/.digital-human/remotion-runtime`，每个工程用目录联接指过去（不然每条片子要再占约 900 MB）。没装时脚本会自动装上（连同 Node.js 和渲染用的浏览器），并在输出的 `installed` 里列出来；装不上以退出码 7 结束并说明原因。
- **不在一条龙流程里时**（用户自己拍的口播，只做包装）用 `--project <工程目录> --presenter 人物.mp4 --voice 原声.wav --words words.json`。
- **人物视频还没出来时**可以先用一张静态图配上原声做一段占位视频起工程，把画面先写出来；人物视频好了再重跑一次 `remotion_prepare.py` 换进去。
- `remotion_prepare.py` 放的东西：人物视频 → `public/presenter.mp4`；原声转成双声道 → `public/voice.wav`；逐词时间 → `src/data/words.json`；字幕 → `src/data/captions.json`（给了 `--copy 文案.txt` 就按原文做：一句一条、保留标点；没给才退回按识别结果分行）；尺寸、帧率、时长、音效音量 → `src/data/film.json`；字体 → `public/fonts/sub.ttf`（`--font` 指定用户给的字体）。它还会放几个合成的短音（`public/sfx/`），只是现成的原料，用不用、另做什么音色由设计决定。

字幕由 `captions_from_copy.py` 按原文案生成：一句一条，句中的标点、小数点、英文空格都保留，长句在逗号处分开，每条至少停留约 1 秒。一行放得下多少字用 `remotion_prepare.py --max-chars` 调（横屏默认 22，竖屏用 14 左右）。生成后把 `captions.json` 通读一遍，断得别扭的地方直接改这个文件。

真实素材（照片、截图、成片截帧）放到工程的 `public/assets/`。这条片子的设计说明写在工程根目录的 `STYLE.md`。

## 2. 工程里有什么

模板里只有工具，没有任何设计：没有预设的风格、容器、进场方式和特效。

| 文件 | 作用 |
|---|---|
| `src/Film.tsx` | 这条片子的画面。模板里是一张白纸，每条片子从头写；组件拆成几个文件、怎么组织由你定 |
| `src/lib/words.ts` | `T("几个字", 第几次)`：口播里说到这几个字的时刻（秒）；`TT(…)` 每个字各自的时刻（逐字出现的大标题用）。原样找不到时依次试 `data/aliases.json` 里的别名、模糊匹配（错字不超过三分之一），都不行才报错，并给出最接近的识别文字 |
| `src/lib/beats.ts` | 背景音乐的节拍：`snap(t)` 吸到最近的半拍、`bar(t)` 找小节第一拍、`beatInfo(t)` 第几拍和离上一拍多久 |
| `src/lib/gsap.ts` | `useTimeline((tl, s) => …)`：用 GSAP 排时间线，每帧 seek 到当前秒数，读补间后的数值 |
| `src/lib/gl.tsx` | `<Stage3D>` 一块 3D 画布；`<ShaderPlane>` 铺满的片元着色器（默认当背景，`overlay` 当遮罩）；`<PostFX>` 后期特效容器；`GLSL_NOISE` 噪声函数 |
| `src/lib/motion.ts` | `useNow()` 成片时间、`useU()` 尺寸单位、`rng(种子)` 固定种子随机数、`clamp` 和两条基本缓动 |
| `src/core/PresenterCard.tsx` | 人物卡：在命名站位之间移动、变形、滑出画面，竖屏人物进横屏、左右换位、角上小窗或圆头像都用它（见第 3 节） |
| `src/core/Presenter.tsx` | 简单版人物层：全屏，或缩小后放在右下角 |
| `tools/check-hooks.mjs` | 渲染前检查"先 return 再调 hook"（`render.cmd` 自动跑） |
| `src/core/Sfx.tsx` | 在某一秒响一声，整体响度已经压在人声下面 |
| `src/core/Subtitles.tsx` | 加载字体（`SubFont`），并带一个最朴素的一行白字字幕（底部居中、位置固定）。字幕长什么样是设计的一部分，可以自己写一个读 `data/captions.json`；不用它的字幕时也要 `import './core/Subtitles'` 来加载字体 |
| `src/core/ChapterBar.tsx` | 最朴素的底部分段时间轴：给它每段的名字和起始时间。样子同样由设计决定 |
| `src/core/gaps.ts` | 空档工具：`GAPS`（含片尾定格）、`guardGaps` 让讲者在空档里离场、`assertNoFrozen` 渲染时逐帧查（见第 3 节） |
| `src/core/Demo.tsx` | 演示窗口：`<Demo>` 录屏按锚点变速（同一成片时刻两个锚点 = 跳过加载白屏）、按关键帧推近点击处、点击光圈；`<Window>` 放代码复刻的界面；`<Cursor>` 画光标。颜色和外框由片子传 |
| `src/data/` | `words.json`、`captions.json`、`film.json`（含 `bgmGain`）、`beats.json`、`aliases.json` |
| `render.cmd`、`remotion.config.ts` | 验证过的渲染参数；渲染前先跑 hook 检查和类型检查 |

### 已经装好的库：技术栈不设限，目标是动画尽可能好

所有工程共用一份依赖（`~/.digital-human/remotion-runtime`），直接 `import`，不用再装、不用改 webpack：

| 要做什么 | 用什么 | 注意 |
|---|---|---|
| 3D 物件、场景、灯光、粒子 | `three`、`@react-three/fiber`、`@react-three/drei`（圆角盒、环境光、轮廓线、Float……）、`@remotion/three` | 放在 `<Stage3D>` 里；姿态、位置全部由 `useNow()` 算，不用 `useFrame` 累加，不用物理引擎逐帧积分 |
| 辉光、景深、色差、噪点、暗角 | `@react-three/postprocessing` + `postprocessing` | **一律包在 `<PostFX>` 里**；直接用 `<EffectComposer>` 时 3D 画布挂上后的第一帧是黑的（单帧静帧全黑、并行渲染每个页面的第一帧黑，实测） |
| 全屏着色器（极光、溶解、扫光、像素格、液体转场） | `<ShaderPlane>` + `GLSL_NOISE` | 当背景用默认值（不透明、最先画）；当盖在上面的遮罩用 `overlay`。透明材质会画在不透明物体之后，当背景用会把 3D 物件盖住 |
| 复杂的编排、缓动、stagger、路径动画 | `gsap`（`useTimeline`） | 时间线永远暂停、每帧 seek；不要 `tl.to(ref.current)` 直接改 DOM，不用 `gsap.ticker`、`delayedCall` |
| 形状变形 | `flubber`、`@remotion/paths`、`@remotion/shapes` | 路径插值的函数用 `useMemo` 建一次 |
| 噪声驱动的晃动、漂移 | `simplex-noise`、`@remotion/noise` | 种子固定 |
| 图表、比例尺、曲线 | `d3` | 只用它算数，DOM 由 React 画 |
| 现成的 Lottie 动画 | `@remotion/lottie` + `lottie-web` | 用 `<Lottie animationData>`，它自己按帧号定位 |
| 大量 2D 精灵、滤镜 | `pixi.js` | 每帧手动 `app.render()`，不用它的 ticker |
| 场景之间的转场 | `@remotion/transitions` | 注意 `<TransitionSeries>` 里帧号会重新数，用到 `useNow()` 的组件不要放进去 |
| 运动模糊 | `@remotion/motion-blur` | 很费渲染时间，只给快速移动的大元素用 |

需要上面没有的能力就现装（`npm i` 装进 `~/.digital-human/remotion-runtime`，并把版本写进技能模板的 `package.json`，不然下次重装会丢），只要满足第 4 节。

## 3. 人物层

### 人物卡（`core/PresenterCard`）：站位由设计定

```tsx
const POSES = {
  R:    {x: 1187, y: 104, w: 583, h: 778, r: 0},    // 右侧大卡（竖屏人物裁成 3:4）
  C:    {x: 668,  y: 104, w: 583, h: 778, r: 0},    // 居中打照面
  MINI: {x: 1666, y: 730, w: 190, h: 190, r: 22},   // 右下角圆角小方窗（r = 宽的一半就是圆头像）
  OFF:  {x: 2040, y: 104, w: 583, h: 778, r: 0},    // 出画也是一个站位
};
const KEYS = [[0, 'R'], [T('所以我') - 0.25, 'MINI'], [T('牛吹完了') - 0.2, 'C'], …];
<PresenterCard poses={POSES} keys={KEYS} focus={[0.5, 0.25]} decorate={(box, t) => ({front: <角标和标签/>})} />
logCardSpec(POSES, KEYS, film.duration);   // Film.tsx 末尾
```

- 每个 key 从它的时刻开始，用 0.65 秒平滑地换到新站位（位置、大小、圆角一起变）。
- 换边（左 ⇄ 右）尽量放在满屏动画盖住画面的那一刻，观众看不到人物横穿画面。
- 站位是像素坐标，按 `--size` 定下的成片尺寸排；底边留给字幕和时间轴。
- 没有特别要求时，人物以右侧为主，缩小时在右下角。

### 简单版（`core/Presenter`）：全屏 ⇄ 右下角小窗

人物视频和成片同样画幅、只需要"全屏讲 / 缩到角上让内容"时用它：

```tsx
<Presenter runs={[[t1, t2], [t3, t4]]} hide={[[t5, t6]]} corner={{w: 0.3, h: 0.3, radius: 0.2}} decorate={(box) => ({back: …, front: …})} />
```

- `runs`：这些区间里人物缩到右下角，其余时间全屏。相邻的内容段之间只隔一两秒时把区间接起来，不要让人物来回缩放。
- `hide`：这些区间里不显示人物（内容需要整个画面，或遮盖人物视频的异常）。人物在角上时是整张小窗向右滑出画面、再滑回来；全屏时是很短的淡出淡入。
- `corner`：缩小后的宽、高、边距（画面短边的比例）、圆角（0 直角到 1 最圆）、过渡时长、头肩中心 `focus`、小窗里再放大多少。先看一眼人物视频的抽帧，确认头肩在哪再填 `focus`。
- `decorate`：给小窗加边框、投影、装饰，拿到的是小窗这一刻的位置和大小。

简单版缩小后的位置固定在右下角；要放别处就用人物卡。大小、形状、边框、什么时候缩由设计决定。右下角的小窗、它扫过的路径，都要和主画面的内容、字幕一起排，不要互相盖住。画面底部留给字幕和时间轴（见 design.md）：把小窗的下边距 `marginY` 设到让它的底边落在这条带子上面（字幕加时间轴占底部 20% 时，`marginY` 约 0.2）。

**人物可以整场不出现，但消失和再出现要有来路。** 有的场面让动画占满整个画面更好，这时把人物隐藏掉是允许的。要注意的是进和出的方式——人物在右下角原地淡出、过一会儿又原地淡入，像被开关拨了一下，很突兀。三条规矩：

- **时机优先放在换场处。** 人物的消失和出现尽量落在两场之间，观众看到的是“这一场有人、下一场没人”，而不是一场中间多出或少掉一张卡片。
- **离开得短，滑出、滑入。** 像别的贴纸一样从画面外进来、往画面外出去（`hide` 已经是这样做的）。脸上不做回弹，只用平滑的缓入缓出。
- **离开得久，回来时先全屏打个照面，再缩到角上。** 挑一句对着观众说的话让人物全屏回来，停一两秒再缩小：让 `hide` 的区间结束在下一个 `runs` 区间开始之前一两秒。

必须在一场中间进出时（比如遮盖人物视频里出问题的几秒），用滑出、滑入，不要原地淡入淡出。

**插了空档就必须用 `core/gaps.ts`。** timeline_edit.py 插的空档里，人物视频补的是定格帧；讲者在空档里露面，观众看到的是"人卡了一下又动起来"（实测用户看 5 秒样段就指出来了）。

```tsx
import {assertNoFrozen, guardGaps} from './core/gaps';
const KEYS = guardGaps(myKeys, POSES, {off: 'OFF', dur: 0.75, lead: 0.3});   // 空档前离场、空档结束那一刻回到原站位
assertNoFrozen(POSES, KEYS, {dur: 0.75, covered: (t, g) => …});            // 满屏转场盖住画面的时刻返回 true
```

- 满屏转场要**比空档早约 0.3 秒**开始盖住画面（讲者还在动的时候就被盖住），揭开时**越过空档结束约 0.5 秒**，和讲者回场重叠，中间不留空场。
- 讲者离场时停在**刚好出画**的位置（例如 x = 画面宽 + 40），一动就进画；放得太远，回场时会空等半秒。
- 片尾定格（`--tail`）也算一个空档，片尾定版卡同样要提前盖住。
- 片头空档里也一样：开场动画结束时讲者再滑进来（例如粒子聚成讲者卡的轮廓，讲者滑进轮廓落定后轮廓才散）。

**推近、拉远这类运镜在这里做，不交给视频模型。** 人物视频是按“镜头固定、人物大小不变”生成的（提示词里写前倾、推近，模型会把背景一起弄坏）。想在强调处推近一点、在换段时缓缓拉开，就在成片里对人物层做缩放：把 `<Presenter>` 包在一个按时间算缩放的容器里，缩放中心放在脸上，幅度小（1.03–1.12），用平滑的缓入缓出，只在人物全屏时做。

这个组件替你保证了几件容易出错的事，自己另写人物层时也要保证：

- **同一段视频、同一个时钟。** 全屏和小窗是同一个人物视频的两种展示，不是重新播放、不是静态头像。成片第 n 帧永远取人物视频的第 n 帧，和缩放、遮罩、是否隐藏无关；隐藏后再出现，人物仍然对着当前的口播。
- **只有一条人声。** 人物视频静音，声音只来自 `voice.wav`，否则会叠音。
- **等比缩放。** 宽高用同一个缩放系数，改变的是取景，不挤压人脸；要正圆就让宽高相等。
- **人物的缩放用平滑的缓入缓出**，脸上不做回弹和拉伸。

## 4. 不遵守就会出错的约束

这些和审美无关，是渲染方式和素材决定的。

- **画面只由当前帧决定。** 每一帧是单独渲的，而且多个页面各渲各的帧。用 `useNow()` 或 `useCurrentFrame()` 算出这一帧的位置、透明度、形状、进度，写进样式；不用 CSS 的 transition、animation，不用定时器，不依赖上一帧留下的状态。随机的东西用 `rng(种子)`。物理模拟、粒子这类本来要逐帧累积的东西，写成“给定时间直接算出状态”的形式。这样只渲一段、只出一帧，结果都和全片一致。
- **时间从逐词时间取。** 画面上的事件什么时候发生，用 `T("口播里的几个字")` 取，不凭感觉估秒数。识别文字有同音错字（人名、英文、数字），`T()` 里要写识别出来的那几个字；写完跑一遍找不到的词会直接报错。
- **`useNow()` 是整条片子的时间。** 用到它的组件不要包进 `<Sequence from=…>`，那里面帧号从 0 重新数。
- **原声原样放。** `<Html5Audio src={staticFile('voice.wav')} />` 放一条，不改音量、不裁剪。原声和音效都存成双声道——单声道文件进了 Remotion 会被压低 3 分贝（`remotion_prepare.py` 已经这样处理）。
- **音效的响度不在各处单独调大。** 统一由 `film.json` 的 `sfxVolume` 控制（默认让音效峰值比人声低 15 分贝）。新做的音效峰值归一到 1。
- **图片用 Remotion 的 `<Img>`，视频用 `<OffthreadVideo>`**，它们会等素材加载好再出这一帧；字体用 `delayRender` 挡到加载完（`core/Subtitles.tsx` 已经这样加载了字幕字体）。用系统里没有的字体时放进 `public/fonts/` 自己加载，不要指望渲染的机器装了它。
- **大面积的元素不要套 SVG 滤镜，也少用大面积的模糊。** 整块跑滤镜会把每一帧拖慢好几倍；只有外形不规则的小元素才用滤镜描边。
- **尺寸乘 `useU()`**，位置用画面宽高的比例，横竖屏和不同分辨率才不会错位。
- **工程路径不要太长。** Windows 上路径超过 260 个字符时渲染用的浏览器会启动失败（报找不到 `chrome-headless-shell.exe`）。
- **`Film.tsx` 末尾的 `logSpec(RUNS, 小窗参数)`（或用人物卡时的 `logCardSpec(POSES, KEYS, 时长)`）不要删**，质检脚本靠它知道人物每段在哪。
- **hook 一律写在组件最前面。** `useNow()`、`useVideoConfig()`、`useMemo()` 这类调用放在任何 `if (…) return null` 之前。写反了平时看不出来，渲到组件从"不画"变成"要画"的那一帧会报 React #310，整条渲染失败（实测两次）。`render.cmd` 渲染前会用 `tools/check-hooks.mjs` 查一遍。
- **后期特效包在 `<PostFX>` 里，背景着色器用不透明的 `<ShaderPlane>`。** 见第 2 节的表。

## 5. 渲染

先出静帧看版式，再渲一小段看动效和速度，最后渲全片：

```bash
npx remotion still src/index.ts Main out/still.png --frame=480
npx remotion render src/index.ts Main out/sample.mp4 --frames=276-395 --concurrency=12
render.cmd
```

- 写完先跑一遍 `npx tsc --noEmit -p .`，类型错误比渲染报错好找。
- `render.cmd` 要在 Windows 的命令行里跑（PowerShell 里用 `cmd /c ".\render.cmd"`）。它用的是验证过的一组参数：浏览器用显卡渲染，多个页面同时渲（逻辑核心数的一半，最多 16），中间帧用 JPEG 95，有英伟达显卡时用显卡编码，失败自动退回处理器编码；渲完清掉成片文件里的元数据。参考速度：1376×768、24 帧、4 分半的片子，全片约 2 分钟。
- **慢在哪：** 每一帧是无头浏览器里一次排版加截图，再加上人物视频、录屏逐帧解码，主要吃 CPU；显卡只负责浏览器绘制（ANGLE）和最后的编码（NVENC），渲染时显卡占用 10% 左右是正常的，换更好的显卡不会明显变快。60 帧比 30 帧多一倍时间（实测 1080p60、5 分钟约 20 分钟）。改版阶段出样段用 30 帧，定稿再渲 60 帧。
- 一次要出几十张检查静帧时，写个脚本只打包一次、循环 `renderStill`（比每张跑一次 `npx remotion still` 快得多），再拼成联系表看。
- 改了一处不用重渲全片来确认，出那几帧的静帧看；一次要看很多个时间点时，写个小脚本循环出静帧，再拼成一张图看。
- 渲完照常检查：`qc_scan.py`（黑帧、卡帧、静音）、`av_check.py`（原声对齐、小窗运动），抽帧看全片。`av_check.py` 要的 spec 用 `remotion_spec.py <工程目录>` 取，写在 `<工程目录>/spec.json`。加了音效后原声比对的相关系数会略低于 1，看偏移是否为 0。

## 6. Node.js 装不上时的备用做法

用 `pip_render.py` 合成人物和小窗，`overlay_kit.py` 叠文字卡、框选、图片，`subs_from_words.py` 出字幕。能做的只有这些基本件，做不了成套的图形动画。Node.js 缺了会自动安装，只有装不上（比如这台机器不允许安装软件）时才走这条路，并告诉用户效果会简单很多、原因是什么。
