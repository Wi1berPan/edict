# Video Moderation Agent Lab

一个面向视频内容安全审核的多 subagent 实验系统，已适配豆包 / 火山方舟 Ark OpenAI-compatible API。

## 你能用它做什么

- 对一个视频同时跑多个风险域 subagent。
- 每个风险域维护自己的 sub-benchmark。
- 业务反馈 badcase 后自动进入 benchmark。
- badcase 可触发对应 subagent 自动迭代。
- 一键重建所有 subagent。
- 在看板里追踪模型分析过程、OCR、grounding、transcript、evidence 和 fallback 原因。

## 架构

```text
Video Case
  -> Orchestrator
    -> violence subagent
    -> adult subagent
    -> self_harm subagent
    -> hate subagent
    -> regulated_goods subagent
  -> Aggregated verdict
  -> Trace dashboard
  -> Feedback
  -> Sub-benchmark
  -> Subagent iteration
```

## Provider

默认 provider：

```text
local
```

豆包 provider：

```text
doubao
```

豆包使用火山方舟 Ark OpenAI-compatible Chat Completions：

```text
POST https://ark.cn-beijing.volces.com/api/v3/chat/completions
Authorization: Bearer $ARK_API_KEY
```

配置文件：

```text
video_moderation/.env
```

示例：

```env
VIDEO_MODERATION_PROVIDER=doubao
ARK_API_KEY=你的火山方舟APIKey
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_MODEL=doubao-1-5-vision-pro-32k-250115
DOUBAO_VIDEO_INPUT_MODE=video_url
```

生产建议使用火山方舟控制台创建的 Endpoint ID，即 `ep-...`，并把它填入 `ARK_MODEL`。

## 启动

```powershell
cd E:\edict
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r video_moderation\requirements.txt
python -m uvicorn video_moderation.app:app --host 127.0.0.1 --port 8010 --reload
```

访问：

```text
http://127.0.0.1:8010
```

## 没有豆包 Key 时

系统仍然可运行。选择 `Doubao Ark` 分析时，后端会：

1. 尝试读取 `ARK_API_KEY`。
2. 如果缺失，记录 provider error。
3. 自动 fallback 到本地 deterministic subagent。
4. 在 trace 里展示 fallback 原因。

这保证了你可以先开发 feedback、benchmark、看板和数据闭环，再接入真实 key。

## 视频输入

支持：

- `source_url`：公网视频 URL，按 `video_url` content item 发送给模型。
- 本地上传：上传到 `data/video_moderation/uploads`。
- 本地抽帧：安装 `ffmpeg` 后，设置 `DOUBAO_VIDEO_INPUT_MODE=frames`，系统会抽帧为 base64 `image_url`。

## Badcase 闭环

分析完成后，在每个风险域卡片中提交 feedback：

- `correct_label`: `pass` 或 `violation`
- `notes`: 业务反馈说明
- `benchmark_domain_id`: badcase 要进入的 sub-benchmark
- `add_to_benchmark`: 是否加入 benchmark
- `auto_iterate`: 是否自动迭代该 subagent

提交后：

- `feedback[]` 增加一条记录。
- `benchmark_items[]` 增加一条 badcase。
- subagent version 增加。
- `iteration_log[]` 记录迭代事件。

## 测试

```powershell
python -m compileall video_moderation
python -m pytest tests\test_video_moderation.py
```

## 关键文件

```text
video_moderation/app.py              FastAPI API
video_moderation/agents.py           Orchestrator / subagent / iteration
video_moderation/provider.py         Doubao Ark provider
video_moderation/storage.py          JSON local storage
video_moderation/static/             Dashboard
tests/test_video_moderation.py       Unit tests
docs/video-moderation-deployment.md  部署说明
```

## 当前限制

- `state.json` 是本地开发存储，生产应替换为数据库。
- 没有豆包 key 时无法真实调用远端模型，只能验证 fallback 和数据闭环。
- 不同豆包视觉模型对 `video_url` 支持可能受模型/Endpoint 配置影响；若 endpoint 不支持视频 URL，可用本地抽帧或对象存储预处理方案。
