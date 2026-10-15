# 工具总表

配音、数字人两个阶段所有脚本的用法、输入输出和约定（包装阶段的脚本在 `scripts/packaging/`，用法见 `references/packaging/` 下的说明和各脚本文件头）。给要直接调用这些脚本的 agent 看；按流程做一条片子时，照 `references/voice/guide.md` 和 `references/avatar/guide.md` 走，用到哪个工具再回来查。

## 一、通用约定

**调用方式**：在技能目录下运行 `python scripts/...`，或者给脚本的绝对路径。需要 Python 3.10 以上；依赖缺了先跑 `scripts/setup_env.py`。所有输出都是 UTF-8。

**项目目录**：一律用 `--workdir <项目目录>`（以前的 `--dir`、`contact_sheet.py` 把视频路径写在最前面、`--dry-run` 这些旧写法已经删掉）。项目目录怎么来、里面有什么，见 `references/pipeline.md`。

**机读结果 `--json`**：任何一个能单独运行的脚本加上 `--json`，照常打印给人看的中文，**最后一行**额外输出一行 JSON。只读最后一行就能拿到结果：

```json
{"script": "synth.py", "ok": true, "exit": 0, "exit_name": "ok", "error": null, "merged": "…/out/merged.wav", "total_seconds": 120.58}
```

- 每个结果都有的字段：`script`、`ok`（是否成功）、`exit`（退出码）、`exit_name`、`error`（失败时的一句话说明，成功时为 `null`）、**`next`（下一步要运行的命令或要做的事，照着做，不用自己判断）**。
- 其余字段每个工具不同，见第三节各工具的「机读结果」。
- 要用户看的东西（裁剪预览、嘴部逐秒图、总览图）会以文件路径给出，调用方要自己打开看。

**退出码**（全技能统一）：

| 退出码 | `exit_name` | 意思 | 调用方怎么做 |
|---|---|---|---|
| 0 | `ok` | 成功 | 往下走 |
| 1 | `error` | 一般错误、校验没通过、缺前一步的产物 | 看 `error` 和打印的说明，补上前一步或改输入 |
| 2 | `usage` | 参数写错、给的文件不存在 | 改命令 |
| 3 | `no_key` | 本机没有密钥 | 按对应说明里的原话向用户要，用户粘贴后保存 |
| 4 | `key_rejected` | 密钥被拒绝 | 让用户重新获取 |
| 5 | `need_user` | 需要用户先提供东西或做决定（会员档位、人声样本……） | 问用户，不要自己替他定 |
| 6 | `file_locked` | 输出文件被别的程序占用 | 请用户关掉播放器，重跑同一条命令 |
| 7 | `deps` | 依赖没装上 | 把 `setup_env.py` 给的 `tell_user` 转告用户 |
| 8 | `remote_failed` | 云端任务或接口调用失败 | 看失败原因；重跑同一条命令只补失败的部分 |

**联网和花钱：一律默认只预览，加 `--yes` 才执行。** 预览不联网、不要密钥、不写结果文件，机读结果里 `preview: true`。

| 脚本 | 花钱的动作 | 预览给出什么 |
|---|---|---|
| `voice/clone_voice.py` | 上传样本复刻音色 | 样本的本地检查（时长、格式、大小）。沿用默认音色、`--use`、`--list` 不花钱，不需要 `--yes` |
| `voice/synth.py` | 合成配音（按字数，少），并识别一遍剪语气词 | 每段实际发给接口的文字、要合成几段几个字、几段已缓存 |
| `voice/align_words.py` | 语音识别（很少） | 要识别几段 |
| `voice/verify_audio.py` | 只有缺 `words.json` 时先去对齐 | 本身只读文件、不联网；缺 `words.json` 又没加 `--yes` 时退出码 1 |
| `avatar/run_avatar.py` | 云端生成人物视频（**最贵**） | 要生成几段、多少秒、每段种子、哪些段会沿用、哪些片段会收进历史 |
| 其余所有脚本 | 不联网、不花钱 | — |

