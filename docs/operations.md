# Passagen CLI Installation And Operations

本文档说明 CLI 的安装、配置和命令使用。数据库、artifact、provider 和 pipeline 的稳定
运行语义见
[`passagen-core/docs/operations.md`](../../passagen-core/docs/operations.md)。

## Source Installation

开发环境需要相邻的 Core 和 CLI checkout：

```text
Passagen/
  passagen-core/
  passagen-cli/
  passagen-web/
```

```bash
cd passagen-cli
uv sync --frozen
mkdir -p data
cp passagen.example.yaml data/passagen.yaml
uv run passagen config check
uv run passagen db init
```

`pyproject.toml` 在开发环境中通过 editable source 使用 `../passagen-core`。发布安装时，
`passagen-cli` 通过正常 package dependency 安装兼容版本的 `passagen-core`。

## Configuration

CLI 默认读取数据目录下的 `passagen.yaml`（即 `<data-dir>/passagen.yaml`，默认
`./data/passagen.yaml`）。`data_dir` 只能由 `--data-dir` 命令行参数指定，不能出现在
配置文件或环境变量中。入口支持：

```bash
passagen --config <path> <command>
passagen --data-dir <path> <command>
passagen --debug <command>
```

配置也可以通过嵌套环境变量覆盖。LLM API key 的变量名由
`providers.llm.api_key_env` 指定，例如：

```bash
export PASSAGEN_API_KEY=your-key
```

API key 不会出现在 `config check`、execution log、LLM diagnostics 或数据库中。

## Processing Commands

```bash
passagen scan <directory>
passagen run <directory>
passagen update [paper-id] [--force]
passagen metadata <paper-id> [--force]
passagen parse <paper-id> [--parser auto|grobid|pymupdf] [--force]
passagen backfill-abstracts [paper-id] [--parser auto|grobid|pymupdf] [--force]
passagen fix-abstracts [paper-id] [--force]
passagen summarize <paper-id> [--force]
passagen outline <paper-id> [--force]
```

失败后再次执行 `update` 会从最后成功阶段继续；`--force` 从 metadata 开始重建。具体状态、
缓存和 artifact 规则由 Core 定义。

`backfill-abstracts` 只从受管理 PDF 提取 canonical Abstract。省略 Paper ID 时处理全部
缺少 Abstract 的论文；它不改变 Paper 状态、不重建 `extracted.json`、Summary 或 Outline，
也不调用 LLM。`--force` 可以刷新 parser 来源的 Abstract，但不会覆盖用户编辑值。

默认 update pipeline 会在全文解析后执行 Abstract fixing。原始 Author Abstract 保留在
Paper metadata 中，清洗文本写入 `abstract_cleaned_json` artifact；结果按原文、prompt 和模型
缓存，校验失败只产生 warning，不阻塞 Summary 和 Outline。`fix-abstracts` 可为历史论文单独
生成这些 artifact，省略 Paper ID 时处理所有已有 Abstract 的论文。

## Library Commands

```bash
passagen list [--status STATUS]
passagen show <paper-id>
passagen collection create <name>
passagen collection list
passagen collection add <collection-id> <paper-id>
passagen collection remove <collection-id> <paper-id>
passagen collection rename <collection-id> <new-name>
passagen collection delete <collection-id>
passagen tag create <name>
passagen tag list
passagen tag add <tag-id> <paper-id>
passagen tag remove <tag-id> <paper-id>
passagen tag rename <tag-id> <new-name>
passagen tag delete <tag-id>
```

Collection 和 tag 的详细命令 contract 见
[`roadmap-collection-and-tags.md`](roadmap-collection-and-tags.md)。

## Maintenance Commands

```bash
passagen config check
passagen check
passagen db init
passagen db status
passagen db backup [destination]
passagen artifacts check
passagen logs clean
```

迁移或手工处理数据前先执行 `db backup`。跨机器迁移必须复制完整 `data_dir`，然后运行
`artifacts check`，不能只复制 SQLite 文件。

## Execution Logs

每次命令在当前工作目录的 `logs/<execution-id>/log.txt` 写入 CLI execution log。默认终端
只显示 warning 及以上日志，`--debug` 显示详细日志。`passagen logs clean` 将历史目录归档到
`logs/old/`。

Execution log 只用于宿主进程运行信息。可复用 artifact、LLM call 元数据以及后续统一的
prompt/raw response diagnostics 由 Core 管理。

## Common Errors

`Database is not initialized`

执行 `passagen db init`。该命令同时升级 Core 提供的 forward migrations。

`environment variable PASSAGEN_API_KEY is not set`

在启动 Passagen 的同一个 shell 中设置配置指定的变量。

`Provider llm is unavailable`

检查 `base_url`、认证、模型名称和 `/models` route。

`Provider grobid is unavailable`

检查 GROBID `/api/isalive`，或将 parser 设为 `pymupdf`。

`no_text_layer`

PDF 没有可用文本层；当前版本不提供 OCR。

`artifact file is missing` 或 hash 不匹配

恢复完整 `data_dir` 备份，不要直接编辑受管理文件。
