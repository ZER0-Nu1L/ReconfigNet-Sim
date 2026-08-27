# OCS 仿真原理与边界

[English](./ocs-simulation-principles-and-boundaries.md)

- 状态：公开项目边界
- 日期：2026-08-27
- 适用 target：BMv2/P4App 与 Tofino/BFRT

## 文档目的

ReconfigNet-Sim 在真实光交换机尚不可用或尚未完成系统集成前，提供一套可以运行的 OCS 控制与集成近似模型。它用于提早暴露调度器、控制器、端侧和部署问题，同时确保每个实验结论都能追溯到可编程包交换机真正能够复现的行为。

只有明确声明边界，这个模型才有意义。它既不是虚拟光学设备，也不能证明包交换机行为与真实 OCS 在物理上等价。

## 为什么以 P4 作为近似边界

A P4 switch provides a controllable approximation boundary within which a large class of OCS system-integration questions can be investigated before—and alongside—real hardware。

BMv2/P4App 提供成本低、可复现、编辑—构建—运行周期短的软件 target，适合在硬件 target 可用前开发和测试 controller、端侧、部署及失败处理流程。对于依赖真实 RDMA NIC 交互、PHY 行为、链路训练和硬件时序的实验，它的交互保真度可能低于物理部署。

Tofino 提供互补的硬件层次，包含可编程 packet-processing pipeline 以及 BF-SDE/BFRT 开发环境。它能够暴露软件 target 难以体现的硬件 target 和部署问题，但本质上仍是电交换机而不是光学设备。更完整的开放 P4 开发环境见 [Open P4 Studio](https://github.com/p4lang/open-p4studio)。

这两个 target 是互补的验证层次。从 BMv2/P4App 迁移到 Tofino 可以提高对可编程交换机行为的信心，但任何一个 target 都不能单独验证光学机制、物理链路恢复或 transceiver 行为。

> [!NOTE]
> P4 是可控的近似边界，不是物理等价边界。BMv2/P4App 适合快速迭代，Tofino 适合检验硬件和部署问题；光学行为仍需单独验证。

## 逻辑 OCS 抽象

真实 OCS 改变端口之间的物理连通关系。它的核心抽象是一组 1:1 cross-connect，不会根据 Ethernet、IP 或传输层包头选择光路。

ReconfigNet-Sim 保留其中的逻辑部分：

- 一组稳定、具名的 logical port；
- port 之间具名的双向 connection；
- 允许部分 port 空闲的 `ConnectionSet`；
- 表示完整、无自环、对称 matching 的严格 `pi`；
- 单连接、DELTA 和 FULL 更新；
- break-before-make、合法性检查、revision 和 rollback。

所有受支持 backend 共享同一逻辑模型。设备专用的 P4 table name、P4Runtime identifier、BFRT object 和物理 `dev_port` 不进入 northbound connection model。

## Packet-level 实现

电交换机不能改变物理光路。当前 P4 pipeline 通过两个阶段近似其结果：

1. IPv4/MAC 转发状态根据数据包目的端确定候选出口。
2. 以 ingress 和候选 egress 为 key 的 OCS 许可表，只允许属于当前 connection set 的有向 port pair 通过。

因此，一条双向逻辑 connection 会转换为两条有向许可表项。修改这张许可表，就在电链路持续物理在线的情况下模拟 active port mapping 发生变化。

这个实现不是协议透明的。Pipeline 会解析 IPv4，可能重写 Ethernet 地址，递减 IPv4 TTL 并重新计算 IPv4 checksum。ARP 不属于当前 OCS abstraction 的转发范围，因此实验通常需要提前建立端侧邻居状态。

## 重构时序模型

发生变化的表项采用 break-before-make：

1. 删除旧的有向许可表项；
2. 等待请求指定的 `delay_us`；
3. 安装目标许可表项；
4. 等待所选 ACK 或 readback 边界。

`delay_us` 是请求指定的控制面 gap，不直接测量以下内容：

- 光交换时间；
- packet blackout 时长；
- 物理 cross-connect 完成；
- 链路重新捕获或端侧恢复；
- 新路径上第一个数据包到达。

Host scheduling、RPC 执行和 backend 调用可能让实际观测间隔大于请求值。如果实验关心 data-plane blackout，必须单独测量。

> [!WARNING]
> `delay_us` 是控制面的请求值，不是光交换时间或 packet blackout 的测量值。不能把它直接与真实 OCS 的重构时长比较。

## 状态与真实性边界

项目区分四类状态：

| 边界 | 含义 | 不能证明的内容 |
| --- | --- | --- |
| Desired state | Agent 已接受的 connection set | 设备下发已经完成 |
| Backend acknowledgement | P4Runtime、BFRT 或未来 backend 已接受操作 | 数据包已经使用新路径 |
| Software/hardware readback | 受管表项与目标一致 | 光功率、BER 或端侧已经就绪 |
| Physical observation | 外部对链路、数据包或光硬件的测量 | 当前 emulator 本身不提供这些事实 |

Agent 中的 `CONNECTED`、`TUNED`、peer connectivity 和 port status 来自前三类边界的推导，不会被描述成光学遥测。

> [!IMPORTANT]
> Desired state、backend acknowledgement 和 readback 属于不同事实边界的软件或设备状态。它们都不能单独证明光功率、BER、物理链路就绪或新路径上的数据包已经到达。

## 模型之外的物理与系统行为

ReconfigNet-Sim 不复现：

- link down/up 信号和物理链路训练；
- NIC、PHY、firmware 或 driver 初始化；
- transceiver tuning 或波长行为；
- 光功率、损耗、串扰、BER 或信号完整性；
- MEMS 或硅光器件状态；
- 硬件特定的异步 connection state machine；
- 所有数据面路径瞬时或原子切换。

表项变化期间，电链路保持在线。这可能有意绕过直连物理拓扑中的主要开销。在相关集成工作观测到的一个 Cavium 环境中，强制 link down 再 link up 大约需要一秒钟恢复。这个观测与具体环境相关，仿真器不会复现它。

这种 bypass 既是局限，也是一个有价值的实验区分：仓库可以研究逻辑 reachability 改变时的控制与端侧行为，但不会声称其中包含物理链路重启成本。

> [!WARNING]
> L1/L2 链路事件、NIC/PHY 恢复和光学设备状态都在模型之外。表项写入 ACK 或 readback 不能替代这些物理观测。

## Debug Mode 边界

Debug Mode 使用所有非自环有向 port pair 替换 1:1 matching，让实验人员可以在 OCS 实验前验证包网络。它是诊断用 many-to-many 模式，不是 OCS connection state，也不是透明 Ethernet switching mode。

完整 lifecycle、API 行为、容量要求和失败语义见 [Debug Mode](./debug-mode-zh.md)。

## 可移植事实与部署事实

可移植仓库保存 logical port、connection 语义、target-neutral test 和 backend adapter。以下信息应保存在部署仓库或外部实验 artifact 中：

- 真实地址、MAC、物理端口和设备管理地址；
- switch、NIC、线缆和 transceiver inventory；
- 实测 RTT、blackout、链路恢复和应用恢复；
- site-specific 启动过程和验收结果；
- 仿真器与具名真实 OCS 的对比结论。

每项公开结果都应说明 target、deployment profile、控制协议、网络部署位置、backend、consistency boundary、update strategy、transport 和 measurement point。
