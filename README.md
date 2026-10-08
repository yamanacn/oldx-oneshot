# OLDX oneshot：口播一条龙

把一段文案做成一条可以直接发的数字人口播视频，用你自己的声音和一张口播图片。

三个阶段，前一阶段的产物是后一阶段的输入：

| 阶段 | 做什么 | 产物 |
|---|---|---|
| 1 配音 | 复刻你的声音，把文案念出来，得到逐词时间 | 配音、逐词时间 |
| 2 数字人 | 让一张照片开口说话，手势跟着内容走 | 人物视频 |
| 3 包装 | 配图形动画、字幕、音效，用 Remotion 渲成片 | 成片 |

## 安装

这是一个 Claude Code 技能。克隆到技能目录即可：

```bash
git clone https://github.com/yamanacn/oldx-oneshot.git ~/.claude/skills/oldx-oneshot
```

然后在 Claude Code 里说“用 oldx-oneshot 帮我把这段文案做成口播视频”，并给它文案、5–10 秒的人声样本和一张口播图片。

## 需要的密钥

只需要两个，都由你自己提供（粘贴到对话里，Claude 会负责保存）：

- 阿里云百炼 API Key：配音和声音复刻
- RunningHub API Key：生成数字人视频

数字人用的是已经搭好的工作流应用，不需要自己搭建，只填密钥即可。

## 环境

Python 3、ffmpeg、Node.js 缺什么会由 `scripts/setup_env.py` 自动装好。

## 目录

- `SKILL.md`：技能入口
- `references/`：三个阶段的详细说明
- `scripts/`：各阶段脚本
- `assets/remotion-template/`：包装用的 Remotion 工程模板
- `tests/`：离线测试
