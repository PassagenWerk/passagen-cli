# Passagen CLI Code Style

CLI 遵循 Passagen Core 仓库的 docs/development/code-style.md，并补充以下
适配器约束。

## Commands

- Command handler 只解析参数、构造依赖、调用 Core 和呈现结果。
- 共享业务必须先成为 Core 的公开 service，不在多个 command 中复制。
- 使用 `Annotated` 声明 Typer argument 和 option，并提供简洁英文 help。
- 预期 Core 异常转换为稳定退出码；不捕获裸 `Exception` 隐藏程序错误。
- 非交互输出保持可重定向；Rich markup 不能改变实际数据文本。

## Logging

- 使用 `logging.getLogger(__name__)`，不直接 `print` 运行诊断。
- 用户表格、进度和错误通过共享 Rich Console 呈现。
- 不在 command 中保存完整 LLM prompt、response 或 API key。
- Core 日志和 CLI 日志由 CLI composition root 统一安装 handler。

## Tests

- 使用 `CliRunner` 覆盖 help、参数、输出和退出码。
- Core 业务边界通过真实 service 或轻量 fake 验证，不复制 Core 单元测试。
- 测试不得访问真实 provider。
- 提交前运行：

```bash
uv run ruff format --check .
uv run ruff check .
uv run basedpyright
uv run mypy
uv run pytest
uv build
```
