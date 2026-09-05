# Passagen CLI

Passagen CLI 是本地论文处理入口。它将 PDF 导入受管理论文库，补全元数据，解析全文，并通过
LLM 生成 cleaned Abstract、结构化英文 Summary 和技术 Outline。处理结果可继续通过
[Passagen Web](/PassagenWerk/passagen-web) 浏览和整理。

## 功能

- 扫描目录、导入 PDF、按内容去重。
- 运行完整 pipeline，或单独运行某个处理阶段。
- 从最后成功阶段恢复失败或中断的论文。
- 管理论文标签和有序集合。
- 检查 provider、数据库和 artifact，创建一致性备份。

## 环境要求

- Python 3.12+
- [uv](https://docs.astral.sh/uv/)
- DeepSeek API key，或兼容 OpenAI Chat Completions 的服务
- 可选 GROBID；不使用 GROBID 时可选择本地 PyMuPDF parser
- PDF 必须包含文本层；当前不提供 OCR

## 安装

源码安装要求 Core 与 CLI 位于同一父目录：

以下 URL 可替换为你使用的 GitHub、GitLab 或 Gitea 镜像地址：

```bash
git clone https://github.com/PassagenWerk/passagen-core.git
git clone https://github.com/PassagenWerk/passagen-cli.git
cd passagen-cli
uv sync --frozen
mkdir -p data
cp passagen.example.yaml data/passagen.yaml
```

## 快速开始

默认配置使用 DeepSeek `deepseek-flash-v4`：

```bash
export PASSAGEN_API_KEY=your-deepseek-api-key
uv run passagen config check
uv run passagen db init
uv run passagen run ./papers
```

查看论文和 artifact：

```bash
uv run passagen list
uv run passagen show <paper-id>
```

`run` 会先扫描目录，再处理所有未完成论文。重复运行时，相同 PDF 和已经完成的阶段会被跳过。

## 配置

CLI 默认读取 `<data-dir>/passagen.yaml`，默认 data directory 是 `./data`。可以覆盖路径：

```bash
uv run passagen --data-dir /path/to/library --config /path/to/passagen.yaml <command>
```

默认 LLM 配置：

```yaml
providers:
  llm:
    base_url: https://api.deepseek.com/v1
    model: deepseek-flash-v4
    api_key_env: PASSAGEN_API_KEY
```

没有 GROBID 时使用本地 parser：

```yaml
pipeline:
  parsing:
    parser: pymupdf
```

完整字段、GROBID 启动方式、DeepSeek、metadata provider 和 pipeline 参数由 Core 统一定义，
见 Passagen Core 仓库的 docs/user/configuration.md（[Passagen Core](/PassagenWerk/passagen-core)）。

## 常用命令

以下示例使用已安装的 `passagen`；在源码环境中在命令前添加 `uv run`。

```bash
passagen scan <directory>
passagen run <directory>
passagen update [paper-id] [--force]
passagen metadata <paper-id> [--force]
passagen parse <paper-id> [--parser auto|grobid|pymupdf] [--force]
passagen abstract [paper-id] [--parser auto|grobid|pymupdf] [--force]
passagen summarize <paper-id> [--force]
passagen outline <paper-id> [--force]
passagen collection --help
passagen tag --help
```

Abstract clean 是显式但非阻塞的阶段。`passagen abstract` 会在缺少原文时尝试从 PDF 提取，
然后生成或复用 cleaned artifact；原始 Author Abstract 始终保留。

维护命令：

```bash
passagen check
passagen config check
passagen db status
passagen db backup
passagen artifacts check
passagen logs clean
```

## 故障排查

### LLM API key 未设置

在执行 Passagen 的同一个 shell 中设置 `PASSAGEN_API_KEY`。服务进程不会读取启动后才添加的
环境变量。

### LLM 请求失败

运行 `passagen check`，并检查 `base_url`、`model`、API key、网络和模型是否支持 JSON object
response format。

### GROBID 不可用

检查 `http://localhost:8070/api/isalive`。无法运行 GROBID 时，将 parser 改为 `pymupdf`。

### `no_text_layer`

PDF 没有可解析文本层。请先使用外部 OCR 工具生成带文本层的 PDF，再重新导入。

### 数据库或 artifact 错误

先停止所有使用该 data directory 的进程。恢复时复制完整 data directory，不要只替换
`passagen.db`。详细步骤见[运行与恢复](docs/user/operations.md)。

## 文档

- [CLI 运行与命令](docs/user/operations.md)
- [CLI 架构](docs/development/architecture.md)
- [Passagen Core](/PassagenWerk/passagen-core) 的 docs/user/configuration.md
- [Roadmap](docs/roadmap/README.md)

## 许可证

[GNU Affero General Public License v3.0](LICENSE)，SPDX 标识为 `AGPL-3.0-only`。
