# Passagen

Passagen 是一个本地 CLI 论文处理工具。它将 PDF 导入受管理存储，补全论文元数据，解析全文，并通过 OpenAI-compatible LLM 生成经过 Pydantic Schema 校验的英文结构化摘要和英文技术提纲。

当前处理流程：

```text
PDF
  -> discovered
  -> metadata_resolved
  -> parsed
  -> summarized
  -> outlined
```

每个状态都表示一个已经成功持久化的阶段。处理失败不会将 Paper 改成单独的失败状态；修复问题后再次执行 `update`，程序会从最后成功阶段继续。

## 功能

- 递归扫描 PDF，计算 SHA-256 并去重。
- 将原始 PDF 导入内容寻址的受管理目录，后续处理不依赖扫描源文件。
- 从 PDF、Crossref、arXiv 和可选 GROBID 中提取及合并元数据。
- 使用 GROBID 或 PyMuPDF 生成统一的全文结构 `extracted.json`。
- 对长论文先分块提取事实，再生成通用 Structured Summary v2。
- 对 LLM 返回执行 JSON 解析、Pydantic 校验和有限修复。
- 仅根据合法 `summary.json` 生成分层英文 `outline.md`。
- 保存模型、Prompt、token 用量、原始响应和错误诊断。
- 支持阶段恢复、批量处理、数据库备份和 artifact 完整性检查。

## 环境要求

