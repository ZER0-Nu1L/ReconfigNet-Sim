# OCS simulation principles and boundaries

[中文](./ocs-simulation-principles-and-boundaries-zh.md)

- Status: public project boundary
- Date: 2026-08-27
- Applies to: BMv2/P4App and Tofino/BFRT targets

## Purpose

ReconfigNet-Sim provides an executable approximation of the OCS control and integration surface before a real optical switch is available or fully integrated. It is intended to expose scheduler, controller, endpoint and deployment problems early, while keeping every result traceable to what a programmable packet switch can actually reproduce.

The model is useful only when its boundary is explicit. It is neither a virtual optical device nor evidence that packet-switch behavior is physically equivalent to an OCS.

## Why P4 is the approximation boundary

A P4 switch provides a controllable approximation boundary within which a large class of OCS system-integration questions can be investigated before—and alongside—real hardware.

BMv2/P4App provides a low-cost, reproducible software target with a short edit-build-run cycle. It is useful for developing and testing controller, endpoint, deployment and failure workflows before a hardware target is available. Its interaction with real RDMA NICs, PHY behavior, link training and hardware timing can be less representative than a physical deployment.

Tofino provides a complementary hardware layer with a programmable packet-processing pipeline and the BF-SDE/BFRT development environment. It can expose hardware-target and deployment issues that a software target cannot, while remaining an electrical packet switch rather than an optical device. The wider open P4 development environment is available through [Open P4 Studio](https://github.com/p4lang/open-p4studio).

These targets are complementary validation layers. Moving from BMv2/P4App to Tofino can improve confidence about programmable-switch behavior, but neither target by itself validates optical mechanisms, physical link recovery or transceiver behavior.

> [!NOTE]
> P4 is a controllable approximation boundary, not a physical-equivalence boundary. Use BMv2/P4App for rapid iteration and Tofino for hardware/deployment checks; validate optical behavior separately.

## Logical OCS abstraction

A real OCS changes physical connectivity between ports. Its core abstraction is a set of 1:1 cross-connects; it does not inspect Ethernet, IP or transport headers to decide which optical path to use.

ReconfigNet-Sim retains the logical part of that abstraction:

- a stable inventory of named logical ports;
- named bidirectional connections between ports;
- a `ConnectionSet` that may leave ports unused;
- a strict `pi` representation for a complete fixed-point-free symmetric matching;
- individual, DELTA and FULL connection updates;
- break-before-make behavior, validation, revision checks and rollback.

The logical model is shared by all supported backends. Device-specific P4 table names, P4Runtime identifiers, BFRT objects and physical `dev_port` values do not enter the northbound connection model.

## Packet-level realization

An electrical packet switch cannot change a physical optical path. The supported pipelines approximate the result in two stages:

1. IPv4/MAC forwarding state identifies the candidate egress associated with the packet destination.
2. An OCS permission table keyed by ingress and candidate egress permits only the directed port pairs belonging to the active connection set.

A bidirectional logical connection therefore becomes two directed permission entries. Updating the permission table emulates changing the active port mapping while the electrical links remain physically up.

This realization is not protocol transparent. The pipeline parses IPv4, may rewrite Ethernet addresses, decrements IPv4 TTL and recomputes the IPv4 checksum. ARP is not forwarded as part of the OCS abstraction, so experiments normally establish endpoint neighbor state in advance.

## Reconfiguration timing model

Updates use break-before-make for changed entries:

1. remove the old directed permission entries;
2. wait for the requested `delay_us` interval;
3. install the target permission entries;
4. wait for the selected acknowledgement or readback boundary.

`delay_us` is a requested control-plane gap. It does not directly measure any of the following:

- optical switching time;
- packet blackout duration;
- completion of a physical cross-connect;
- link reacquisition or endpoint recovery;
- the arrival of the first packet on the new path.

Host scheduling, RPC execution and backend calls can make the observed interval longer than the requested delay. Data-plane blackout must be measured separately when it is part of an experiment.

> [!WARNING]
> `delay_us` is a control-plane request, not an optical switching-time or packet-blackout measurement. Do not compare it directly with a physical OCS reconfiguration interval.

## State and truth boundaries

The project distinguishes four kinds of state:

| Boundary | Meaning | What it does not prove |
| --- | --- | --- |
| Desired state | The connection set accepted by the Agent | That device programming completed |
| Backend acknowledgement | P4Runtime, BFRT or a future backend accepted the operation | That packets already use the new path |
| Software/hardware readback | The managed table entries match the target | Optical power, BER or endpoint readiness |
| Physical observation | External measurement of links, packets or optical hardware | Not currently supplied by the emulator itself |

Agent values such as `CONNECTED`, `TUNED`, peer connectivity and port status are derived from the first three boundaries. They are deliberately not presented as optical telemetry.

> [!IMPORTANT]
> Desired state, backend acknowledgement and readback are software/device facts at different boundaries. None of them alone proves optical power, BER, physical link readiness or packet arrival on the new path.

## Physical and system behavior outside the model

ReconfigNet-Sim does not reproduce:

- link down/up signaling and physical link training;
- NIC, PHY, firmware or driver initialization;
- transceiver tuning or wavelength behavior;
- optical power, loss, crosstalk, BER or signal integrity;
- MEMS or silicon-photonic device state;
- hardware-specific asynchronous connection state machines;
- instantaneous or atomic switching of all data-plane paths.

The electrical links stay established while table entries change. This can intentionally bypass behavior that dominates a direct physical topology. In one Cavium-based environment observed during related integration work, forcing a link down and back up required roughly one second to recover. That observation is context-specific and is not reproduced by the emulator.

The bypass is both a limitation and a useful experimental distinction: the repository can study control and endpoint behavior when logical reachability changes without claiming to include the cost of physical link restart.

> [!WARNING]
> L1/L2 link events, NIC/PHY recovery and optical device state are outside this model. Table-write ACKs and readback must not be used as substitutes for those physical observations.

## Debug Mode boundary

Debug Mode replaces the 1:1 matching with every non-self directed port pair so operators can validate the packet network before an OCS experiment. It is a diagnostic many-to-many mode, not an OCS connection state and not a transparent Ethernet switching mode.

Its lifecycle, API behavior, capacity requirements and failure semantics are documented separately in [Debug Mode](./debug-mode.md).

## Portable versus deployment-specific facts

The portable repository contains logical ports, connection semantics, target-neutral tests and backend adapters. The following belong in a deployment repository or external experiment artifact:

- real addresses, MACs, physical ports and device management endpoints;
- switch, NIC, cable and transceiver inventory;
- measured RTT, blackout, link recovery and application recovery;
- site-specific startup procedures and acceptance results;
- claims comparing the emulator with a named real OCS.

Every published result should identify the target, deployment profile, control protocol, network placement, backend, consistency boundary, update strategy, transport and measurement point.
