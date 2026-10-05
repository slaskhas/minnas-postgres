# 环境变量全表

> 从 `AGENTS.md` 下沉。
> 返回 [AGENTS.md](../AGENTS.md)

## 环境变量全表

复制 `.env.template` 为 `.env` 并填写。所有配置通过环境变量注入，**零代码改动**。

| 变量 | 必填 | 默认 | 说明 |
|------|:---:|------|------|
| `ARK_API_KEY` | 推荐 | - | 火山引擎 ARK（豆包）：日常对话 LLM（不用于 embedding，embedding 见下） |
| `DEEPSEEK_API_KEY` | 推荐 | - | DeepSeek：蒸馏/审计（双底座） |
| `MODEL_BACKEND` | 否 | `ark` | `ark` \| `openai`（OpenAI 兼容端点切换） |
| `OPENAI_API_KEY` | 是 | - | embedding 必填（embedding 固定走 OpenAI 兼容端点，与 `MODEL_BACKEND` 无关） |
| `OPENAI_BASE_URL` | 否 | `https://api.openai.com/v1` | 本地 vLLM: `http://localhost:8000/v1` |
| `OPENAI_EMBED_MODEL` | 否 | `text-embedding-3-small` | embedding 模型 |
| `OPENAI_CHAT_MINI` | 否 | `gpt-4o-mini` | 轻量对话 |
| `OPENAI_CHAT_LITE` | 否 | `gpt-4o` | 主力对话 |
| `EMBED_DIM` | 否 | `1536` | 向量维度，须与目标库 `vector(N)` 列宽度、以及所选 embedding 模型的**实际原生输出维度**一致（设错会被维度校验拦下，见下方说明） |
| `PGUSER` | 是 | `postgres` | 数据库用户 |
| `PGPASSWORD` | 是 | - | 数据库密码 |
| `PGDATABASE` | 是 | `mnemosyne` | 数据库名 |
| `PGHOST` | 否 | `127.0.0.1` | 数据库地址 |
| `PGPORT` | 否 | `5432` | 数据库端口 |
| `MNEMOSYNE_HOST` | 否 | `127.0.0.1` | 服务监听地址 |
| `MNEMOSYNE_PORT` | 否 | `8010` | 服务监听端口（v7.8.3 起真正生效） |

> 模型可插拔原则：换模型/换后端只改环境变量，不碰代码。

## Ollama / 其它 OpenAI 兼容端点（本地或开发服务器）

`OPENAI_BASE_URL` 可指向任意实现了 `/v1/embeddings` 的服务，包括 Ollama。示例（开发服务器）：

```bash
OPENAI_BASE_URL=http://<host>:11434/v1   # 注意要带 /v1
OPENAI_EMBED_MODEL=mxbai-embed-large     # 需先 ollama pull mxbai-embed-large
OPENAI_API_KEY=unused                    # Ollama 不校验, 但代码要求该变量非空时才方便区分"未配置"
EMBED_DIM=1024                           # mxbai-embed-large 原生 1024 维
```

⚠️ **Ollama 的维度陷阱**：请求体里的 `dimensions` 参数只对支持 Matryoshka 截断的模型生效，
且只能**截断到比原生维度小**的值。若 `EMBED_DIM` 设置得**比模型原生维度大**（例如对
`mxbai-embed-large` 原生 1024 维的模型设 `EMBED_DIM=1536`），Ollama 会**静默忽略**该参数，
直接返回原生维度的向量，不报错。`core/embedding.py` 对此有运行时校验：返回向量长度与
`EMBED_DIM` 不符时会立即抛 `EmbeddingDimensionError`（不重试），而不是让错误维度的向量
悄悄写入数据库或等到 `INSERT` 时才报一个不好排查的 Postgres 错误。

常见 Ollama embedding 模型的原生维度：`nomic-embed-text`=768，`mxbai-embed-large`=1024，
`bge-m3`=1024。目前没有常见 Ollama 模型原生输出 1536 维（1536 是 OpenAI
`text-embedding-3-small` 的默认值）——开发环境若用 Ollama，`EMBED_DIM` 与目标库的
`vector(N)` 列宽度应按所选模型的原生维度配置，与生产环境（OpenAI，1536 维）分开。

---