花钱之前要按 `SKILL.md`「要停下来等用户的地方」报费用；`run_avatar.py` 的预览直接给出预计费用（`estimate_coins`，按本机以往的实际费用算）。

**密钥**：脚本只报密钥的来源，不打印内容。调用方也不要在输出里复述密钥。

**音频只能在合成时改**：要加速、减速，只能用 `synth.py --speed <倍数>`，之后再对齐、分段、生成。不要用 ffmpeg 或别的工具去改 `out/` 和 `avatar/` 下的任何音频——停顿和切点是按改之前的音频算的，对不上，视频里会出现明显的停顿。每一步都会核对上一步记下的音频指纹，对不上就以退出码 1 停下：

| 哪一步核对 | 核对什么 | 对不上说明 |
|---|---|---|
| `align_words.py` | `out/merged.wav`、`out/seg_NN.wav` 和合成时一致 | 合成之后音频被改过 |
| `plan_segments.py` | `out/merged.wav` 和逐词对齐时一致 | 对齐之后音频被改过 |
| `run_avatar.py`（预览和提交）、`finalize.py` | `avatar/seg_NN.wav`、`out/merged.wav` 和分段时一致 | 分段之后音频被改过 |

被拦下时按报错做：`synth.py --speed <倍数> --yes` 重新出音频 → `align_words.py --yes` → `plan_segments.py` → 按新分段重写提示词、`build_prompts.py`。

**结果文件里没有指纹也会停下**：说明它不是现在的脚本写出来的（旧版本，或者被手工改过）。能重跑上一步就重跑；确定是旧项目要沿用，加 `--legacy`（`align_words.py`、`plan_segments.py`、`run_avatar.py`、`finalize.py` 都有）。另外 `plan_segments.py` 不靠指纹也会核对内容：从配音里检测出的停顿超过 20% 落在词中间，就说明逐词时间和配音对不上，停下。

**不要手工编辑**任何时间文件和音频：`timeline.json`、`words.json`、`plan.json`、`finalize.json`、`merged.wav`、`avatar/seg_NN.wav`。要改什么都有对应的脚本参数（下表）。运行环境跑不了这些脚本（缺依赖装不上、沙盒里不能执行）时，以退出码 7 停下告诉用户，**不要用估算的时间代替**。

**想做什么 → 用哪个参数**（找不到办法时先查这张表；音频、时间文件一律不手工改）：

| 想做什么 | 怎么做 |
|---|---|
| 配音整体加快或放慢 | `pipeline.py voice --workdir <目录> --speed 1.1 --yes`（或 `synth.py --speed`），之后重新对齐、分段 |
| 拆段不对 | `voice/split_text.py --workdir <目录> --force` 按规则重拆；用户明确要按他的分段配才加 `--user-split` |
| 某一句念错、语气不对 | 改 `emotions.json` 或文案后重跑 `pipeline.py voice`（有缓存，只重合成改了的段） |
| 分段的切点不好 | 重跑 `pipeline.py segments`；要更短的段用 `plan_segments.py --max 12` |
| 只重做某几段人物视频 | `run_avatar.py --only 3 5 --seed random`（先预览报费用，再加 `--yes`） |
| 换回某段以前的版本 | `takes.py --use 6=2`，然后 `pipeline.py finish` |
| 取消还在云端跑的任务 | `run_avatar.py --cancel`（`--only` 只取消某几段） |
| 提交时一直排队 | `run_avatar.py --queue-min 60` 等得更久；或者稍后重跑同一条命令 |
| 接缝处顿一下 | 拼接默认裁掉每段开头 4 帧（`finalize.py --head-frames`） |
| 旧项目缺音频指纹被拦下 | 能重跑就重跑上一步；确定沿用旧结果才加 `--legacy` |


## 二、一条片子的调用顺序

用一键流程 `scripts/pipeline.py` 最省事：一个环节一条命令，失败就停在那一步并给出 `next`。

