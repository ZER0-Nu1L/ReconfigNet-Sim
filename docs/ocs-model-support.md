# OCS Draft / YANG 支持范围

- 状态：当前 profile contract
- 日期：2026-08-27

本表保留 API Proposal draft 的基本实体和命名，但只声明真实实现或明确推导的能力。未实现字段必须返回明确错误或不出现在 capability 中，不能静默接受。

状态含义：

- ✅ `SUPPORTED`：存在真实读写或执行逻辑；
- 🧮 `DERIVED`：由 Agent desired state、southbound ACK 或最近一次设备读回推导；
- 🗓️ `PLANNED`：设计允许后续增加，但当前请求会拒绝；
- 🚫 `UNSUPPORTED`：当前 profile 明确不支持；
- ➖ `OUT_OF_SCOPE`：不属于当前 P4App/Tofino 模拟 OCS 范围。

| Capability ID | Draft area | Status | 当前真实性边界 |
| --- | --- | --- | --- |
| `optical-switch-state` | `optical-switch` | ✅ `SUPPORTED` | Agent runtime snapshot 和受支持配置 |
| `connection-recovery` | `optical-switch/port-connection-recovery` | 🚫 `UNSUPPORTED` | 本地 startup/recover 安全机制不冒充 Draft 持久化恢复能力 |
| `platform-port-identity` | `components/component` | ✅ `SUPPORTED` | YAML logical port inventory；Tofino dev_port 映射留在 backend 配置 |
| `port-enabled` | `components/component/ocp-ocs-port/enabled` | 🧮 `DERIVED` | 当前固定为 true，不支持写 admin state |
| `port-alias-description` | `components/component/ocp-ocs-port/config` | 🗓️ `PLANNED` | 当前不持久化 alias/description |
| `switch-side` | `components/component/ocp-ocs-port/state/switch-side` | 🚫 `UNSUPPORTED` | 当前 backend 没有标准化可信来源 |
| `port-status` | `components/component/ocp-ocs-port/state/status` | 🧮 `DERIVED` | 来自 desired state、ACK/readback 和 cache status；不是光学测量 |
| `port-peer-connected` | `components/component/ocp-ocs-port/state/connection` | 🧮 `DERIVED` | 从当前 ConnectionSet 和 verification 边界推导 |
| `error-counters` | `components/component/ocp-ocs-port/state/counters` | 🚫 `UNSUPPORTED` | 不用流量 counter 冒充建连错误 counter |
| `point-to-point-connections` | `optical-switch-connections/port-connection` | ✅ `SUPPORTED` | 逐条 CRUD、稀疏连接集和具名连接 |
| `connection-rejection-reason` | `operation-errors` | ✅ `SUPPORTED` | 结构化端口冲突、revision、lease 和 backend 错误 |
| `asynchronous-connection-state` | `port-connection/state/status` | 🗓️ `PLANNED` | 当前写路径同步到所选 consistency boundary |
| `connection-state` | `port-connection/state` | 🧮 `DERIVED` | CONNECTED/FAILED/UNKNOWN 等来自 Agent 和 backend 状态 |
| `multicast-connections` | `multicast-port-connection` | 🚫 `UNSUPPORTED` | 当前 pipeline/backend 只支持点到点 |
| `soa-amplifier` | `openconfig-ocs-soas` | ➖ `OUT_OF_SCOPE` | 当前没有光功率、gain、current 数据源或 actuator |
| `full-connection-set-replace` | `optical-switch-connections` | ✅ `SUPPORTED` | 完整具名 connection set replace；HTTP profile 用 `pi` batch |
| `permutation-batch` | `vendor-extension/pi` | ✅ `SUPPORTED` | 完整、无自环、双向 perfect matching，支持 FULL/DELTA |

## Profile 差异

两个 deployment profile 使用同一 YAML model 和同一 capability 含义：

- `python-monolith-http-direct` 通过 HTTP 暴露 operational subset，不声明 gNMI wire service；
- `go-split-grpc` 通过 gNMI `Capabilities/Get/Set` 和 `OcsOperations` 暴露 typed NBI；
- gNMI `Subscribe` 当前返回 `UNIMPLEMENTED`；
- P4App 与 Tofino 只改变状态来源和 southbound adapter，不改变 capability ID。

机器可读事实来源：

- `agent/configs/p4app/capabilities.yaml`；
- `agent/configs/tofino/capabilities.yaml`。

测试会校验 capability YAML 与本表的 ID、状态和 icon 一致。
