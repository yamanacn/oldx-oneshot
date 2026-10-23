# OLDX oneshot: one-shot talking-head videos

[English](README.md) | [简体中文](README.zh-CN.md)

Turn a script into a ready-to-post digital-human talking-head video, using your own cloned voice and a single portrait photo.

Three stages; each stage's output is the next stage's input:

| Stage | What it does | Output |
|---|---|---|
| 1 Voice | Clones your voice, reads the script aloud, and produces word-level timestamps | Voice-over, word timings |
| 2 Avatar | Makes a photo speak, with hand gestures that follow the content | Avatar video |
| 3 Packaging | Adds motion graphics, captions and sound effects, rendered with Remotion | Final video |

## Install

This is a Claude Code skill. Clone it into your skills directory:

```bash
git clone https://github.com/yamanacn/oldx-oneshot.git ~/.claude/skills/oldx-oneshot
```

Then tell Claude Code something like "use oldx-oneshot to turn this script into a talking-head video", and give it the script, a 10-20 second voice sample, and a portrait photo. You can also run a single stage: voice-over only, avatar video only, or packaging for a talking-head video you already have.

## Where it stops and waits for you

It only stops where money is spent or a wrong guess is expensive; everything else runs on its own:

- **After the voice-over**: you get the file to listen to. Changing the voice later invalidates the avatar video, so your approval of this version is required before the avatar is generated (enforced in code).
- **Before generating the avatar video**: it reports the number of segments and the estimated RH coins. For long scripts it renders 1-2 sample segments first so you can check how big the gestures are. It asks up front whether you want small, medium or large movements.
- **Before the packaging style is locked**: 2-3 stills with different overall concepts to choose from, then 3 style stills and a 5-second sample; the full film is made only after you approve. With a reference video, it asks which layers to borrow (motion, pacing, layout, colour, elements) and keeps the reference's elements out of the layers you did not ask for.

## What the packaging stage can do

- Every film is designed from scratch, with no preset template; animation can use three.js / R3F, shaders, post-processing, GSAP, Lottie, PixiJS and d3.
- Bottom captions (the original script, with punctuation) and a chapter timeline are always included.
- **Screen-recording demos**: `timeline_edit.py` inserts gaps into the voice-over pauses so recordings and full-screen animations can finish before the next line; the presenter is frozen during gaps, so the template keeps them off screen and checks every frame.
- **Recording clean-up**: `demo_prep.py` crops browser chrome, converts to constant frame rate and blurs API keys, accounts and balances while transcoding; the demo window in the composition follows the voice-over, zooms into clicks and draws a cursor halo.
- **Background music**: ducked under the voice, beat-detected, and looped by whole bars when it is shorter than the film.
- **Clean-up**: `cleanup.py` removes files that can be regenerated for free before delivery; paid renders are never deleted.

## API keys

Two keys are needed. You provide both by pasting them into the chat; Claude saves them for you.

- Alibaba Cloud Bailian API key: voice-over and voice cloning
- RunningHub API key: generates the avatar video

The avatar stage uses an already-built workflow app, so you do not need to build anything. Just provide the key.

Where to get them:

- Bailian: https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key
- RunningHub: register with my invite link https://www.runninghub.cn?inviteCode=150e26b6 to get an extra 1000 RH coins. After signing in, choose "API" at the top of the page, click "获取密钥" (Get key) at the top left of the API page. The key page opens on "企业级-共享" (Enterprise, shared); switch to "消费级-会员" (Consumer, member) in the left sidebar and copy the key there.

The API endpoints are already written in the scripts; you do not need to fill them in.

Note: the RunningHub site and the prompts the skill shows to users are in Chinese.

## Environment

Missing Python packages, ffmpeg and Node.js are installed automatically by `scripts/setup_env.py`. The packaging render dependencies (about 900 MB) are installed once and shared by every project.

Rendering is mostly CPU-bound (each frame is a browser layout plus a screenshot); the GPU handles drawing and encoding. A 5-minute 1080p60 film takes about 20 minutes. Use 30 fps for drafts.

## Layout

- `SKILL.md`: skill entry point
- `references/`: detailed guides for the three stages (`packaging/design.md` covers what to settle before designing)
- `scripts/`: scripts for each stage; `scripts/pipeline.py` runs one stage per command, `scripts/status.py` shows where a project is
- `assets/remotion-template/`: Remotion project template for packaging (tools only, no preset design)
- `tests/`: offline tests (no network, no cost)