```
setup_env → voice/check_key → voice/project init → voice/clone_voice（--yes）→ （需要情绪时写 emotions.json）
→ pipeline.py voice（预览）→ pipeline.py voice --yes [--speed 1.1]        # 拆段 → 拆段把关 → 合成 → 逐词对齐 → 核对
→ avatar/check_setup → avatar/prepare_image（看 risks 和裁剪预览）→ pipeline.py segments
→ （写 prompt_src.json）avatar/build_prompts
→ avatar/run_avatar（预览，报费用）→ 等确认 → avatar/run_avatar --yes
→ pipeline.py finish                                                      # 拼接 → 质检
→ （有问题）avatar/takes 换版本，或 run_avatar --only 重跑 → pipeline.py finish
→ 进入包装：packaging/remotion_new.py --workdir（异常时间段按 anomalies.json 遮盖）
```

括号里的文件由 agent 按说明自己写，不是脚本生成。中途断了，先跑 `status.py` 看走到哪一步。顺序不能调换：调速在合成里做，不能放到对齐或分段之后。

## 三、各工具

下面的路径都相对项目目录（`<workdir>`）。

### 准备和总控

**`scripts/pipeline.py`** — 一个环节一条命令，模型不用自己处理音频、不用记顺序。

- 命令：`voice --workdir <目录> [--speed 1.1] [--user-split] [--yes]`（没有 `paragraphs.json` 时先自动拆段 → 拆段把关 → 合成 → 逐词对齐 → 核对；不加 `--yes` 停在合成预览）；`segments --workdir <目录> [--legacy]`（分段）；`finish --workdir <目录> [--legacy]`（拼接 → 质检）。
- 各步的输出原样打印。任何一步失败就停：机读结果里 `failed_step` 是哪一步，`error` 是原因，`next` 是该运行的命令。
- 机读结果：`steps`（每步的脚本、退出码）、`failed_step`、`preview`，以及最后一步的要点（`issues`、`segments`、`cut_punct`、`must_view`）、`next`。
- 退出码：和失败那一步相同。

**`scripts/setup_env.py`** — 检查依赖，缺什么自动装什么（Python 包、ffmpeg）。

- 命令：`python scripts/setup_env.py [--stage voice|avatar|all] [--check]`；`--check` 只看不装。
- 机读结果：`stage`、`present`（已有的）、`installed`（这次新装的，要告诉用户）、`failed`（没装上的，每项带 `tell_user`）、`missing`（`--check` 时缺的）。
- 退出码：0；7 有东西没装上。

**`scripts/status.py`** — 项目走到哪一步、下一步做什么。不联网、不写文件。

- 命令：`python scripts/status.py [--workdir <项目目录>]`（不给就是最近一个项目）；`--list` 列出所有项目。
- 机读结果：`project_dir`、`chars`、`seconds`、`voice`（各项有没有）、`avatar`（各项有没有、异常时间段数）、`stage`（`voice` / `avatar` / `done`）、`next`（一句话）；`--list` 时是 `projects`。
- 退出码：0；1 还没有任何项目。

### 配音阶段 `scripts/voice/`

**`check_key.py`** — 本机有没有百炼 Key。

- 命令：`python scripts/voice/check_key.py`；`--lock` 把密钥文件设为仅当前用户可读。
- 机读结果：`found`、`source`。
- 退出码：0；3 没有 Key（打印的引导文案要原样发给用户）。

**`project.py`** — 建项目目录、看进度。

- 命令：`init --copy <文案文件>`（建立或命中已有项目）；`show [--workdir <项目目录>]`；`latest`（打印最近一个项目目录）。
- 写：`copy.txt`、`meta.json`、根目录的 `latest.json`。
- 机读结果：`project_dir`、`cache_hit`（`init` 时：是不是命中了已有项目）、`state`（各环节有没有做完）；`show` 多一个 `meta`。
- 退出码：0；1 还没有任何项目；2 文案是空的。

**`clone_voice.py`** — 复刻音色、列出和换回以前的音色。

