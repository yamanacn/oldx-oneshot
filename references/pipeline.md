# 项目目录和三个阶段之间的交接

三个阶段不直接互相调用，只通过一个共用的项目目录交接：前一阶段把产物写进去，后一阶段从里面读。所以任何一步断了都能接着做，用户也可以只从中间某一阶段进来。

## 项目目录

每份文案对应一个项目目录，默认在 `~/.digital-human/projects/<日期-哈希前8位>/`（Windows 是 `%USERPROFILE%\.digital-human\projects\...`；环境变量 `DH_WORKSPACE` 可以改根目录）。下文称 `workdir`。文案去掉空白后相同就算同一个项目，会直接命中已有的目录。

```
workdir/
├── copy.txt, meta.json          文案原文（一个字不改）和它的哈希、字数
├── voice.json, paragraphs.json  用的哪个音色；配音的拆段
├── emotions.json                情绪标签规则和整体描述（要用标签时才有，格式见 references/voice/emotions.example.json）
├── cache/                       各段配音的缓存
├── out/
│   ├── merged.wav               ★ 整条配音
│   ├── words.json               ★ 逐词时间和停顿
│   └── timeline.json            每段配音的起止
├── out_prev/<时间>/             重新合成出了不同结果时，上一版自动存在这里（可以对着听）
├── avatar/
│   ├── image.png                处理过的口播图片
│   ├── plan.json, prompt_src.json, prompts/   分段和提示词
│   ├── clips/, result.json      各段原始视频、任务号和费用
│   ├── final_avatar.mp4         ★ 人物视频（声音就是 merged.wav）
│   └── qa.json, anomalies.json  ★ 质检结果和需要遮盖的异常时间段
├── package/                     包装工程（Remotion）
│   ├── STYLE.md                 这条片子的风格说明
│   ├── src/Film.tsx             画面编排
│   └── out/main.mp4             渲出来的成片
└── final/                       ★ 交付给用户的成片（质检通过后从 package/out 复制过来，起一个看得懂的文件名）
```

根目录 `~/.digital-human/` 下还有：`latest.json`（最近一个项目）、`voice.json`（用户的默认音色，新项目不给样本就沿用）、`voice_history.json`（复刻过的所有音色，可用 `clone_voice.py --list`、`--use` 换回）、`remotion-runtime/`（包装用的依赖，所有项目共用一份）。两个密钥分别在 `~/.bailian/api_key` 和 RunningHub 自己的配置里，由各阶段的检查脚本去找。

## 交接物

| 从 | 到 | 文件 | 用来做什么 |
|---|---|---|---|
| 配音 | 数字人 | `out/merged.wav` | 分段后逐段送去驱动人物；成片的声音也用它 |
| 配音 | 数字人 | `out/words.json` 的 `pauses` | 只能在停顿处切段 |
| 配音 | 数字人 | `out/words.json` 的 `words` | 动作编排按词对时间 |
| 配音 | 包装 | `out/words.json`、`copy.txt` | 画面元素跟着词出现；字幕按原文纠正识别错字 |
| 数字人 | 包装 | `avatar/final_avatar.mp4` | 人物层 |
| 数字人 | 包装 | `avatar/anomalies.json` | 这几秒画面有异常（变色、嘴不动），包装时把人物缩小或移开、用内容盖过去，不必花钱重新生成 |
| 数字人 | 包装 | `avatar/image.png` | 现成的真实素材（“就这一张照片”） |

带 ★ 的文件是判断“这一阶段做完没有”的依据，`scripts/status.py` 就是按它们判断的。

## 看进度、接着做

```
python scripts/status.py            # 最近一个项目走到哪了、下一步是什么
python scripts/status.py --list     # 所有项目
```

每个阶段内部也都能断点续做：配音按“文字+音色+指令”缓存，只补变动或失败的段；数字人的任务号一提交就落盘，重跑同一条命令会续上云端的任务，不重复计费；包装的工程是普通文件，改了哪里重渲哪里。所以**中断后先跑 `status.py`，不要从头来**。

## 重做某一阶段时，后面的要不要跟着重做

| 改了什么 | 后面要重做什么 |
|---|---|
| 文案 | 全部（这是一个新项目，目录也会是新的） |
| 音色、配音 | 数字人（嘴型和时长都变了）、包装（逐词时间变了，重新放素材，画面编排里用 `T("…")` 取的时间会自动跟着变，但要重看一遍静帧） |
| 口播图片、某几段人物视频 | 包装只需重新放素材、重渲；编排不用改 |
| 风格、版式、画面内容 | 只重做包装 |

## 用户从中间进来

| 用户手里有什么 | 从哪开始 |
|---|---|
| 只有文案 | 第一阶段 |
| 文案加自己录好的配音 | 没有逐词时间就没法切段和对画面：先建项目（`scripts/voice/project.py init`），把音频放到 `out/merged.wav`，按 `references/voice/guide.md` 第 5 步做逐词对齐，再进第二阶段 |
| 自己拍的口播视频（真人出镜），只想做包装 | 直接第三阶段：用 `remotion_new.py --project` 指定工程目录、人物视频、原声和逐词时间；逐词时间用第一阶段的识别脚本从原声得到 |
| 已有项目，想换个风格再做一版 | 第三阶段。把原来的 `package/` 改个名字留着，再起一个新的；从设计说明重新开始，不能只换颜色和边框 |
