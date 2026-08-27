# OCS 架构历史

此目录保存双 Frontier 定案前的设计、迁移和性能分析。内容用于复核决策，不代表当前可部署 runtime 或配置格式。

> [!NOTE]
> 这里的文档保留历史背景和证据。当前实现、配置键和支持范围以仓库主文档为准，不应直接从归档内容复制旧 runtime 方案。

| 文档 | 历史范围 |
| --- | --- |
| [API 设计与迁移方案](./ocs-agent-api-migration-design.md) | Python/Go、HTTP/gRPC、monolith/split 的候选矩阵和 Draft 解读 |
| [HTTP/gRPC 性能对比](./2026-08-27-p4app-http-grpc-performance.md) | P4App 分层时延、dedicated-thread 根因分析和历史数值 |
| [HTTP 到 gRPC 迁移指南](./legacy-http-to-grpc-migration-guide.md) | 旧候选架构阶段的 wire API 迁移方法 |

当前架构只以 [OCS Agent 当前架构](../ocs-agent-architecture.md) 和 [OCS Draft / YANG 支持范围](../ocs-model-support.md) 为准。
