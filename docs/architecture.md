# Passagen CLI Architecture

`passagen-cli` 是 `passagen-core` 的终端适配器。它负责把 Typer 参数和本地运行环境转换为
Core service 调用，并使用 Rich 呈现结果；业务规则、数据库和外部 provider 均由 Core
拥有。

## Dependency Direction

```text
shell
  -> Typer commands
  -> CLI composition/runtime
  -> passagen-core public services
```

依赖只允许从 `passagen_cli` 指向 `passagen`。Core 不导入 `passagen_cli`，CLI 也不导入
`passagen.external` 等低层 adapter。

共享业务架构见
[`passagen-core/docs/architecture.md`](../../passagen-core/docs/architecture.md)。

## Package Responsibilities

| Path | Responsibility |
|---|---|
| `src/passagen_cli/app.py` | Root callback、命令注册和依赖组合 |
| `src/passagen_cli/commands/` | 参数转换、调用 Core、输出结果和退出码 |
| `src/passagen_cli/runtime.py` | Settings、provider health、LLM stats 和终端进度 |
| `src/passagen_cli/logging.py` | CLI execution log handler 与历史日志归档 |

CLI 不应包含：

- SQLAlchemy model、Session 或 migration。
- PDF、metadata、summary、outline、collection 或 tag 业务规则。
- HTTP provider adapter 或供应商响应解析。
- FastAPI schema、HTTP 状态码或 Web runtime。
- Core 业务模型的复制版本。

## Command Flow

每个命令遵循相同流程：

1. Typer 解析参数。
2. Root callback 合并 CLI 配置来源并创建 runtime state。
3. Command 从 state 获取经过校验的 Core settings 和依赖。
4. Command 调用一个 Core application entry point。
5. Rich 将返回值或进度事件呈现给用户。
6. 已知 Core 异常转换为稳定消息和退出码。

命令不能为了方便绕过公开 service 直接访问数据库。新增业务能力时，先在 Core 建立可由
普通 Python 调用的 contract，再增加 CLI 命令。

## Configuration Boundary

- CLI 决定 YAML 路径、当前工作目录、环境变量和命令行 override 的优先级。
- Provider、pipeline 和 artifact 等字段由 Core 配置模型校验。
- `--config` 和 `--data-dir` 是 CLI 输入，不应成为 Core 的全局状态。
- API key 仅从配置指定的环境变量读取，不写入输出、日志或数据库。

## Logging And Progress

CLI 配置 `passagen_cli.*` 和 `passagen.*` logger 的终端及文件 handler，因此一次命令可以
同时记录适配器和 Core 事件。Core 只发出结构化日志，不配置 handler。

终端进度、Rich 表格、warning、最终汇总和退出码属于 CLI。LLM prompt、raw response、
validation error 和可共享 run 记录属于 Core 诊断数据，不应由 command handler 自行保存。

## Tests

- Core 行为在 `passagen-core/tests` 覆盖。
- CLI 测试只覆盖参数、命令组合、输出、退出码和少量完整用户工作流。
- 架构测试确认 CLI wheel 不包含 `passagen` Core package，且 CLI 不导入 Core external
  adapter。
- 外部服务使用 fake 或固定响应，默认测试不访问网络。