- 命令：`--workdir <项目目录> --sample <人声样本>`（只做本地检查）→ 加 `--yes` 才上传复刻；`--workdir <项目目录>`（不给样本就沿用默认音色，不花钱）；`--force`（已有也重新复刻）；`--list`；`--workdir <项目目录> --use <序号>`。
- 写：`voice.json`；根目录的 `voice.json`（默认音色）、`voice_history.json`。
- 机读结果：`voice_id`、`voice_json`、`action`（`created` / `reuse_project` / `reuse_default` / `use`）、`sample_seconds`；预览时 `preview`、`sample_seconds`、`sample`；`--list` 时是 `voices`。
- 退出码：0；2 参数不全、样本文件不存在；4 密钥被拒；5 需要用户给（或重新给）人声样本：没有可沿用的音色、格式不支持、太大、太短、太长、读不出时长；8 接口复刻失败。

**`split_text.py`** — 配音前按规则自动拆段，写 `paragraphs.json`（不让模型自己判断）。

- 规则：全文不超过 1000 字整篇一段；超过就拆成尽量少、尽量均匀的几段（每段 ≤1000 字），只在整句结束处断，优先断在用户换行的地方。用户的换行只是“可以断开的位置”，不是“每行一段”。
- 命令：`--workdir <项目目录>`；`--user-split`（用户明确说“按我的分段配”时才用：一个自然段一段）；`--force`（已有 `paragraphs.json` 时覆盖）。
- 机读结果：`paragraphs`、`segments`（每段字数）、`user_split`；已有文件没覆盖时 `kept`。
- 退出码：0；1 有一句话超过 1000 字没法在句末断；2 没有文案。

**`split_check.py`** — 检查拆段：拼起来和原文一字不差、每段不超过上限、只断在句末、没有拆得过碎。

- 命令：`--copy <workdir>/copy.txt --paragraphs <workdir>/paragraphs.json`；`--user-split`（用户要按他的分段，放行“拆得过碎”两条）。
- 不合格：文字对不上、某段超长、某段不是在整句结束处断开、全文不超过 1000 字却拆成多段、有不到 60 字的短段（后两条在 `user_split` 时放行）。
- 机读结果：`passed`、`segments`（每段字数）、`max_chars`、`user_split`；不合格时 `next` 是重新拆段的命令。
- 退出码：0 合格；1 不合格。

**`synth.py`** — 并发合成各段配音、去掉多余的语气词、拼成一条。发给接口的文字会自动处理两处（文案本身不改，字幕照样用原文）：没有标点结尾的换行（标题行）补一个句号，不然标题和下一行连成一句、中间没有停顿；行首的列表序号“1. ”改成“1、”。

- 命令：`--workdir <项目目录>`（**预览**：打印要发的文字，不联网）→ 加 `--yes` 才合成；`--keep-fillers`（不剪语气词）；`--variant <名字>`（另出一版到 `out_<名字>/`，不覆盖）；`--gap <秒>`（段间停顿，默认 0.3，按调速后的时间算）；`--speed <倍数>`（整体调速，0.5–2.0，例如 1.1；只变快慢、不变调。**调速只能用这个参数**）。
- 读：`voice.json`、`paragraphs.json`、`emotions.json`（有就用）。
- 写：`out/merged.wav`、`out/seg_NN.wav`、`out/timeline.json`、`cache/`；结果和上一版不同时，上一版自动存到 `out_prev/<时间>/`。
- 机读结果：`merged`、`timeline`、`segments`、`total_seconds`、`cached`（命中缓存的段数）、`notes`（剪掉了哪些语气词等，要告诉用户）、`speed`；预览时 `preview`、`to_synthesize`（要合成的段号）、`cached`（已缓存的段号）、`chars_to_synthesize`、`instruction`；失败时 `failed_segments`；文件被占用时 `locked`。
- 退出码：0；1 缺 `voice.json` 或 `paragraphs.json`；2 `--speed` 超出范围；4 密钥被拒；6 `merged.wav` 被占用（新的另存为 `merged_新.wav`）；8 有段合成失败（没拼接，重跑只补失败的段）。

