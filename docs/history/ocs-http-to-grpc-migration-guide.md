# OCS HTTP 到 gRPC/gNMI 迁移指南

> 历史迁移快照：其中旧 runtime 字段和临时开关已经删除。当前迁移表见 [OCS Agent 当前架构](../ocs-agent-architecture.md#8-默认部署与迁移)。

本文面向现有使用 `/ocs_mapping` 和 `/ocs_mode` 的 controller。P4app HTTP compatibility adapter 已冻结并默认关闭；Tofino 只部署 gRPC/gNMI，并保持相同 wire contract，只替换 OCS Agent 内的 southbound backend。

完整组件边界、数据模型和 Draft 支持矩阵见 [OCS Agent API 设计与迁移方案](ocs-agent-api-migration-design.md)，性能基线见 [OCS 分层性能评估](ocs-http-grpc-performance.md)。

## 1. 部署边界

任意网络可达 host 都可以运行 Go/Python client 或可选的多 OCS controller。当前低时延路径是：

```text
client / optional controller
  -> one gRPC request
OCS-local Agent Core
  -> local call or UDS gRPC
Python Device Worker / backend
  -> P4Runtime or BF Runtime gRPC
BMv2 or Tofino
```

`P4DeviceWorker` 和 `P4appBackend` 都属于 OCS Agent。当前 runtime：

| Runtime | Agent Core | Device owner | 用途 |
| --- | --- | --- | --- |
| `python-monolith` | Python 3.5 | 同进程 P4app/BFRT backend | P4app 默认、Tofino 控制组 |
| `python-split` | Python 3.5 | Python Device Worker | P4app 历史对照 |
| `go-split` | Go 1.25 | 同一个 Python Device Worker | Tofino 默认候选 |

client 语言、NBI 协议和 Agent runtime 是三个独立变量。controller 从 HTTP 迁移到 gRPC 时，不需要同时切换 Agent 实现语言。

## 2. 新接口如何分工

- gNMI：Draft 模型对象，包括端口 inventory、逐条具名 connection、稀疏连接集和整棵 connection subtree replace；
- `OcsOperations`：control lease、`pi` batch、Full/Delta、transport、mode、revision、恢复和 timing；
- HTTP：冻结的 P4app 迁移兼容层，调用同一个 Agent Core，不再持有独立 mapping、锁或 revision。

默认监听来自 instance JSON：HTTP 关闭，gRPC `:9339`。只有历史 P4app 复现才显式打开 HTTP `:5000`。

## 3. 写入控制：必须先获取 lease

所有 HTTP、OcsOperations 和 gNMI 写入都必须满足两个前置条件：

1. 持有唯一 active control lease；
2. 携带当前 `expected_revision`。

默认 lease 为 30 秒，controller 应约每 10 秒续租。Agent 重启后旧 token 全部失效。HTTP adapter 不会为了兼容旧 client 自动获取 lease。

错误语义：

| 条件 | gRPC | HTTP |
| --- | --- | --- |
| 另一个 holder 已持 lease | `RESOURCE_EXHAUSTED` | 429 |
| token 缺失、错误或过期 | `FAILED_PRECONDITION` | 409 |
| revision 已过期 | `ABORTED` | 409 |
| transaction 已取得 commit slot 后 lease 到期 | 继续完成 | 继续完成 |

### 3.1 gRPC control session

```python
import grpc

from api.proto import ocs_operations_pb2
from api.proto import ocs_operations_pb2_grpc

channel = grpc.insecure_channel('127.0.0.1:9339')
operations = ocs_operations_pb2_grpc.OcsOperationsStub(channel)

lease = operations.AcquireControl(
    ocs_operations_pb2.AcquireControlRequest(
        client_id='cluster-controller'))

lease_token = lease.lease_token
revision = lease.revision
write_metadata = (('x-ocs-control-lease', lease_token),)

# 长期运行的 controller 应在 lease 到期前续租；默认建议每 10 秒一次。
renewed = operations.RenewControl(
    ocs_operations_pb2.RenewControlRequest(
        lease_token=lease_token),
)
lease_token = renewed.lease_token

# 所有写完成后显式释放。
operations.ReleaseControl(
    ocs_operations_pb2.ReleaseControlRequest(
        lease_token=lease_token))
channel.close()
```

续租不改变 revision。每次成功写之后必须用响应中的新 revision 更新本地 session；不要为了每次写再增加一个 `GetRuntime` 往返。

### 3.2 HTTP control session

```bash
curl -sS -X POST http://127.0.0.1:5000/ocs_control/acquire \
  -H 'Content-Type: application/json' \
  -d '{"client_id":"cluster-controller"}'
```

响应包含 `lease_token`、`expires_unix_ns` 和 `revision`。后续写必须携带：

```text
X-OCS-Control-Lease: <lease_token>
X-OCS-Expected-Revision: <revision>
```

旧 client 若不增加这两个 header，现在会明确失败，不会进入 legacy unsafe mode。

## 4. 调用替换表

| 旧调用 | 推荐替换 | 语义差异 |
| --- | --- | --- |
| `GET /ocs_mapping` | `OcsOperations.GetPermutation` | 仅完整 perfect matching 有结果；通用状态改用 gNMI Get |
| `POST /ocs_mapping` | `OcsOperations.ApplyBatch(permutation)` | 必须携带 lease/revision；显式选择 Full/Delta 和 transport |
| `GET /ocs_mode` | `OcsOperations.GetRuntime` | 同时返回 revision、connection set、cache 和 backend capability |
| `POST /ocs_mode` | `OcsOperations.SetMode` | 必须携带 lease/revision；返回结构化 timing |
| 无 | gNMI `Set.replace` 单个 connection | 新增逐条具名连接，可形成稀疏状态 |
| 无 | gNMI replace 整个 connections subtree | 使用 Draft 对象表达完整目标集合 |
| 无 | `RecoverDeviceState(REAPPLY_DESIRED)` | drift/unknown 后显式重写 desired state |

## 5. `pi` batch 迁移

下面示例在一次 lease session 内下发完整 `pi`。`expected_revision` 是必填项，不是可选优化。

```python
import grpc

from api.proto import ocs_operations_pb2
from api.proto import ocs_operations_pb2_grpc

channel = grpc.insecure_channel('127.0.0.1:9339')
client = ocs_operations_pb2_grpc.OcsOperationsStub(channel)
lease = client.AcquireControl(
    ocs_operations_pb2.AcquireControlRequest(client_id='pi-controller'))

token = lease.lease_token
revision = lease.revision
metadata = (('x-ocs-control-lease', token),)

request = ocs_operations_pb2.ApplyBatchRequest(
    strategy=ocs_operations_pb2.EXECUTION_STRATEGY_DELTA,
    transport=ocs_operations_pb2.TRANSPORT_NATIVE_BATCH,
    has_expected_revision=True,
    expected_revision=revision,
)
request.permutation.pi.extend([2, 1, 6, 5, 4, 3, 8, 7])
reply = client.ApplyBatch(request, metadata=metadata)
revision = reply.state.revision

print(reply.result, revision)
print(reply.timing.delete_entries, reply.timing.insert_entries)

client.ReleaseControl(
    ocs_operations_pb2.ReleaseControlRequest(lease_token=token))
channel.close()
```

策略选择：

- `FULL`：删除全部 active entries，再安装完整目标；最接近旧接口的 break-before-make；
- `DELTA`：只删除/安装变化项并保留 unchanged entries；
- `SEQUENTIAL`：所有 backend 必须支持的逐 entry 基线；
- `NATIVE_BATCH`：仅在 runtime capability 声明支持时使用，不支持时明确失败。

当前 P4app benchmark 中的 Full/Delta 都是完整 8 端口 `pi` 从一个合法排列切到另一个，不是只改一个 connection。

### 5.1 HTTP 兼容写法

```bash
curl -sS -X POST http://127.0.0.1:5000/ocs_mapping \
  -H 'Content-Type: application/json' \
  -H "X-OCS-Control-Lease: $OCS_LEASE_TOKEN" \
  -H "X-OCS-Expected-Revision: $OCS_REVISION" \
  -d '{
    "new_pi":[2,1,6,5,4,3,8,7],
    "strategy":"DELTA",
    "transport":"NATIVE_BATCH",
    "delay_us":0
  }'
```

成功响应中的 `revision` 必须作为下一次写的 expected revision。

## 6. gNMI 逐条 connection

单条 replace 使用带 `connection-name` key 的完整 list-entry path。gNMI metadata 同时携带 lease 和 revision：

```python
import json
import grpc

from api.proto import gnmi_pb2
from api.proto import gnmi_pb2_grpc
from api.proto import ocs_operations_pb2
from api.proto import ocs_operations_pb2_grpc


def connection_path(name):
    path = gnmi_pb2.Path()
    root = path.elem.add()
    root.name = 'oc-optical-switch-connections:optical-switch-connections'
    item = path.elem.add()
    item.name = 'port-connection'
    item.key['connection-name'] = name
    return path


channel = grpc.insecure_channel('127.0.0.1:9339')
operations = ocs_operations_pb2_grpc.OcsOperationsStub(channel)
gnmi = gnmi_pb2_grpc.gNMIStub(channel)

lease = operations.AcquireControl(
    ocs_operations_pb2.AcquireControlRequest(client_id='gnmi-controller'))
token = lease.lease_token
revision = lease.revision

request = gnmi_pb2.SetRequest()
replace = request.replace.add()
replace.path.CopyFrom(connection_path('controller-link-1'))
replace.val.json_ietf_val = json.dumps({
    'connection-name': 'controller-link-1',
    'config': {
        'connection-name': 'controller-link-1',
        'bidirectional': True,
        'near-port-name': 'port-1',
        'far-port-name': 'port-3',
    },
}).encode('utf-8')

response = gnmi.Set(request, metadata=(
    ('x-ocs-control-lease', token),
    ('x-ocs-expected-revision', str(revision)),
))
operation = json.loads(response.message.message)
revision = operation['revision']

operations.ReleaseControl(
    ocs_operations_pb2.ReleaseControlRequest(lease_token=token))
channel.close()
```

如果任一端口已由另一条 connection 占用，Agent 返回 `FAILED_PRECONDITION` 并包含冲突 port 和 connection；不会隐式拆除旧 connection。

删除使用相同 path：

```python
request = gnmi_pb2.SetRequest()
request.delete.add().CopyFrom(connection_path('controller-link-1'))
response = gnmi.Set(request, metadata=(
    ('x-ocs-control-lease', token),
    ('x-ocs-expected-revision', str(revision)),
))
revision = json.loads(response.message.message)['revision']
```

同一个 `SetRequest` 中的多个 delete/replace 属于同一个 Agent commit。当前 gNMI 单连接操作固定使用 Delta + Sequential；leaf-level update、`union_replace` 和 `Subscribe` 返回 `UNIMPLEMENTED`。

## 7. 整个具名连接集替换

对 `/oc-optical-switch-connections:optical-switch-connections` 执行 `Set.replace`，JSON_IETF value 形如：

```json
{
  "port-connection": [
    {
      "connection-name": "link-a",
      "config": {
        "connection-name": "link-a",
        "bidirectional": true,
        "near-port-name": "port-1",
        "far-port-name": "port-2"
      }
    },
    {
      "connection-name": "link-b",
      "config": {
        "connection-name": "link-b",
        "bidirectional": true,
        "near-port-name": "port-5",
        "far-port-name": "port-8"
      }
    }
  ]
}
```

该对象可以是稀疏连接集。若 controller 已持有完整合法 `pi`，优先使用 `ApplyBatch(permutation)`，因为它提供 perfect-matching 强校验、Full/Delta、Native Batch 和更完整 timing。

gNMI subtree replace 表示目标集合完整替换，当前设备执行使用 Delta + Sequential。需要显式 Full break-before-make 时，使用 `ApplyBatch(connection_set, strategy=FULL)`。

## 8. 状态、cache 和恢复

`GetRuntime` 返回 desired connection set、revision、backend capability 和：

- consistency mode：`CACHED_ACK`、`CACHED_SYNC` 或 `STRICT_DEVICE`；
- cache status：`READY`、`DRIFTED`、`UNKNOWN`；
- generation、最近验证/对账时间和 drift count。

`CACHED_ACK` 正常写不做 device pre-read，收到 southbound ACK 后提交 cache；`CACHED_SYNC` 额外做同步 post-write software readback；`STRICT_DEVICE` 还会在写前读设备。默认每 30 秒 reconcile。外部修改不会被静默采用。启动 `REQUIRE_MATCH` 不一致时，普通写会被阻止直到显式 recovery。

`DRIFTED` 或 `UNKNOWN` 时，持 lease 的 controller 可显式重写 desired state：

```python
reply = operations.RecoverDeviceState(
    ocs_operations_pb2.RecoverDeviceStateRequest(
        mode=ocs_operations_pb2.RECOVERY_MODE_REAPPLY_DESIRED,
        has_expected_revision=True,
        expected_revision=revision,
    ),
    metadata=(('x-ocs-control-lease', token),),
)
revision = reply.state.revision
```

HTTP 对应 `POST /ocs_recover`，同样要求两个 control header。该 API 处理运行时 drift，不等同于 Draft 中尚未实现的跨重启 connection recovery。

## 9. 查询和稀疏状态

gNMI `Capabilities` 只声明本地支持的 Draft profile。`Get` 当前支持 `JSON_IETF` 和 `ALL`、`CONFIG`、`STATE`、`OPERATIONAL`。

根 Get 返回：

- `oc-optical-switch:optical-switch`；
- `openconfig-platform:components`；
- `oc-optical-switch-connections:optical-switch-connections`。

`TUNED`、`CONNECTED` 和 `connected=true` 来自 Agent 的 ACK/读回 table snapshot 推导，不是光功率、MEMS、BER 或物理链路测量。`write_verification` 与 `last_verified_unix_ns` 用于区分当前边界。

逐条 gNMI 操作可形成稀疏状态，此时：

- `GetPermutation` 返回 `FAILED_PRECONDITION`；
- `GET /ocs_mapping` 返回 409；
- gNMI Get 和 `GetRuntime` 仍正常返回具名 connection set；
- 用合法完整 `pi` 调用 `ApplyBatch`/`POST /ocs_mapping` 可恢复 perfect matching。

## 10. 推荐迁移顺序

1. 给现有 controller 增加 lease session、revision 串行推进和结构化错误处理；写入暂时仍走 HTTP。
2. 确认旧 HTTP 写已经携带两个 control header，且不在每次写前额外调用 GetRuntime。
3. 把完整 `pi` 写替换为 `OcsOperations.ApplyBatch`，保持 Agent runtime 为 `python-monolith`，对比最终 state、entry 数和 timing。
4. 需要稀疏连接的调用迁移到 gNMI replace/delete。
5. 分别评估 protocol 和 Agent runtime；不要在一次变更中同时切 HTTP -> gRPC 和 monolith -> split。
6. 所有 controller 等价性验证通过后，把 `enable_rest_api` 设为 `false`；当前默认已经如此。
7. 删除 HTTP adapter 是后续独立归档变更；BFRT/Tofino 已复用相同 contract。

client 可提前执行端口名、JSON/protobuf schema、`pi` 对称性等无状态检查，以尽早拒绝本地错误。但 Agent 必须再次执行权威的端口占用、lease、revision、cache generation 和设备一致性检查；远端 client 的状态副本不能替代这些检查。

## 11. Runtime 切换与回滚

持久配置：

```json
{
  "agent_runtime": "go-split",
  "device_worker": {
    "consistency_mode": "CACHED_ACK",
    "southbound_execution": "DEDICATED_THREAD"
  },
  "startup_policy": "REQUIRE_MATCH"
}
```

测试时可用环境变量覆盖：

```bash
P4APP_CONTAINER_ARGS='-e OCS_AGENT_RUNTIME=go-split -e OCS_CONSISTENCY_MODE=CACHED_SYNC' make run
```

三个 runtime 保持相同 gRPC contract、YAML、logical port 和 revision 语义；P4app 可显式启用冻结的 HTTP adapter。回滚 runtime 只需重启并恢复 `python-monolith`；controller 不需要改 payload，但 Agent 重启后必须重新 AcquireControl。

切换前至少验证：

- HTTP、gNMI、OcsOperations 最终状态一致；
- 缺失/过期 lease 和 stale revision 在设备写前失败；
- Worker 断连返回 `UNAVAILABLE`，revision 不推进，cache 进入未知状态；
- drift 能被发现，且只能通过显式 recovery 恢复；
- Full/Delta、Sequential/Native Batch 结果等价；
- 使用真实 BMv2/P4Runtime，而不是 fake backend 性能数据。

## 12. 本阶段明确不支持

本阶段不支持 Draft reboot connection recovery 配置、port admin/alias/description 写入、switch-side、错误 counters、异步 connection 状态机、multicast、SOA、gNMI Subscribe 和 leaf-level patch。相关写入必须明确失败，不能忽略后返回成功。
