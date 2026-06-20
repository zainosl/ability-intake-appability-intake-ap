# Ability Intake App

能力资产诊断的 AI 前置采集与会前访谈系统。

## 环境变量

```text
DATA_DIR=/data
PORT=8787
HOST=0.0.0.0
ADMIN_PASSWORD=顾问台访问口令
OPENAI_MODEL=gpt-5.5
OPENAI_BASE_URL=你的模型接口路由
OPENAI_API_KEY=你的模型 API Key
```

## Docker

```bash
docker build -t ability-intake-app .
docker run -p 8787:8787 \
  -e DATA_DIR=/data \
  -e HOST=0.0.0.0 \
  -e ADMIN_PASSWORD=你的顾问台口令 \
  -e OPENAI_MODEL=gpt-5.5 \
  -e OPENAI_BASE_URL=你的模型接口路由 \
  -e OPENAI_API_KEY=你的模型APIKey \
  -v $(pwd)/data:/data \
  ability-intake-app
```

## 访问

顾问后台：`/`，需要输入 `ADMIN_PASSWORD`。

用户访谈页：`/client?session=xx`

用户访谈页不需要后台口令，只能看到对应档案的用户可见材料、AI 会前访谈和会前准备内容。