**`align_words.py`** — 逐词时间和停顿。

- 命令：`--workdir <项目目录>`（预览）→ 加 `--yes` 才识别；`--out out_<名字>`（对 `--variant` 出的版本）；`--legacy`（`timeline.json` 没有音频指纹时也继续）。
- 写：`out/words.json`。
- 机读结果：`words_json`、`words`（词数）、`pauses`（停顿数）、`total_seconds`；预览时 `preview`、`segments`。
- 退出码：0；1 还没有 `out/timeline.json`（先合成），或音频在合成之后被改过；4 密钥被拒；8 识别失败。

**`verify_audio.py`** — 核对配音：文字对不对、语速音高是否一致、有没有把标签念出来、有没有漏剪的语气词。

- 命令：`--workdir <项目目录>`；`--out out_<名字>`。本身不联网。没有 `words.json` 时退出码 1；加 `--yes` 则先自动跑 `align_words.py --yes`。
- 写：`out/verify.json`。
- 机读结果：`verify_json`、`issues`（要处理或要用户听的问题，每条一句话）、`segments`（每段的时长、语速、音高、文字相似度）。
- 退出码：0；其余同 `align_words.py`。

### 数字人阶段 `scripts/avatar/`

**`check_setup.py`** — RunningHub Key、会员档位、应用 ID、工作流版本。

- 命令：不带参数是检查；`--lock`；`--set-plan <档位>`；`--set-concurrency <数>`；`--set-workflow "<版本标签>"`（用户说改过云端工作流时记一个）；`--set-app-id <ID>`（一般用不到）。
- 写：`~/.runninghub/config.json`。
- 机读结果：`key_found`、`key_source`、`plan`、`concurrency`、`app_id`、`workflow`；设置时是 `saved`。
- 退出码：0；2 档位名写错；3 没有 Key；5 还没记录会员档位（问用户一次是哪一档）。

**`prepare_image.py`** — 定画幅和尺寸、中心裁剪。

- 命令：`--image <图片> --workdir <项目目录> --mouth-box x0,y0,x1,y1`（嘴在裁剪后画面里的位置，占宽高的比例，看图后给出）。
- 写：`avatar/image.png`、`avatar/image_info.json`、`avatar/image_crop_check.jpg`（**要打开看**：头顶和手有没有被裁掉）。
- 风险预检（`risks`，有就告诉用户、让他换图或确认继续）：偏糊、过暗过亮、对比太低、透明背景、放大超过 1.5 倍；给了 `--mouth-box` 时还查嘴离上边太近（头顶会出画）、脸太小、人物不在中间。头顶、双手、商品有没有被裁到脚本看不出来，`checklist` 列出看裁剪预览时要确认的几项。
- 机读结果：`image`、`image_info`、`crop_check`、`ratio`、`width`、`height`、`cropped_fraction_lost`、`warnings`、`risks`、`checklist`。
- 退出码：0；2 图片不存在。

**`plan_segments.py`** — 在停顿处分段，每段 14 秒以内，切点优先句末，切出每段音频。

- 规则：停顿直接从 `merged.wav` 里检测（不用 `words.json` 里记的那份）；切点优先句末（。！？）的停顿，其次逗号处，没有标点的停顿最后才用，同时尽量均分；多一段能让切点都在句末、逗号处时宁可多一段。
- 核对：检测出的停顿超过 20% 落在词中间 → 逐词时间和配音对不上，退出码 1（正常 4%–7%）。
- 命令：`--workdir <项目目录>`；`--max <秒>`（改上限）；`--legacy`（`words.json` 没有音频指纹时也继续）。
- 读：`out/merged.wav`、`out/words.json`。写：`avatar/plan.json`、`avatar/seg_NN.wav`。
- 机读结果：`plan`、`segments`、`max_seconds`、`total_seconds`、`cut_tier`（0 不用拆；1 全在停顿处；2 用了句末标点；3 用了词边界）、`cut_punct`（切点里句末、逗号、无标点各几个）、`mismatch_ratio`、`durations`。
- 退出码：0；1 没有 `words.json`、找不到合适的切点、逐词时间和配音对不上、没有指纹（没加 `--legacy`），或音频在逐词对齐之后被改过。

