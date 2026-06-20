# 能力资产诊断 · AI 前置采集服务

这是“三步深度版”的第一个用户-AI交互环节：

1. 用户创建诊断档案。
2. 上传过往材料，或粘贴简历、项目、复盘、文章、SOP。
3. 后端抽取文本并保存到 SQLite。
4. AI 基于 BEI/STAR、KSAO、三例证、SIGN、五桶归因、认知资产分层继续追问。
5. 信息足够后，AI 生成真人顾问会前审阅材料。

## 启动

```bash
OPENAI_API_KEY=你的key ./run.sh
```

默认地址：

```text
http://127.0.0.1:8787
```

可选环境变量：

```bash
PORT=8787
OPENAI_MODEL=gpt-4.1-mini
OPENAI_BASE_URL=https://api.openai.com/v1
```

## 数据保存

所有数据保存在：

```text
ai_intake_app/data/intake.sqlite3
ai_intake_app/data/uploads/
```

主要数据表：

- `sessions`：诊断档案
- `files`：上传材料和抽取文本
- `messages`：AI追问和用户回答
- `reports`：真人审阅材料包

## 当前支持文件

- `.txt`
- `.md`
- `.pdf`
- `.docx`
- 直接粘贴文本

