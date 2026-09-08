# CLI 运行指南

共享配置、provider 和 pipeline 字段见
Passagen Core 仓库的 docs/user/configuration.md。

## 全局参数

```bash
passagen --data-dir PATH --config FILE --debug <command>
```

| 参数 | 说明 |
|---|---|
| `--data-dir PATH` | 论文库目录；默认 `./data`。 |
| `--config FILE` | 配置文件；默认 `<data-dir>/passagen.yaml`。 |
| `--debug` | 在终端和 execution log 中提供更详细的运行信息。 |

全局参数必须放在子命令之前。

## 初始化与处理

```bash
passagen --data-dir ./data config check
passagen --data-dir ./data db init
passagen --data-dir ./data run ./papers
```

`run` 组合 scan 与 update。也可以分阶段执行：

```bash
passagen scan <directory>
passagen update [paper-id] [--force]
passagen metadata <paper-id> [--force]
passagen parse <paper-id> [--parser auto|grobid|pymupdf] [--force]
passagen abstract [paper-id] [--parser auto|grobid|pymupdf] [--force]
passagen summarize <paper-id> [--force]
passagen outline <paper-id> [--force]
```

`update` 从最后成功状态继续。`--force` 从对应命令负责的阶段重建；对 `update` 使用时从
Metadata 开始完整重建。

## 查询与组织

```bash
passagen list [--status STATUS]
passagen show <paper-id>
passagen collection synthesize <collection-id> [--format markdown|json] [--allow-partial] [--force]
passagen collection compare <collection-id> [--format markdown|json] [--allow-partial] [--force]
passagen collection --help
passagen tag --help
```

Collection 是有顺序的论文集合。Tag 是可独立组合和筛选的用户标签；它与 Summary 中生成的
keywords 不自动合并。

`collection synthesize` 输出完整 synthesis；`collection compare` 的 JSON 输出 comparison
matrix，Markdown 输出 Core 的完整 synthesis（其中包含 comparison）。两个命令默认要求全部论文
具有有效 Summary；`--allow-partial` 显式允许部分覆盖，`--force` 跳过未变化结果的复用。payload
写入 stdout，运行状态、warning 和错误写入 stderr。

## 维护

```bash
passagen check
passagen config check
passagen db status
passagen db backup [destination]
passagen artifacts check
passagen logs clean
```

跨机器迁移和恢复必须复制完整 data directory。共享规则见
Passagen Core 仓库的 docs/user/operations.md。

## 日志

每次 CLI 执行在当前工作目录的 `logs/<execution-id>/log.txt` 写入 execution log。`--debug`
增加详细日志；`logs clean` 将历史 execution log 移至 `logs/old/`。

日志会记录 stage、provider、token usage 和错误类别，但不记录 API key。

## 退出和重试

- 配置、数据库或目标参数错误会直接结束命令。
- 批量处理中的单篇失败不会取消其他论文。
- Abstract clean 失败属于 warning，不阻塞 Summary 和 Outline。
- Provider 认证、timeout 或无效响应修复后，可以重新执行 `update`。