**`build_prompts.py`** — 组装并校验每段提示词。

- 命令：`--workdir <项目目录>`
- 读：`avatar/plan.json`、`avatar/prompt_src.json`（agent 照 `references/avatar/prompt_src.example.json` 写）。写：`avatar/prompts/segment_NN.txt`。
- 机读结果：`passed`、`prompts_dir`、`segments`、`style`（只有 `narrative_zh` 中文叙述一种）、`image_type`、`prompt_prefix`、`include_scene`、`include_line`、`hints`（只提示的问题）；没通过时是 `problems`（必须改的）和 `hints`。
- 退出码：0；1 校验没通过，或 `prompt_src.json` 格式不对、段数对不上。

**`run_avatar.py`** — 提交云端生成人物视频：并发、重试、断点续跑、种子、历史版本、工作流版本。

- 命令：`--workdir <项目目录>`（**预览，不联网不花钱**）；加 `--yes` 才提交。`--only <段号...>` 只重跑这几段；`--all` 全部重跑；`--seed random|<整数>` 换种子（不写：没跑过的段用 42；跑过的段沿用上次的种子，但提示词和工作流都没改时自动换随机种子，因为结果会一模一样）；`--cancel`（取消还在云端跑的任务，配合 `--only`）；`--queue-min <分钟>`（并发名额被占满时最多排队等多久，默认 30）；`--legacy`；`--note "<一句话>"` 给这次的版本记备注；`--instance`（默认 `plus`，`ultra` 未经用户授权不用）；`--retries`；`--timeout-min`。
- 读：`avatar/plan.json`、`avatar/image_info.json`、`avatar/prompts/`。
- 写：`avatar/clips/seg_NN.mp4`、`avatar/result.json`；重跑某段前，它原来的片段自动收进 `avatar/takes/seg_NN/`。
- 机读结果（预览和提交都有）：`workflow`、`instance`、`concurrency`、`total_segments`、`todo`（每段的秒数和种子）、`todo_seconds`、`estimate_coins`（预计 RH 币：`low`、`mid`、`high`、`basis`）、`auto_seed`（自动换了随机种子的段）、`reused`（沿用的段）、`resumable`（会续上的云端任务）、`will_archive`（会收进历史的段）。预览时 `preview: true`。提交后多 `done`、`failed`（段号 → 原因）、`cost_known`（人民币合计）、`coins_known`（RH 币合计；都没返回为 `null`，**不是 0**）、`cost_reported_segments`、`result_json`。`--cancel` 时是 `cancelled`、`failed_cancel`（取消接口没实测过，失败就到后台手动取消）。
- 退出码：0；1 缺提示词，或音频在分段之后被改过（预览时就会拦下）；2 种子写错；3 没有 Key；4 密钥被拒；8 有段云端失败（重跑同一条命令：已成功的不重复提交，云端还在跑的会续上）。

**`takes.py`** — 各段的历史版本：列出、查看、换回。

- 命令：`--workdir <项目目录>`（列表）；`--use 6=2 [9=1 ...]`（第 6 段换成历史版本 2，现在用的收进历史）；`--show 6=2`（打印那一版当时的提示词）。
- 写（`--use` 时）：`avatar/clips/`、`avatar/takes/`、`avatar/result.json`。换完要重新跑 `finalize.py` 和 `qa_video.py`。
- 机读结果：列表时 `segments`（每段的 `current` 和 `takes`，含种子、工作流版本、生成时间、提示词指纹、备注）；`--use` 时 `swapped`；`--show` 时 `prompt`、`seed`、`workflow`。
- 退出码：0；1 那一段还有云端任务没跑完；2 版本不存在。

**`finalize.py`** — 裁掉多余帧、拼接各段、配上原配音。

