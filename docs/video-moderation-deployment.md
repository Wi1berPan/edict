# Video Moderation Agent Lab 部署说明

该子系统位于 `video_moderation/`，用于本地开发和验证“视频违规判断 agent”能力。

核心能力：

- 每个风险域由独立 subagent 负责。
- 支持豆包/火山方舟 Ark OpenAI-compatible Chat Completions。
- 没有豆包 API Key 时可自动 fallback 到本地 deterministic subagent。
- 业务 feedback 可以进入指定风险域 sub-benchmark。
- badcase 提交后可自动触发对应 subagent 迭代。
- 支持一键重建单个或全部 subagent。
- 看板展示 grounding、OCR、transcript、证据、模型 reasoning 和 provider fallback trace。

## 1. 本地启动

```powershell
cd E:\edict
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r video_moderation\requirements.txt
python -m uvicorn video_moderation.app:app --host 127.0.0.1 --port 8010 --reload
```

打开：

```text
http://127.0.0.1:8010
```

运行数据默认保存在：

```text
E:\edict\data\video_moderation\state.json
```

上传视频保存在：

```text
E:\edict\data\video_moderation\uploads
```

## 2. 配置豆包 / 火山方舟 Ark

本项目使用火山方舟 Ark 的 OpenAI-compatible Chat Completions 形态：

- Base URL: `https://ark.cn-beijing.volces.com/api/v3`
- HTTP endpoint: `POST /chat/completions`
- Header: `Authorization: Bearer $ARK_API_KEY`
- Model: 豆包模型 ID 或方舟 Endpoint ID，例如 `doubao-1-5-vision-pro-32k-250115`，生产环境推荐使用你在方舟控制台创建的 `ep-...` Endpoint ID。

在 `video_moderation/.env` 中配置：

```env
VIDEO_MODERATION_PROVIDER=doubao
ARK_API_KEY=你的火山方舟APIKey
ARK_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
ARK_MODEL=doubao-1-5-vision-pro-32k-250115
DOUBAO_VIDEO_INPUT_MODE=video_url
```

也可以用环境变量：

```powershell
$env:VIDEO_MODERATION_PROVIDER="doubao"
$env:ARK_API_KEY="你的火山方舟APIKey"
$env:ARK_BASE_URL="https://ark.cn-beijing.volces.com/api/v3"
$env:ARK_MODEL="doubao-1-5-vision-pro-32k-250115"
```

兼容的 key 变量名：

- `ARK_API_KEY`
- `VOLCENGINE_ARK_API_KEY`
- `DOUBAO_API_KEY`

如果未配置 key，前端仍可选择 `Doubao Ark`，后端会回退到本地 subagent，并在 trace 中记录 `ARK_API_KEY is not configured`。

## 3. 真实视频测试

支持两种输入方式：

1. 公网视频 URL：在新增 case 或上传表单中填写 `source_url`，provider 会按 `video_url` content item 传给豆包视觉模型。
2. 本地上传视频：上传文件到本地；如果机器安装了 `ffmpeg` 且 `DOUBAO_VIDEO_INPUT_MODE=frames`，系统会抽帧为 `image_url` 输入。

建议先用公网 URL 测试豆包链路；本地上传视频更适合后续生产化时结合对象存储或视频预处理服务。

## 4. Feedback / Badcase / Sub-benchmark

每个风险域都有自己的 sub-benchmark：

- `violence`
- `adult`
- `self_harm`
- `hate`
- `regulated_goods`

在分析结果卡片的 `Feedback` 区域：

1. 选择正确标签：`pass` 或 `violation`。
2. 输入业务反馈说明。
3. 选择 badcase 要进入哪个 sub-benchmark，默认当前风险域。
4. 勾选 `Add to sub-benchmark`。
5. 勾选 `Auto iterate this subagent`。
6. 点击 `Submit Badcase`。

提交后系统会：

- 写入 `feedback`。
- 创建 `benchmark_items`，source 为 `feedback`。
- 从 feedback 中抽取 learned keywords。
- bump 对应 subagent version。
- 写入 `iteration_log`，用于追踪自动迭代。

Benchmark 面板：

- `Eval`：只评估某个风险域的 sub-benchmark。
- `Rebuild`：只重建某个风险域 subagent。
- `Rebuild All Subagents`：基于当前全部 feedback / benchmark 状态重建所有风险域 subagent。

## 5. API

```text
GET  /api/state
GET  /api/provider
GET  /api/risk-domains
GET  /api/videos
POST /api/videos
POST /api/videos/upload
POST /api/videos/{video_id}/analyze?provider=local|doubao
GET  /api/runs
GET  /api/runs/{run_id}
POST /api/runs/{run_id}/feedback
GET  /api/benchmarks
POST /api/benchmarks/evaluate
GET  /api/sub-benchmarks
POST /api/sub-benchmarks/{domain_id}/evaluate
POST /api/subagents/rebuild
```

新增公网视频 case：

```bash
curl -X POST http://127.0.0.1:8010/api/videos \
  -H "Content-Type: application/json" \
  -d '{
    "title": "public video test",
    "description": "real video URL",
    "source_url": "https://example.com/video.mp4",
    "transcript": "",
    "ocr_texts": [],
    "grounding": []
  }'
```

使用豆包分析：

```bash
curl -X POST "http://127.0.0.1:8010/api/videos/vid_xxx/analyze?provider=doubao"
```

提交 badcase：

```bash
curl -X POST http://127.0.0.1:8010/api/runs/run_xxx/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "domain_id": "violence",
    "correct_label": "violation",
    "notes": "业务确认：画面出现持刀威胁，应判违规",
    "benchmark_domain_id": "violence",
    "add_to_benchmark": true,
    "auto_iterate": true
  }'
```

重建所有 subagent：

```bash
curl -X POST http://127.0.0.1:8010/api/subagents/rebuild \
  -H "Content-Type: application/json" \
  -d '{"domain_id": null, "reason": "manual_all_rebuild"}'
```

## 6. 生产化建议

- 将 `state.json` 替换成 Postgres 或对象存储 + 索引表。
- 将 benchmark evaluation 接入 CI，核心风险域 PR 前必须 replay。
- feedback 进入人工审核队列，审核通过后再自动迭代 subagent。
- 对每次 prompt、policy、模型、Endpoint ID 变化记录版本。
- trace 中保留原始视频 URL、抽帧图片、OCR 原文、grounding box 和模型原始响应。
- 本地视频建议上传到 TOS/S3/OSS，再用公网或预签名 URL 交给模型。

## 7. 官方参考

- 火山方舟对话(Chat) API: https://www.volcengine.com/docs/82379/1494384
- 火山方舟 Base URL 及鉴权: https://www.volcengine.com/docs/82379/1298459
- 火山方舟常规在线推理: https://www.volcengine.com/docs/82379/2121998