- Linux 或其他支持 Python 3.12+ 的环境。
- [`uv`](https://docs.astral.sh/uv/)。
- 包含文本层的 PDF；当前不提供 OCR。
- 一个 OpenAI-compatible Chat Completions 服务。
- 可选 GROBID 服务。没有 GROBID 时可以显式使用 PyMuPDF parser。

## 安装

```bash
git clone <repository-url> Passagen
cd Passagen
uv sync --frozen
cp passagen.example.yaml passagen.yaml
uv run passagen config check
```

查看 CLI：

```bash
uv run passagen --help
uv run passagen run --help
uv run passagen update --help
```

也可以构建 wheel：

```bash
uv build
```

## 快速开始

准备一个 PDF 目录，例如 `./papers/`，然后设置 LLM API key：

```bash
export PASSAGEN_API_KEY=your-key
```

如果没有运行 GROBID，在 `passagen.yaml` 中选择 PyMuPDF：

```yaml
pipeline:
  parsing:
    parser: pymupdf
```

初始化数据库并运行完整流程：

```bash
uv run passagen db init
uv run passagen run ./papers
```

`run` 依次执行 scan 和 update。重复运行具有幂等性：相同 PDF 会按 SHA-256 跳过，已经达到 `outlined` 的 Paper 不会再次调用外部服务或 LLM。

查看结果：

```bash
uv run passagen list
uv run passagen show <paper-id>
```

## 完整配置

默认读取当前工作目录下的 `passagen.yaml`。仓库中的 `passagen.example.yaml` 提供完整模板：

```yaml
passagen:
  data_dir: data
  database_path: null
  debug: false

providers:
  crossref:
    enabled: true
    base_url: https://api.crossref.org
    mailto: null
    timeout_seconds: 10

  arxiv:
    enabled: true
    base_url: https://export.arxiv.org
    timeout_seconds: 10

  grobid:
    base_url: http://localhost:8070
    timeout_seconds: 60

  llm:
    base_url: https://api.openai.com/v1
    model: gpt-4o-mini
    api_key_env: PASSAGEN_API_KEY
    timeout_seconds: 120
    disable_thinking: false

pipeline:
  metadata:
    first_pages: 2

  parsing:
    parser: auto
    min_text_characters: 10

  summarization:
    max_chunk_characters: 96000
    fact_max_output_tokens: 3000
    summary_max_output_tokens: 6000
    facts_prompt_path: null
    summary_prompt_path: null
    repair_prompt_path: null

  outlining:
    max_output_tokens: 4000
    prompt_path: null
```

### 基础配置

| 配置 | 说明 |
|------|------|
| `passagen.data_dir` | 数据库、受管理 PDF 和生成产物的根目录。 |
| `passagen.database_path` | SQLite 路径；为 `null` 时使用 `<data_dir>/passagen.db`。 |
| `passagen.debug` | 启用 DEBUG 文件日志；不会把 API key 写入日志。 |
| `--config` | 为单次命令指定其他 YAML 配置文件。 |
| `--data-dir` | 为单次命令覆盖数据目录。 |

配置也支持嵌套环境变量。例如：

```bash
export PASSAGEN_PROVIDERS__LLM__MODEL=paper-facts
export PASSAGEN_PIPELINE__PARSING__PARSER=pymupdf
```

API key 的环境变量名由 `providers.llm.api_key_env` 指定。程序读取该环境变量的值作为 Bearer token，但不会在 `config check`、日志或数据库中打印该值。

## 外部元数据服务

### Crossref

当 PDF 中识别到 DOI 时，Passagen 使用 Crossref `/works/<doi>` 做精确查询，不通过模糊标题自动合并论文。

```yaml
providers:
  crossref:
    enabled: true
    base_url: https://api.crossref.org
    mailto: you@example.com
    timeout_seconds: 10
```

建议设置 `mailto`，便于遵循 Crossref polite pool 使用规范。Crossref 请求失败属于可降级错误，程序会保留本地元数据。

### arXiv

当 PDF 中识别到 arXiv ID 时，Passagen 使用 arXiv API 做精确查询：

```yaml
providers:
  arxiv:
    enabled: true
    base_url: https://export.arxiv.org
    timeout_seconds: 10
```

不需要元数据补全时，可以将 Crossref 或 arXiv 的 `enabled` 设为 `false`。

## GROBID 配置

GROBID 用于学术 PDF 的 header 和全文 TEI 解析。它通常比通用 PDF 文本提取更可靠，尤其适合双栏论文和章节结构识别。

使用 Docker 启动：

```bash
docker run --rm --init -p 8070:8070 lfoppiano/grobid:0.9.1-crf
curl http://localhost:8070/api/isalive
```

更多 GROBID 配置和选项，请参考 [GROBID 官方文档](https://grobid.readthedocs.io/en/latest/)。

配置：

```yaml
providers:
  grobid:
    base_url: http://localhost:8070
    timeout_seconds: 60

pipeline:
  parsing:
    parser: auto
```

Parser 取值：

| 值 | 行为 |
|----|------|
| `grobid` | 使用 GROBID 解析全文；服务不可用时失败。 |
| `auto` | 使用当前默认的 GROBID 全文解析路径，并执行 provider 健康检查。 |
| `pymupdf` | 不依赖 GROBID，使用本地 PyMuPDF 解析。 |

没有部署 GROBID 时请显式配置 `parser: pymupdf`。GROBID metadata fallback 只在本地身份信息不足或需要冲突校验时使用。

## LLM 与 LiteLLM 配置

Passagen 调用 OpenAI-compatible 接口：

```text
POST <base_url>/chat/completions
```

OpenAI 示例：

```yaml
providers:
  llm:
    base_url: https://api.openai.com/v1
    model: gpt-4o-mini
    api_key_env: PASSAGEN_API_KEY
    timeout_seconds: 120
    disable_thinking: false
```

之后会考虑使用 LiteLLM 做不同层级 LLM 的转发。

LiteLLM 示例：

```yaml
providers:
  llm:
    base_url: http://localhost:4000/v1
    model: paper-facts
    api_key_env: PASSAGEN_API_KEY
    timeout_seconds: 120
    disable_thinking: true
```

```bash
export PASSAGEN_API_KEY=your-litellm-key
```

`model` 必须是 LiteLLM 暴露的模型名或 alias。`base_url` 应指向 API 前缀，Passagen 会自行追加 `/chat/completions`。

当 `disable_thinking: true` 时，请求 JSON 会包含：

```json
{
  "thinking": {
    "type": "disabled"
  }
}
```

这与 OpenAI SDK 的以下写法在 HTTP 层等价：

```python
extra_body = {"thinking": {"type": "disabled"}}
```

直连 `deepseek.com` 时程序也会自动发送该字段。通过 LiteLLM 中转时无法根据 URL 判断上游模型，因此需要显式设置 `disable_thinking`。

LLM 同时用于：

- 分块事实提取。
- Structured Summary v2 生成和有限修复。
- 分层英文 Outline v2 生成。

请求使用 `temperature: 0` 和 JSON object response format。所有响应仍会经过本地 Pydantic 校验。

执行 `run`、`update`、`summarize` 或 `outline` 后，CLI 会输出本次运行的 LLM 调用统计，
包括总调用次数、input/output/total token，以及 `fact`、`summary`、`outline` 各阶段明细。
facts 截断重试和 summary 修复均按实际请求次数统计；该统计仅保存在当前进程内，不写入数据库。

## Pipeline 参数

| 配置 | 说明 |
|------|------|
| `metadata.first_pages` | 本地元数据和标识提取读取的前几页。 |
| `parsing.min_text_characters` | 判断 PDF 是否具有有效文本层的最低字符数。 |
| `summarization.max_chunk_characters` | 长论文事实提取的单块字符上限。 |
| `summarization.fact_max_output_tokens` | 单个 facts 请求的最大输出 token。 |
| `summarization.summary_max_output_tokens` | 最终 Summary 和修复请求的最大输出 token。 |
| `outlining.max_output_tokens` | 英文 Outline 请求的最大输出 token。 |

如果模型上下文较小，应降低 `max_chunk_characters`。如果响应因为 `finish_reason=length` 被截断，应提高对应输出 token 上限，或进一步缩小输入块。

## 自定义 Prompt

内置 Prompt 位于：

```text
src/passagen/resources/prompts/
  facts-v2.txt
  summary-v2.txt
  repair-v2.txt
  outline-v2.txt
```

可以在配置中覆盖：

```yaml
pipeline:
  summarization:
    facts_prompt_path: prompts/facts.txt
    summary_prompt_path: prompts/summary.txt
    repair_prompt_path: prompts/repair.txt
  outlining:
    prompt_path: prompts/outline.txt
```

模板使用 Python `string.Template` 语法。允许的占位符：

| 模板 | 必需占位符 |
|------|------------|
| facts | `$schema`, `$chunk` |
| summary | `$schema`, `$identity`, `$facts` |
| repair | `$schema`, `$validation_error`, `$candidate` |
| outline | `$schema`, `$summary` |

字面量 `$` 必须写成 `$$`。`passagen config check` 会检查文件读取、占位符语法、缺失变量和未知变量。修改 Prompt 后应使用 `update <paper-id> --force` 重建最终产物。

## CLI 命令

| 命令 | 用途 |
|------|------|
| `passagen config check` | 校验配置和 Prompt 模板，并显示生效配置。 |
| `passagen db init` | 初始化 SQLite 数据库。 |
| `passagen db status` | 查看数据库 Schema 版本。 |
| `passagen db backup [destination]` | 创建一致性 SQLite 备份。 |
| `passagen scan <directory>` | 导入 PDF，但不执行后续处理。 |
| `passagen run <directory>` | 执行 scan，然后处理所有未完成论文。 |
| `passagen metadata <paper-id>` | 单独执行元数据阶段。 |
| `passagen parse <paper-id>` | 单独执行全文解析阶段。 |
| `passagen summarize <paper-id>` | 单独生成 Structured Summary v2。 |
| `passagen outline <paper-id>` | 单独生成英文 Outline。 |
| `passagen update [paper-id]` | 从最后成功状态继续单篇或全部论文。 |
| `passagen list [--status STATUS]` | 列出论文并按状态过滤。 |
| `passagen show <paper-id>` | 显示元数据和 artifact 路径。 |
| `passagen check` | 检测外部服务（GROBID、LLM、Crossref、arXiv）可达性；任一不可用则以退出码 1 结束。 |
| `passagen artifacts check` | 检查 artifact 路径、大小和 SHA-256。 |
| `passagen logs clean` | 将历史执行日志归档到 `logs/old/`。 |

SQLite 访问集中在 `passagen.storage`：SQLAlchemy 2.0 ORM 负责查询与短事务，内嵌
Alembic revision 负责 Schema 升级。`db init` 对新库创建完整 Schema；已有 Schema v1
会先经过结构和完整性检查，再原地登记 migration 版本，不会重建业务表。

所有命令都可以使用 `--help` 查看参数：

```bash
uv run passagen summarize --help
uv run passagen db backup --help
```

## 分阶段使用

只导入文件：

```bash
uv run passagen scan ./papers
```

查看 Paper ID：

```bash
uv run passagen list
```

逐阶段执行：

```bash
uv run passagen metadata <paper-id>
uv run passagen parse <paper-id> --parser pymupdf
uv run passagen summarize <paper-id>
uv run passagen outline <paper-id>
```

处理所有尚未达到最终状态的论文：

```bash
uv run passagen update
```

只处理一篇：

```bash
uv run passagen update <paper-id>
```

## 失败恢复与强制重建

阶段失败时 Paper 保持在最后一个成功状态。例如 Summary 失败后仍为 `parsed`，Outline 失败后仍为 `summarized`。

程序不会在一次执行中自动进行多次 retry。修复服务或配置后手动重试：

```bash
uv run passagen update <paper-id>
```

从 metadata 开始完整重建：

```bash
uv run passagen update <paper-id> --force
```

Summary Schema v2 与旧 Summary v1 不兼容，升级后应对已有论文执行 `--force`。

## 数据和产物

默认目录结构：

```text
data/
  passagen.db
  pdfs/<sha256-prefix>/<sha256>.pdf
  papers/<paper-id>/
    extracted.json
    summary.json
    summary.yaml
    outline.md
    outline.source.json
    summary/facts/

logs/<execution-id>/
  log.txt
  external/llm/<paper-id>/
```

`summary.json` 是规范化的 Structured Summary v2。`summary.yaml` 便于人工阅读。`outline.md` 只使用合法 Summary 中的事实，不再次读取 PDF。

执行期间终端逐行保留阶段进度，并显示 WARNING 及以上级别的日志（如 provider 降级、重试）；`--debug` 时终端显示全部日志。完整日志始终写入 `logs/<execution-id>/log.txt`。

## 备份与完整性检查

创建数据库备份：

```bash
uv run passagen db backup
uv run passagen db backup ./backups/passagen.db
```

默认备份路径为 `data/backups/passagen-<timestamp>.db`。该命令只备份 SQLite 数据库；迁移到其他机器时还需要复制完整 `data_dir`。

检查受管理文件：

```bash
uv run passagen artifacts check
```

检查内容包括：

- artifact 路径必须相对 `data_dir`。
- 路径不能逃逸数据目录。
- 文件必须存在。
- 文件大小必须匹配数据库记录。
- 存在 SHA-256 记录时必须匹配。

## 日志与诊断

每次 CLI 执行都会创建独立目录：

```text
logs/YYYYMMDD-HHMMSS-microseconds/
```

LLM 请求诊断保存：

- 渲染后的 Prompt 和 SHA-256。
- provider 和模型。
- 输入、输出及 reasoning token。
- `finish_reason`。
- 原始响应或错误。

日志不会保存 API key。归档历史日志：

```bash
uv run passagen logs clean
```

## 常见问题

### `PASSAGEN_API_KEY` 未设置

确认环境变量名称与 `providers.llm.api_key_env` 一致，并在启动 Passagen 的同一个 shell 中设置。

### LLM 健康检查成功但 Chat 请求失败

健康检查只访问 `/models`。还需要确认：

- `model` 是服务实际暴露的名称或 alias。
- `<base_url>/chat/completions` 可访问。
- API key 有调用该模型的权限。
- LiteLLM 已正确映射上游 provider。

### LLM 返回空响应

错误信息会包含 `finish_reason`、`output_tokens` 和 `reasoning_tokens`。如果 reasoning 消耗了全部输出预算，可以启用 `disable_thinking`；如果正常内容被截断，则提高输出 token 或缩小输入块。

### GROBID 不可用

检查：

```bash
curl http://localhost:8070/api/isalive
```

不使用 GROBID 时设置：

```yaml
pipeline:
  parsing:
    parser: pymupdf
```

### `no_text_layer`

PDF 可能是扫描件或没有可用文本层。首版不支持 OCR，需要先使用其他工具为 PDF 添加文本层。

### Schema 校验失败

Passagen 会尝试有限的 LLM 修复，并将原始响应和校验错误保存在执行日志中。持续失败时检查模型是否支持稳定 JSON 输出、输出 token 是否足够，以及自定义 Prompt 是否仍明确要求遵守 `$schema`。

## 开发检查

```bash
uv run pytest
uv run ruff check .
uv run basedpyright
uv run mypy
uv build
```

更多设计与运维细节见：

- [`docs/design.md`](docs/design.md)
- [`docs/architecture.md`](docs/architecture.md)
- [`docs/roadmap.md`](docs/roadmap.md)
- [`docs/operations.md`](docs/operations.md)

## 许可证

Passagen 仅按照 [GNU Affero General Public License v3.0](LICENSE)
发布，SPDX 标识为 `AGPL-3.0-only`。

运行时依赖 PyMuPDF 由其权利人按照 AGPLv3 或 Artifex 商业许可证双重授权。如需在不
遵守 PyMuPDF AGPL 条款的情况下使用它，需要向 Artifex 获取适用的商业许可证，或者先
将 PyMuPDF 替换为许可证兼容的实现。Artifex 的商业许可证不会免除 Passagen 自身的
AGPLv3 义务；闭源分发 Passagen 还需要获得 Passagen 全部相关权利人的单独授权。其他
第三方依赖继续适用各自的许可证。