- 命令：`--workdir <项目目录>`；`--fps <帧率>`（默认 24）；`--head-frames <帧数>`（从第 2 段起每段开头裁掉几帧，默认 4，0 不裁）。
- 规则：每段尾巴按音频时长裁掉多出来的帧；从第 2 段起，每段开头裁掉几乎不动的几帧，**同时裁掉配音里同样长的一段开头静音**（口型不错位），最多裁到这一段第一个字之前 0.08 秒。所以成片比 `merged.wav` 略短（每个接缝约 0.17 秒）。
- 拼接前逐段核对：某段视频比它的音频短（拼进去后面的口型会全部提前），就点名停下，`next` 是重跑那几段的命令。
- 读：`avatar/plan.json`、`avatar/result.json`、`avatar/clips/`、`out/merged.wav`、`out/words.json`。写：`avatar/final_avatar.mp4`、`avatar/voice_final.wav`（和成片完全对齐的无损配音，后期包装用）、`avatar/finalize.json`（每段裁了几帧、各段在成片上的起止、配音时间 → 成片时间的 `map`）、`avatar/words_final.json`（换算到成片时间轴的逐词时间和停顿）。
- 机读结果：`final`、`head_frames`（每段开头裁掉的帧数）、`final_seconds`、`finalize_json`、`checks`（分辨率、时长差、黑帧、冻结，每项带 `ok`）、`next`；片段太短时 `short_clips`。
- 退出码：0；1 有段还没视频、某段视频比音频短、检查项没过、没有指纹（没加 `--legacy`），或音频在分段之后被改过；7 找不到 ffmpeg。

**`qa_video.py`** — 成片质检：嘴、手、色彩；结束时自动调用 `qc_frames.py` 查镜头、接缝、生成总览图。

- 命令：`--workdir <项目目录>`；`--video <别的视频>`。
- 写：`avatar/qa.json`、`avatar/anomalies.json`、`avatar/mouth_strip.png`，以及 `qc_frames.py` 写的那些。
- 机读结果：`issues`（所有自动查出的问题，每条一句话）、`anomalies_json`、`qa_json`、`qc_json`、`mouth_strip`、`seams_image`、`sheets`、`must_view`（**必须打开看的图**：嘴部逐秒图、接缝图、全片总览图——多手、手里多出东西、烧字幕这几类只能靠看图发现）。
- 退出码：0（发现问题也是 0，问题在 `issues` 里）。

**`qc_frames.py`** — 镜头推拉、接缝跳变、全片总览图（一般由 `qa_video.py` 调用）。

- 命令：`--workdir <项目目录>`；`--video <别的视频>`。
- 写：`avatar/qc_frames.json`、`avatar/seams.png`、`avatar/sheets/overview_NN.jpg`（每 0.5 秒一格、每格宽 200 像素、每张 30 秒）；镜头问题追加进 `avatar/anomalies.json`。
- 机读结果：`issues`、`qc_json`、`sheets`、`seams`（每个接缝的跳变和倍数）、`camera`（每段的缩放范围和状态：`正常` / `镜头有推拉` / `无法判断`）。
- 退出码：0。

**`contact_sheet.py`** — 按时间点或间隔抽帧拼图，用来放大看可疑的几秒。

- 命令：`--video <视频> --times 12.0,12.5,13.0 --out <图片>`，或 `--video <视频> --every 3 --out <图片>`；`--width`（每格宽，默认 480）、`--cols`、`--start`、`--end`、`--per-sheet`（每张最多几格，超过自动拆成几张）。
- 机读结果：`sheets`（写出的图片路径）、`frames`。
- 退出码：0；1 没取到任何帧；2 参数不全；7 找不到 ffmpeg。

### 只被引用的模块

`scripts/_cli.py`（命令行约定）、`scripts/_util.py`（找 ffmpeg、读媒体时长、读写含中文路径的图片、音频指纹核对、停顿检测）、`scripts/voice/common.py`、`scripts/voice/asr.py`、`scripts/voice/fillers.py`、`scripts/avatar/common.py`。不要直接运行。

