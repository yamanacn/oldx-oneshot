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

Then tell Claude Code something like "use oldx-oneshot to turn this script into a talking-head video", and give it the script, a 5-10 second voice sample, and a portrait photo.

## API keys

Two keys are needed. You provide both by pasting them into the chat; Claude saves them for you.

- Alibaba Cloud Bailian API key: voice-over and voice cloning
- RunningHub API key: generates the avatar video

The avatar stage uses an already-built workflow app, so you do not need to build anything. Just provide the key.

Where to get them:

- Bailian: https://bailian.console.aliyun.com/cn-beijing/model/settings/api-key
- RunningHub: register with my invite link https://www.runninghub.cn?inviteCode=150e26b6 to get an extra 1000 RH coins. After signing in, choose "API" at the top of the page, click "获取密钥" (Get key) at the top left of the API page, then create a new key on the key page and copy it.

The API endpoints are already written in the scripts; you do not need to fill them in.

Note: the RunningHub site and the prompts the skill shows to users are in Chinese.

## Environment

Missing Python packages, ffmpeg and Node.js are installed automatically by `scripts/setup_env.py`.

## Layout

- `SKILL.md`: skill entry point
- `references/`: detailed guides for the three stages
- `scripts/`: scripts for each stage
- `assets/remotion-template/`: Remotion project template used for packaging
- `tests/`: offline tests