## 四、结果文件里有什么

| 文件 | 内容 |
|---|---|
| `meta.json` | 文案哈希、字数、创建时间，各环节写回的进度（音色、配音、逐词时间、人物视频路径） |
| `voice.json` | 用的音色：`voice_id`、样本路径和时长、复刻时间 |
| `paragraphs.json` | 配音的拆段：`{"paragraphs": [...]}`（agent 写） |
| `emotions.json` | 语气描述和情绪标签规则（agent 写，格式见 `references/voice/emotions.example.json`） |
| `out/timeline.json` | 每段配音在合并音频里的起止秒、首部裁剪量、用的指令、倍速 `speed`、音频指纹 `audio_sha`（整条和每段） |
| `out/words.json` | `words`：每个词的起止秒；`pauses`：≥0.25 秒的停顿及前后的词；对齐时用的音频指纹 `audio_sha` |
| `out/verify.json` | `rows`：每段的时长、语速、音高、文字相似度；`flags`：问题 |
| `avatar/image_info.json` | 画幅、输出尺寸、裁剪框、裁掉的比例、嘴部位置、提示 |
| `avatar/plan.json` | 每段的起止、时长、断在哪（停顿／标点／词边界）、音频路径和指纹、段内逐词时间；整条音频的指纹、倍速 |
| `avatar/prompt_src.json` | 写提示词的原料：图片类型、双手和身体的原姿态、每段台词和动作叙述（agent 写） |
| `avatar/result.json` | `results`：每段在用的片段（路径、任务号、种子、工作流版本、提示词全文和指纹、生成时间、来自哪个历史版本）；`pending`：提交了还没完成的云端任务；`errors`；`seeds` |
| `avatar/takes/seg_NN/take_NN.json` | 历史版本的记录：种子、工作流版本、提示词、任务号、生成时间、收进历史的时间、备注 |
| `avatar/qa.json` | 每段嘴的动作量、两只手的静止比例、色差；问题列表 |
| `avatar/qc_frames.json` | 每段镜头缩放、每个接缝的跳变、总览图路径、问题列表 |
| `avatar/finalize.json` | 拼接：每段开头裁掉几帧（`head_frames`）、各段在成片上的起止（`segments`，`qa_video.py`、`qc_frames.py` 按它找接缝和换算时间）、配音时间 → 成片时间的 `map` |
| `avatar/voice_final.wav` | 和成片 `final_avatar.mp4` 完全对齐的配音（裁过每段开头的静音，无损）。后期包装的声音用它 |
| `avatar/words_final.json` | 和 `out/words.json` 同样的格式，时间换算到了成片 `final_avatar.mp4` 上（拼接时裁过每段开头）。后期包装、字幕用它 |
| `avatar/anomalies.json` | 异常时间段：起止秒、类型（`mouth` 嘴不动 / `color` 色彩 / `camera` 镜头推拉）、说明 |

## 五、测试

都不联网、不花钱。改了对应的脚本就跑一遍：

```
python tests/voice/test_voice_stage.py       # 配音阶段
python tests/voice/test_preview.py           # 花钱的脚本默认只预览、--yes 才执行
python tests/avatar/test_build_prompts.py    # 提示词校验
python tests/avatar/test_retry.py            # 提交的重试与续跑、排队、取消
python tests/avatar/test_takes.py            # 历史版本、工作流版本
python tests/test_pipeline.py                # 一键流程、自动拆段和拆段把关、机读结果里的 next
python tests/test_audio_guard.py             # 音频指纹：调速只能在合成时做、缺指纹要 --legacy
python tests/avatar/test_plan_segments.py    # 分段：停顿以配音为准、切点优先句末、逐词时间对不上会拦下
python tests/avatar/test_prepare_image.py    # 图片风险预检
python tests/avatar/test_seed.py             # 种子策略、预计费用
python tests/avatar/test_finalize.py         # 拼接：裁每段开头、音画同步、时间换算、片段太短
```
