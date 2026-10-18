---
title: "Microsecond Optical Switching. Millisecond Reality."
description: "Budgeting OCS Control Latency, from SDN Orchestration to Device Execution"
author: "Changbo Wu, Zhuolong Yu"
date: "2026-10-19"
draft: false
---

Optical Circuit Switches (OCSs) are crossing the threshold into sub-millisecond reconfiguration. In March 2025, on the eve of OFC, iPronics [unveiled ONE-32](https://ipronics.com/ipronics-unveils-worlds-first-silicon-photonics-optical-circuit-switch-for-ai-driven-data-centers-optical-network-transformation/), dubbing it "the world's first silicon photonics OCS" and promising **fast reconfiguration times under 300 microseconds**. Microsecond switching has thus emerged as a key selling point for data center products; in September 2026, the company [announced a $125 million Series B round](https://ipronics.com/ipronics-raises-125-million-to-scale-programmable-optical-networking-for-ai-data-centers/) with NVIDIA participation.

**Faster switching means optical networking can empower many more applications.** In their OCP presentation on [Fast Switching OCS](https://drive.google.com/file/d/1G0o39qR0wZvvHHfMTyiAe2mB6j5HxelF/view), James Burch (Lumotive) and Vivek Sarathy (iPronics) captured this transformation with the phrase "Faster Switching Expands its Use Cases": as reconfiguration enters the sub-millisecond domain, optical connections can adapt more frequently to dynamic traffic demands and AI communication phases—serving dynamic traffic grooming, inter-phase topology adaptation, and finer-grained circuit scheduling. The essence of speed lies in unlocking use cases previously constrained by reconfiguration delays.

Meanwhile, **adapting existing packet-switched SDN orchestration patterns and control architectures has become a prominent integration direction in industry.** In their OCP talk on [interoperable multi-vendor OCS use cases](https://drive.google.com/file/d/1ahYwnygxq6vzFGNZWNrXO7r9fLLhCvg7/view), Iwan Kartawira (iPronics, [co-lead of the OCP OCS sub-project](https://www.opencompute.org/community/optical-circuit-switching)) and Kazuaki Ueda (KDDI) explicitly proposed "CPO + OCS + SDN," placing workload optimization within a collaborative SDN and OCS framework. In this multi-vendor control path, a gNMI client talks to the device's gNMI service and generic YANG datastore before calling vendor APIs. Unified data models, controllers, and legacy management protocols form the backbone of this integration roadmap.

However, when an OCS reconfigures frequently across communication phases, **carrying over the full SDN configuration workflow designed for electrical packet switches risks making the control path the primary bottleneck in reconfiguration speed.** In an implementation where every reconfiguration is mediated by a centralized controller, an application first requests the controller, which arbitrates resources and invokes the device agent (OCS Agent); the device software in turn interacts with local services and SDKs to execute the operation. While centralized governance and vendor abstraction each provide clear value, every hop introduces additional communication, validation, scheduling, and queuing.

Against the historical transition from millisecond-scale 3D MEMS to sub-millisecond silicon photonics, consider the following calculation: **Suppose OCS physical reconfiguration drops from 20 ms to 200 μs, but serial control latency remains 5 ms; full reconfiguration latency only drops from 25 ms to 5.2 ms. While the physical device is 100× faster, end-to-end reconfiguration is only ~4.8× faster.** When the control architecture remains organized around slow configuration cycles while execution must keep pace with communication scheduling, the legacy control path swallows most of the gains from hardware advances: **the device has entered the microsecond regime, but complete reconfiguration remains trapped in milliseconds.**

![Physical switching speeds up 100 times, while full reconfiguration speeds up about 4.8 times](/ReconfigNet-Sim/images/ocs-control-arch/en/time-budget.svg)

*Figure 1: Identical millisecond scales compare physical and full reconfiguration. This hypothetical example reduces physical reconfiguration from 20 ms to 0.2 ms while keeping serial control at 5 ms, raising control's share of total waiting time from 20% to ~96%. This is an explicit hypothetical calculation; endpoint recovery is excluded.*

The gap illustrated above **stems from a fundamental mismatch between the control architecture and how fast-reconfiguring switches are utilized.** In typical SDN-controlled electrical packet networks, a centralized controller manages global resource arbitration and rule configuration, while switch hardware continuously forwards packets. This architecture balances centralized management with line-rate forwarding: a single configuration update amortizes its control overhead across massive subsequent traffic flows, making the system relatively insensitive to individual configuration latencies.

In contrast, for applications that reconfigure an OCS across distinct communication phases, establishing the connection directly dictates when the next phase can transmit data. Forcing every reconfiguration through full remote coordination and device provisioning means the previously amortized control cost becomes an unavoidable delay paid before every communication round. **Directly inheriting this workflow creates a severe timescale mismatch between the control architecture and fast OCS switching.** Returning to the example: physical actuation is compressed to 200 μs, but the 5 ms control path remains untouched, inverting the cost structure entirely—the bulk of the waiting time is no longer the physical device action, but the control software stack itself.

While the OCP community advances unified data models, multi-vendor interoperability, and end-to-end metrics, this article asks: **when configuration becomes an in-path step in every communication round, is the existing control architecture still appropriate?**

> [!TLDR]
>
> - **Budget the full end-to-end path.** Beyond physical switching, request processing, software handoffs, and low-level API invocations govern when a link becomes usable. If physical reconfiguration drops from 20 ms to 200 μs, a 5 ms serial control path keeps total reconfiguration at millisecond scales.
> - **Evaluate three decoupled dimensions.** How the controller participates, what interface and protocol are used, and how the device executes can each be chosen independently, guided by application workload requirements and latency budgets.
> - **Verify latency budgets on Tofino.** Measurements show that streamlining upper software layers reduces full API latency from ~6.59 ms to 4.39 ms, with vendor runtime calls dominating the remaining time; controller-bypass benefits and photonic parallel convergence remain to be measured.
> - **Recognize software limits.** Pushing protocol and process optimizations to the limit on general-purpose CPUs with management-protocol stacks yields a software latency floor of ~20 μs (p99); faster paths demand driver-level or FPGA-based designs.

## A Single Reconfiguration: From Planning Decisions to Device Execution
Integrating an OCS into a network begins with deciding which ports to connect, when to change topology, and how to synchronize with application communication phases; the system must then pass these decisions to the device to execute the physical reconfiguration. The former belongs to topology planning and scheduling; the latter belongs to triggering and execution. **Planning can be performed ahead of time, while execution is triggered on demand.** When requirements are predictable, connections can be computed in advance; when requests arrive dynamically, decisions must be made online. Our focus here is: once a target is established, how long is the execution path before the application can actually transmit over it?

Execution typically starts with a connection request: after necessary coordination, the request reaches the device control service; device software validates the target and permissions, invokes the SDK or low-level API, and converts port mappings into executable physical actuator states. Implementations vary across OCS hardware:

> - Google's [Mission Apollo](https://arxiv.org/abs/2208.10041) describes a concrete MEMS example: the CPU receives port connection commands, sets mirror voltages via an FPGA and high-voltage driver boards, and uses camera feedback to adjust mirrors until optical paths stabilize.
> - For programmable silicon photonics meshes, the target must also be mapped to internal waveguides and programmable cell states; [λλ](https://dl.acm.org/doi/10.1145/3789240.3829151) translates high-level programs into graph representations and solves for optimal mappings onto photonic hardware.
> Both examples translate high-level connection intent into physical device actions, differing only in the intermediate transformations required.

When reporting results according to API contracts, systems must distinguish between **request received, programming completed, optical path settled, and application ready**. Figure 2 illustrates these responsibilities and dependencies.

![Planning and scheduling feed a trigger and execution path, expanded into five responsibilities](/ReconfigNet-Sim/images/ocs-control-arch/en/control-framework.svg)

*Figure 2: Planning/scheduling and trigger/execution are separate responsibilities. The expanded view shows request submission, device service, low-level API, actuation and path readiness. A reply reports the state defined by the API contract.*

## The Time Budget: Beyond the Device, How Long Must We Wait?
With this framework in place, the initial 5 ms budget can be broken down. Consider a serial scenario: once an application determines its target, it issues a request, the device performs the reconfiguration, and endpoints wait until the path is fully ready before starting the next communication round. Decomposing these non-overlapping intervals along the timeline:

```text
T_ready = T_request + T_service + T_apply + T_settle + T_endpoint
```

- `T_request`: Request preparation, outbound network transit, and mandatory controller processing.
- `T_service`: Device service reception, validation, queuing, and internal software handoffs.
- `T_apply`: Translating target mappings into device commands and invoking low-level APIs.
- `T_settle`: Physical settling time required after API invocation completes.
- `T_endpoint`: Endpoint recovery procedures and completion notifications required by the application.

Here, the finish line is meeting the prerequisites for the next round of communication; an API reply is only one possible notification mechanism.

In practical implementations with overlapping phases, latency follows the critical path of the dependency graph. For instance, if an SDK blocks until micro-mirrors settle, one must not double-count physical actuation time; similarly, if endpoint recovery runs concurrently with physical settling, their durations cannot simply be added. The purpose of this model is to pinpoint what the application truly waits for, rather than tallying coarse totals that obscure downstream invocations.

Temporarily setting aside endpoint recovery, let serial control latency be $C$ and physical reconfiguration be $P$, returning to $T_{\text{ready}} = C + P$. The same physical speedup combined with different control paths yields starkly different full reconfiguration latencies:

| Serial Control Cost $C$ | $P = 20\,\mathrm{ms}$: Total Wait | Control Share | $P = 200\,\mu\mathrm{s}$: Total Wait | Control Share |
| --- | ---: | ---: | ---: | ---: |
| 5 ms | 25 ms | 20.0% | 5.2 ms | 96.2% |
| 500 μs | 20.5 ms | 2.4% | 700 μs | 71.4% |
| 100 μs | 20.1 ms | 0.5% | 300 μs | 33.3% |

*The control share is calculated as $C / (C + P)$.*

**What truly transforms architectural requirements is how much time the application allocates to the control path.** If the control budget is capped at 10% of physical reconfiguration time ($C / P \le 10\%$), a 20 ms scenario permits a 2 ms control budget, whereas a 200 μs scenario allows merely 20 μs. This target turns the vague ambition that "control must be fast enough" into an explicit, verifiable constraint.

### How Does Control Latency Accumulate into Milliseconds?
Network serialization and transit for requests and replies can be estimated from byte sizes. Assuming 1 KiB payloads in each direction with 128 B encapsulation overhead across two store-and-forward switches and three link hops: without retransmissions, bidirectional serialization takes ~55 μs at 1 Gb/s and ~5.5 μs at 10 Gb/s, excluding propagation and forwarding delays.

Once bytes arrive at the node, however, the request must traverse endpoint stacks, internal software handoffs, and low-level API invocations. Consider a concrete reference path: **warmed-up connections, low load, direct client access to the device service, a standalone Worker process on the device, two serial low-level API calls per reconfiguration, and a synchronous reply.** Based on typical overhead orders of magnitude in modern Tofino software stacks, we construct the non-overlapping hypothetical budget below; it represents neither universal OCS parameters nor measured statistical bounds.

| Components of a Synchronous Request | Example Budget | Tasks Included |
| --- | ---: | --- |
| Network transit (request + reply) | 10–80 μs | Serialization, propagation, switch forwarding; excludes endpoint stacks |
| Client and northbound processing | 0.5–1 ms | I/O, serialization/deserialization, request dispatch; excludes network transit |
| Internal handoffs and non-programming work | 0.5–2 ms | Worker IPC handoffs, validation, queuing, state tracking; excludes low-level calls |
| Two serial low-level API invocations | 3–4 ms | SDK client, low-level RPC, runtime processing, internal settling wait |
| **Total** | **~4–7 ms** | **No standalone controller, standalone Worker, two serial low-level calls** |

*Summing the range endpoints yields 4.01–7.08 ms, rounded in the table. The budget terminates at the synchronous API reply, which does not guarantee optical path readiness.*

Software stacks, device IPC handoffs, and low-level API calls dominate this budget. Below, we empirically evaluate these assumptions.

For applications that can tolerate only tens of microseconds of additional control delay, hundreds of microseconds of IPC and API overhead already exceed the budget. We must therefore evaluate which coordination steps must occur on every reconfiguration, how much overhead protocol handling introduces, and whether internal device handoffs can be minimized or parallelized. This brings us to three core architectural choices.

## Architectural Choices: Three Independent Design Dimensions
The budget breakdown highlights three critical stages: whether requests must pass through a global controller, how they interface with the device, and how internal device software applies the target connections. These correspond to three orthogonal design dimensions: **① Controller Participation, ② Control Interface & Protocol, and ③ Device Execution Architecture.** Each dimension is judged against a single criterion: does the capability provided justify its latency cost within the given budget?

| Design Dimension | Implementation Options | Primary Trade-offs |
| --- | --- | --- |
| ① Controller Participation | Per-request mediation vs. Preauthorized direct submission (bypass) | Online global arbitration vs. repeated coordination latency |
| ② Control Interface & Protocol | Generic RPC vs. Lightweight request-reply protocol | Tooling richness and protocol features vs. processing overhead and maintenance burden |
| ③ Device Execution Architecture | Standalone Worker vs. In-process adaptation; Serial actuation vs. Batched & multi-channel parallel control | Fault isolation and modularity vs. IPC handoffs, loading bandwidth, concurrency, and settling dependencies |

![Application requests pass through a global controller, device service, Worker, vendor runtime and device](/ReconfigNet-Sim/images/ocs-control-arch/en/sdn-control-architecture.svg)

*Figure 3: One integration example using centralized SDN coordination, with ①, ②, and ③ corresponding to the table above. Application requests pass through a global controller to the device service, which calls the SDK via a Worker process; results return along the invocation path.*

These three dimensions can be varied independently: one can retain generic RPCs while permitting preauthorized direct submissions, or adopt lightweight framing while retaining a standalone Worker. Below, we estimate the magnitude of each trade-off under explicit assumptions to identify what is worth measuring. These are conditional estimates, not additive measured gains; specific combinations are discussed at the end of this section.

### ① Controller Participation: Per-Request Mediation or Preauthorization?
When each reconfiguration competes for global optical ports, centralized controller arbitration is indispensable: determining whether a target topology is available requires global consensus. Other scenarios, however, differ significantly. Suppose a communication group holds exclusive leases on a subset of ports during a job run, such that internal topology reconfigurations do not affect external traffic. The global controller can pre-allocate resource boundaries, allowing a group representative to submit connection changes directly to the device. The device continues to validate permissions, targets, and version epochs, rejecting stale writers, while the controller supervises, reclaims leases, and handles fault recovery. **Unified management of global state does not require every authorized local operation to traverse the same remote orchestrator.**

Bypassing the controller eliminates repetitive network hops, queuing, and processing, while retaining necessary on-device verification. It requires handling representative failures, lease revocations, and reconciliation when request outcomes are ambiguous. Where boundaries cannot be pre-assigned, online arbitration remains necessary; where they can, this hop can be removed from the critical execution path.

To estimate the magnitude: if per-request coordination adds 100 μs of network RTT and 200 μs of controller processing and queuing that cannot be overlapped, preauthorization can shave **~300 μs** off the execution path. For scenarios with tens of microseconds of control budget, this hop alone warrants re-architecting; whether these savings materialize depends on any additional validation work placed on the device.

Beyond *who* submits, dimension ① extends along the time axis: how much preparation can be performed in advance? If communication phases are predictable and the OCS supports pre-storing candidate configurations, runtime execution may only need to select and activate a pre-loaded configuration or trigger it at an agreed timestamp. Authorization and configuration preparation address distinct challenges: the former determines *who* can modify resources, while the latter minimizes the computation and loading work required *during* execution. Device memory constraints, clock skew, and schedule cancellation mechanisms govern feasibility.

### ② Control Interface & Protocol: Generic RPC or Lightweight Request-Reply?
Device management benefits from rich schemas, telemetry streaming, and mature tooling, which generic RPC frameworks readily provide. A latency-critical reconfiguration, however, may only need to convey target port pairs, credentials, version epochs, and completion status. If generic RPC processing stays within the latency budget, retaining it aids maintainability and ecosystem integration. If its overhead becomes non-negligible, a narrower control interface can handle execution, leaving YANG modeling and gNMI/gRPC to handle management and telemetry.

We explored a lightweight framing implementation, referred to here by its project name, `framed`: it uses length-prefixed framing to stream protobuf requests and responses, using persistent TCP northbound and Unix Domain Sockets (UDS) for internal IPC. This bypasses substantial generic RPC framing overhead, but requires implementing error handling, timeouts, reconnection, backpressure, and completion semantics manually. The baseline gRPC setup also reuses persistent connections, so latency deltas cannot be attributed to connection establishment; likewise, sharing protobuf schemas does not imply gNMI interoperability.

Assuming connection reuse, low load, and identical wire transit, if optimizing endpoint protocol processing on both sides reduces request-reply overhead from 200 μs to 100 μs per hop, a single hop saves **~100 μs**, and two serial hops save **~200 μs**. These figures estimate endpoint processing and cannot be double-counted with thread handoff savings; whether narrower framing achieves this gain requires measuring the full execution path.

### ③ Device Execution Architecture: From Software Handoffs to Parallel Control
**③a Software layout determines the number of process handoffs.** Request handling, semantic validation, and SDK adaptation represent distinct responsibilities, but whether each requires an isolated process depends on the trade-off between isolation and the latency budget. A standalone Worker process isolates language runtimes and SDK crashes, but introduces serialization, socket overhead, thread wakeups, and queuing. In-process adaptation eliminates these handoffs, but requires language compatibility and broadens failure domains. Clean architectural boundaries should be maintained; process boundaries must justify their cost.

If an internal UDS round-trip takes 30 μs, and message repackaging and thread dispatch add another 200 μs, collapsing this boundary into an in-process call could eliminate **~230 μs**, assuming negligible new inline overhead. This is a conditional estimate, not an intrinsic UDS constant. Both designs must maintain a single-writer discipline; consolidating worker processes neither eliminates concurrency queues nor bypasses underlying vendor runtimes (e.g., BFRT to `bf_switchd`).

While ③a addresses software handoffs before reaching hardware (on the scale of hundreds of microseconds), once commands enter the device, waiting transitions to an entirely different physical scale: how the hardware actuates the requested connections.

**③b Device actuation determines how many steps are required to realize a target.** A logical connection may involve adjusting phase shifts across multiple Mach-Zehnder Interferometers (MZIs) or reorienting pairs of 3D MEMS micro-mirrors. Bundling multiple operations into one command minimizes submission overhead, but whether the device loads configurations concurrently, drives actuators in parallel, and requires multiple feedback iterations to stabilize depends on low-level controller capabilities. **Fewer API calls do not imply parallel device actuation; concurrent actuation does not imply simultaneous convergence.**

Apollo provides an example of array-level control: the CPU programs MEMS mirrors via an FPGA, high-voltage drivers, and DACs, while feedback cameras capture images of each mirror array to allow the servo system to adjust multiple mirrors. While this avoids per-mirror host requests, array-level sensing and actuation do not guarantee atomic switching across all optical paths.

Silicon photonics can likewise be driven concurrently via multichannel electronic controllers. [A study on integrated electronic controllers](https://arxiv.org/abs/2501.09664v1) used two 8-channel ASICs to control an MZI mesh with 16 parallel feedback loops. While individual thermal phase actuators responded in ~10 μs, first-stage configuration required ~1.5 ms, and overall convergence across a 4-stage mesh took ~10 ms: downstream stages could not settle until upstream stages stabilized. Although conducted on an adaptive beam-coupler rather than a commercial OCS, this experiment clearly demonstrates three distinct timescales: **actuator response, feedback convergence, and full-mesh settling.**

![MZI multichannel drive and photodetector feedback beside MEMS array drive and camera feedback, with waiting-time sketches](/ReconfigNet-Sim/images/ocs-control-arch/en/device-parallel-control.svg)

*Figure 4: Original schematics illustrate concurrent actuation and feedback (MZI multichannel drive and photodetector feedback beside MEMS array drive and camera feedback). Concurrent commands do not imply atomic optical switching. The timing sketches compare serial, grouped, parallel, and dependent settling; they are not product latency measurements.*

Device-side execution time can thus be decomposed into: command submission, configuration loading, multi-stage feedback convergence, and final acknowledgment. Within any parallel stage, latency is bounded by the slowest settling channel; sequential dependencies between stages accumulate additively. Where loading and actuation overlap, latency follows the longest path in the actual dependency graph rather than the sum of loading times.

Consider a simplified hypothetical example: adjusting 16 uncoupled channels, each requiring 70 μs to settle. Assuming configuration loading takes 40 μs, final acknowledgment takes 20 μs, submission overhead is excluded, and phases do not overlap:

| Execution Pattern | Settling Rounds | On-Device Waiting Time |
| --- | ---: | ---: |
| Serial drive (settle before proceeding) | 16 | 40 + 16 × 70 + 20 = **1.18 ms** |
| Grouped drive (4 channels parallel per group) | 4 | 40 + 4 × 70 + 20 = **340 μs** |
| Fully parallel (16 channels concurrent) | 1 | 40 + 70 + 20 = **130 μs** |

*The 70 μs parameter is specific to this example, not a universal lower bound for individual MZIs.*

This breakdown demonstrates that beyond intrinsic actuator speed, one must ask: how many actuators change state, how many channels can the driver actuate concurrently, how fast can configuration bytes be transferred, and how many feedback iterations are required? Transferring $B$ bytes at rate $R$ requires at least $B/R$ seconds of irreducible transit time. Scaling channel counts, bus bandwidth bottlenecks, or thermal crosstalk corrections can easily stretch the critical path. Published sub-millisecond figures for commercial hardware do not reveal their underlying internal control mechanisms.

### Synthesizing Choices into a Candidate Architecture
Evaluating these three dimensions enables concrete architectural synthesis. Consider a fast-reconfiguration workload where resources can be pre-allocated, the device can validate authorization tokens, and the SDK supports in-process binding. A promising candidate architecture emerges: the controller coordinates resources offline, handling only authorization, health monitoring, and reclamation; group representatives submit requests directly via a lightweight request-reply protocol; the device performs validation and SDK adaptation in-process, submitting batched targets to vendor runtimes through a single-writer queue; the device then drives actuators and feedback according to its hardware concurrency, reporting completion per contract. Compared with Figure 3, this design chooses the streamlined end along all three axes, though intermediate combinations remain viable.

![Preauthorization lets applications submit lightweight requests directly to an in-process device service and SDK adapter](/ReconfigNet-Sim/images/ocs-control-arch/en/candidate-architecture.svg)

*Figure 5: One candidate combination of the three design choices, using the same role positions as Figure 3. Dashed lines show preauthorization, supervision, and reclamation; solid lines show per-request calls, device invocations, and replies. The device retains validation, single-writer queuing, and vendor runtimes; lower-level concurrency and settling must be independently verified. This candidate has not been validated end-to-end.*

Comparing these two architectural diagrams clarifies which operations move offline, which software handoffs are collapsed, and which device latencies remain unavoidable. We now turn to our experimental platform to examine the empirical evidence.

## Experimental Platform: Inspecting the Control Path with Tofino
Grounding these architectural trade-offs in verifiable timing budgets requires a platform that allows continuous software modification alongside real-time connectivity observation. Because commercial OCS devices differ widely in physics, port counts, software stacks, and availability, building complete optical testbeds for every control path iteration is impractical. ReconfigNet-Sim was designed to decouple the development of surrounding networking systems from optical hardware, providing controllable connectivity dynamics for systematic benchmarking.

Following our control framework, Tofino receives connection requests, validates and translates them via an Agent, and applies them through vendor control interfaces. The difference lies in the data plane: the BFRT client issues local gRPC calls to the runtime daemon `bf_switchd`, programming P4 match-action tables to toggle packet forwarding between logical ports. Electrical links remain up throughout; the platform exercises the complete software, runtime, and hardware control path, but does not replicate optical transient physics or physical link retraining.

![Physical OCS control responsibilities map to ReconfigNet-Sim and Tofino, while their connectivity mechanisms differ](/ReconfigNet-Sim/images/ocs-control-arch/en/platform-comparison.svg)

*Figure 6: Functional comparison between a physical OCS and the Tofino platform. The two sides align across connection requests, device control, and connectivity changes, but differ in physical mechanisms. Tofino table updates do not replicate optical power settling, wavelength tuning, BER, PHY retraining, or NIC recovery; furthermore, P4 parses and processes packet headers, lacking the protocol transparency of physical OCSs.*

This setup accurately answers questions such as "how long do software handoffs take?" and "what portion of the budget does the vendor runtime consume?", verifying control optimizations against real forwarding traffic. Our experiments use physical Tofino-1 hardware; we do not substitute software simulation models for physical measurements, nor do we extrapolate software latencies directly into optical switching times. See the [Simulation Principles and Boundaries](https://github.com/ZER0-Nu1L/ReconfigNet-Sim/blob/main/docs/ocs-simulation-principles-and-boundaries.md) document for a complete accounting of these boundaries.

## Experimental Evidence: Paths, Processes, and Device Invocations
Experiments ran on physical Tofino-1 hardware with BF-SDE/BFRT 9.3.1 and an Intel Pentium D1517 control CPU. After warming up connections, a single-writer thread repeatedly submitted topology updates. Common baseline settings included DELTA updates, batched device calls, zero artificial delay gaps, and `CACHED_ACK` (acknowledging requests after successful southbound invocation and caching state locally, avoiding synchronous hardware readbacks).

Beyond these common settings, four comparative configurations were evaluated:

- **End-to-end path comparison**: CPU @ ~800 MHz; client access over an external management network; comparing full gRPC against a bundled optimization across 5 runs of 2,000 reconfigurations each.
- **Process layout comparison**: CPU @ ~800 MHz; local TCP on the switch CPU; comparing Go Core + Python Worker, Python single-process, and Go single-process across 200 warm writes per run.
- **Invocation count comparison**: CPU @ ~1.2 GHz; local TCP; comparing two sequential BFRT calls against a single merged call across 200 warm writes per run.
- *These variants reside on internal development branches and are not yet fully available as official options in the public repository.*

Our measurements evaluate ② Control Interface & Protocol and ③ Device Execution Architecture (specifically software handoffs and vendor runtime invocations); they do not include a standalone remote controller hop, nor do they measure optical actuator convergence. We examine end-to-end path optimizations, device process organizations, and lower-level runtime invocation costs. First, we define the observed metrics:

| Metric | Timing Interval | Purpose |
| --- | --- | --- |
| API Latency | Client issues operation → Client receives reply | Measures how long callers wait on the interface |
| Device Programming Interval | Backend starts programming → Low-level API returns | Aggregate of SDK, low-level RPC, runtime processing, and internal wait |
| New-Path Observation Latency | Request submitted → First probe packet received on new path | Measures when the data plane is observed to forward traffic |
| Blackout | Last packet received on old path → First packet received on new path | Data plane forwarding gap observed by probes |

These metrics measure the same reconfiguration from different vantage points and are not additive. API replies and new-path probe packets follow independent completion paths; probe timings also incorporate transmission intervals and packet scheduling. Software intervals are derived by subtracting nested timing timestamps on a per-request basis before computing aggregate statistics: e.g., $\text{Non-device Interval} = \text{API Total} - \text{Device Programming Interval}$. One must not add a Worker's total duration to the BFRT call it encompasses, nor subtract p99 values across mismatched distributions; percentiles use nearest-rank throughout.

### End-to-End Path: What Do Bundled Optimizations Yield?
**Reducing software overhead along the control path makes new paths observable earlier, but forwarding availability and blackout windows do not improve proportionally.** The first experiment retained two sequential BFRT calls (delete followed by insert) on the device, comparing the baseline gRPC path against a bundled set of optimizations.

The baseline stack consisted of: Remote Client → gRPC Northbound → Go Core → Local gRPC Worker → Dedicated Python Execution Thread → BFRT. The optimized bundle introduced `framed` northbound and worker protocols, compact success responses, reduced thread dispatch hops, and pinned CPU affinities. This bundle retained the Python Worker, without in-process adaptation. Both setups executed 5 runs of 2,000 successful reconfigurations each, measured by busy-polling probes targeting 20 μs intervals.

| Architecture | New-Path Obs. p50 | New-Path Obs. p99 | Blackout p99 |
| --- | ---: | ---: | ---: |
| Full gRPC multiprocess baseline | 4.914 ms | 5.490 ms | 2.776 ms |
| Optimized bundle (`framed`, fewer handoffs, compact replies, CPU pinning) | 3.320 ms | 3.817 ms | 2.490 ms |

The p99 new-path observation latency dropped by ~30.5%, while p99 blackout dropped by ~10.3%. Accelerating request handling reduces total waiting time after triggering; the data-plane blackout duration, however, remains governed by device execution dynamics. These gains reflect the combined configuration, including CPU pinning, and cannot be credited to `framed` alone. The actual probe intervals reached ~24/22 μs at p99, so results cannot simply subtract a static "20 μs measurement error."

Importantly, while this baseline includes full gRPC and internal IPC handoffs, the client communicated directly with the device without traversing an independent global controller. **These data support streamlining on-device execution, but do not measure the gains of bypassing a centralized controller.** The preauthorization model in our candidate architecture remains an architectural proposal awaiting end-to-end evaluation.

### Latency Breakdown: Where Did the Milliseconds Go?
Our hypothetical budget assumed a standalone Worker and two low-level API calls. Our empirical API measurements span this exact structure, allowing us to validate the orders of magnitude. Aggregating 5 runs of 2,000 requests each for both the gRPC baseline and the optimized bundle, we compute per-request nested differences and take the mean to produce mutually exclusive intervals:

| API Budget Item (Mean) | Full gRPC Baseline | Optimized Bundle + CPU Pinning |
| --- | ---: | ---: |
| Client / Northbound residual | 847.4 μs | 532.5 μs |
| Core logic (excluding Worker call) | 58.2 μs | 59.3 μs |
| Worker RPC residual (excluding Worker internal) | 1,167.7 μs | 364.2 μs |
| Worker internal (excluding device programming) | 701.4 μs | 294.3 μs |
| BFRT device programming interval | 3,818.1 μs | 3,144.5 μs |
| **Total API Latency** | **6,592.8 μs** | **4,394.8 μs** |

In this test, each reconfiguration deleted six table entries and inserted six, split across two BFRT calls; the data-plane probe experiments above deleted and inserted four entries each. Because these represent different request payloads and measurements, API means cannot be compared directly with new-path percentiles. Northbound overhead totaled ~0.85 → 0.53 ms including network transit; Core, Worker IPC, and non-programming tasks totaled ~1.93 → 0.72 ms; two BFRT calls totaled ~3.82 → 3.14 ms. Total mean API latency dropped from ~6.59 ms to 4.39 ms, aligning closely with our 4–7 ms hypothetical budget. **Hypothetical budgets help locate overhead; empirical measurements confirm actual costs.**

### Process Organization: How Much Latency Do Process Boundaries Add?
The second comparison evaluates the software layout in ③a. Using identical local clients, ~800 MHz CPU frequency, and the `framed` protocol, we evaluated three architectures: Go Core + Python Worker, Python single-process, and Go single-process (calling BFRT directly). Each ran 200 warm writes per run with two BFRT calls; two distinct runs of Go single-process are shown:

| Implementation | Non-Device Interval (Mean) | Non-Device Interval (p99) |
| --- | ---: | ---: |
| Go Core + Python Worker | 1,003.3 μs | 1,272 μs |
| Python single-process | 667.1 μs | 924 μs |
| Go single-process, Run A | 307.0 μs | 408 μs |
| Go single-process, Run B | 302.8 μs | 570 μs |

Compared with the multi-process setup, Python single-process reduced mean non-device latency by ~336 μs; Go single-process runs landed at ~303–307 μs. While several hundred microseconds might appear minor within a multi-millisecond budget, it can make or break feasibility when operating under sub-millisecond budgets. Even on the same physical host, process, runtime, and scheduling boundaries impose substantial costs.

![Independent panels compare new-path p50 and p99 with non-device interval mean and p99](/ReconfigNet-Sim/images/ocs-control-arch/en/latency-comparison.svg)

*Figure 7: Left: End-to-end path comparison (new-path observation p50 and p99 across the management network, ~800 MHz, 10,000 samples per group). Right: Device process layout comparison (non-device intervals over local TCP, ~800 MHz, 200 samples per run, including two Go single-process runs). Panels represent independent experiments with different conditions; line segments are not confidence intervals.*

These implementations simultaneously vary languages, protocol libraries, and runtime schedulers; even between Go runs, environmental factors were not entirely identical, so differences cannot be attributed entirely to process boundaries. The data confirm that software handoffs carry measurable costs; isolating pure process-boundary costs would require fixing the language, northbound interface, and backend while changing only the process layout. We compared Python and Go because the former is interpreted and the latter is compiled (analogous to C/Rust), providing visibility into the overhead of language runtimes rather than establishing a language benchmark.

Repeated targets can also reuse precomputed configuration mappings. In Python single-process records, across three runs without memoization and four runs with memoization (200 warm writes each), mean non-device latency dropped from ~694.1 μs to 635.7 μs—a saving of **~58.3 μs**. This demonstrates the benefit of eliminating redundant software computation for repeated targets; it does not measure pre-loading hardware actuator states. Because this delta is tens of microseconds, consistent instrumentation is vital: in this embedded Python environment, calling `time.monotonic_ns()` took ~2.193 μs; making 8–10 calls per request incurred 18–22 μs purely in timestamping overhead, easily obscuring memoization gains.

### Device Invocations: What Costs Remain After Streamlining Upper Layers?
Process layout represents only one layer of the control path. To examine lower-level costs, we evaluated Go single-process directly driving BFRT at ~1.2 GHz with persistent `framed` connections, compact replies, and CPU pinning. **Even after substantially trimming upper software layers, device programming continues to dominate the remaining latency.** For hardware vendors, minimizing the latency required to turn an intent into hardware state is intrinsic to fast switching.

We illustrate this by comparing two sequential BFRT calls (delete followed by insert) against a single merged call. Following 50 warmup iterations, each run recorded 200 changed writes; the client ran locally on the switch CPU, excluding external network latency.

| Go Direct Architecture | API p50 / p99 | Device Programming p50 / p99 | Non-Device p99 |
| --- | ---: | ---: | ---: |
| Two device calls | 852 / 1,174 μs | 715 / 998 μs | 202 μs |
| Single call, Run A | 625 / 782 μs | 490 / 591 μs | 203 μs |
| Single call, Run B | 622 / 1,021 μs | 486 / 751 μs | 215 μs |

Eliminating one API call reduced median device programming latency by ~0.23 ms, while non-device overhead remained unchanged. While merging calls is an implementation detail, it points to a broader architectural reality: **how many serial invocations a reconfiguration requires, and how much fixed processing each incurs, directly shapes the execution budget.** Whether these steps can be batched or must be driven sequentially by the caller is a fundamental API design question.

![API and BFRT programming p50 and p99 for two device requests and two runs with one request](/ReconfigNet-Sim/images/ocs-control-arch/en/runtime-cost.svg)

*Figure 8: API latency and BFRT programming intervals (p50 and p99) under identical Go direct conditions (~1.2 GHz, local TCP, 200 samples per run) for two device calls versus two single-call runs. BFRT programming is nested inside API latency and the two are not additive.*

The ~0.5 ms programming interval encompasses the BFRT client, local gRPC, `bf_switchd`, OS scheduling, and call waiting; pure hardware programming time has not been isolated. For context, the same atomic write took ~2.4 ms via the Python BFRT client versus ~0.49 ms via Go—switching languages trims client-side overhead, but server-side daemon latency remains. These runs feature different structures and conditions, serving only to differentiate client and daemon boundaries rather than establishing ASIC or OCS physical limits.

The `ROLLBACK_ON_ERROR` flag used in merged calls specifies error-handling semantics; it provides no data-plane atomic switching guarantee. This experiment evaluates submission granularity in ③b; parallel actuation across MZIs or MEMS mirrors remains unmeasured here.

Photonic literature also confirms that driver stages can dominate system reconfiguration times. A [2024 Nature Communications paper](https://www.nature.com/articles/s41467-024-45888-7) describes a general-purpose programmable photonic processor using a programmable current-source array to drive 304 phase actuators; the authors state that setting and readout are dominated by the electronic driver units, requiring **15–90 ms** for full reconfiguration. While this RF processor is distinct from ONE-32 and cannot represent commercial datacenter OCS metrics, it underscores that driver and readout architectures can dictate system reconfiguration speed.

While BFRT illustrates submission costs, λλ highlights that configuration generation can also introduce significant latency. Supporting rich photonic programmability requires compilers to map logical programs onto physical meshes. For fast switching, systems must distinguish **how long it takes to compile a new configuration, how long it takes to load it, and how long it takes to select and activate an existing one.** Only configurations that must be generated dynamically online enter the per-reconfiguration critical path; offline compilation belongs to pre-computation phases.

One cannot assume programmable optical switches inherently lack fast-switching capability simply due to compilation overhead. The key questions to ask of devices and software stacks are: when high-level targets change, what costs must be paid online? Can known targets be staged in advance, and what fast runtime interfaces exist to trigger them? We reference λλ to illustrate configuration generation responsibilities; specific compilation latencies and experimental conditions await complete verification.

Workload arrival rates further alter the budget. Consider a basic queuing estimate: if a single-writer service takes 0.5 ms per request, and requests arrive as a Poisson process at 1,600 req/s, service utilization reaches 80%. Under an M/D/1 queuing model, average queuing delay is ~1 ms, pushing total turnaround to ~1.5 ms. While an idealized estimate rather than measured tail latency, it reminds us that a control path meeting budgets when idle can easily breach them under sustained traffic.

Our empirical findings confirm that software architecture and device interfaces directly dictate end-to-end execution latency, and optimizations at different layers yield distinct benefits. The two most critical gaps remaining to be measured are: holding device paths constant while comparing centralized forwarding against preauthorized direct submission, and holding language and interfaces constant while isolating process-boundary costs. These tests will independently isolate controller participation and internal isolation costs.

## The Latency Floor of Software Paths: How Fast Can General-Purpose CPUs Go?
Pushing these architectural dimensions to their limits inevitably encounters irreducible software latency floors. These are not implementation bugs in a given protocol, but intrinsic overheads of general-purpose CPUs, operating systems, and management protocol stacks:

| Structural Layer | Order of Magnitude | Operational Context |
| --- | ---: | --- |
| System calls / Thread wakeups & context switches | 0.1–2.5 μs per event | Paid on every thread and process handoff |
| Kernel network stack (per packet, unidirectional) | Several to 20 μs | Kernel bypass can achieve 1–3 μs, altering the architecture |
| PCIe device register read/write | 0.3–1 μs | Largely independent of CPU clock frequency |
| End-to-end management protocols | $10^1$ ms | SONiC official telemetry takes 68 ms/round (large counter set); gNMI config is only 1.5–3× faster than NETCONF |

CPU frequency scaling illustrates this bound: scaling from 800 MHz to 1,200 MHz shortened only CPU-bound phases proportionally (measured ratios of 0.55–0.70), while fixed overheads remained unchanged. "Upgrading to a faster CPU" cannot change the order of magnitude.

Eliminating these fixed overheads entirely (using shared memory on the same host, kernel bypass, zero-allocation data paths, and real-time scheduling) pushes the software latency floor to roughly **20 μs (p99)**, with an absolute theoretical limit around **4–8 μs**; going faster requires abandoning general-purpose OS stacks. For comparison, FPGA-based control planes can achieve link-establishment decisions in **500 ns**, with end-to-end link setup demonstrated at **8.36 μs**; JOCN sets a benchmark target of **20 μs (including hardware actuation)**.

Thus, the performance ceiling originates in **the cost structure of SDN stacks, not CPU clock speeds**. For slow MEMS switches (30–200 ms), this ceiling is irrelevant: a 1.5 ms control budget accounts for less than 5% of physical switching time. For silicon photonics (~200 μs), it dictates whether standard control architectures can support dynamic switching at all. Achieving sub-200 μs end-to-end reconfiguration requires driver-level programming (a single write through `bf_switchd` on our platform consumes 0.35–0.72 ms); protocol and process tuning alone are insufficient.

## Scenario Trade-offs: How to Combine These Three Choices?
No single combination of these three design dimensions is universally optimal. The same interface means very different things for low-frequency topology adjustments versus per-phase reconfigurations: the former easily amortizes configuration overhead, whereas the latter forces applications to wait before every communication round. Combining these choices requires evaluating two primary application conditions: how much control latency the application budget allows, and whether reconfiguration targets can be known in advance.

| Control Budget | Targets Determined Dynamically On Demand | Targets Predictable & Prepared in Advance |
| --- | --- | --- |
| Generous (e.g., several ms allowed) | Prioritize generic management interfaces and process isolation; monitor queue lengths | Retain generic interfaces; evaluate whether pre-staging adds sufficient value |
| Tight (e.g., tens to hundreds of μs allowed) | Minimize mandatory coordination and handoffs; inspect online translation, SDK overhead, and tail latencies | Investigate pre-generation, pre-loading, timed, or local triggering; verify hardware capabilities |

*The right-hand column assumes predictable workloads; our experiments have not yet validated the end-to-end benefits of pre-loading and fast activation. Even when pre-staging is feasible, memory footprints, schedule deviations, clock drift, and cancellation recovery consume latency budgets. Conversely, a scenario with ultra-fast actuators but infrequent reconfigurations can reasonably prioritize process isolation and maintainability.*

Returning to the three dimensions: resource partitionability, interoperability requirements, software isolation needs, and underlying hardware concurrency shape the design space. A tight latency budget does not automatically imply choosing the most stripped-down implementation along all three axes; it may instead motivate hardware-level pre-loading and localized triggering.

For any specific system, the engineering sequence should be: first define when the application requires the path and what criteria constitute readiness; next isolate the coordination and execution steps that must execute online; finally synthesize an implementation that satisfies the latency budget. Verifying how many steps vendor APIs require, how many bytes configurations consume, how many channels actuators drive concurrently, and how many feedback iterations are needed must be an integral part of this design process.

## Conclusion: Aligning Control Architectures with Device Speeds
Returning to the initial disparity: when physical switching drops from 20 ms to 200 μs, a 5 ms serial control path leaves complete reconfiguration trapped in the millisecond domain. Translating device advances into faster application-level connectivity requires first separating planning from execution, and then systematically evaluating ① Controller Participation, ② Control Interface & Protocol, and ③ Device Execution Architecture, verifying whether low-level drivers and feedback loops can meet the budget.

Materials from the OCP Fast OCS initiative already call for unified end-to-end metrics, and the Zero-Touch OCS presentation explicitly argued that "A common API stack is not enough," demanding shared service semantics and path validation. This article grounds that challenge in the execution architecture: which operations should move offline, which entities must participate in every reconfiguration, and can the remaining online work satisfy application latency budgets?

Our Tofino observations offer partial answers: reducing software overhead makes new paths observable earlier, differing device process layouts change non-device latency by hundreds of microseconds, and vendor runtime calls can dominate the remaining budget even after upper layers are streamlined. While complete validation on physical optical hardware and standalone controller benchmarks remains ongoing work, the architectural mandate is clear: **every time physical devices speed up by an order of magnitude, the control architecture must re-justify its latency budget. Otherwise, microsecond optical switches will continue to deliver millisecond reality.**

## References and Reading Notes
- [Mission Apollo: Landing Optical Circuit Switching at Datacenter Scale](https://arxiv.org/abs/2208.10041), Section 4.1, Figure 5, and Figure 8a: Palomar CPU, FPGA, high-voltage driver, and MEMS feedback control implementation. The 10–20 ms figure cited includes control software and mirror configuration, and cannot be treated solely as actuator motion time.
- [Integrated electronic controller for dynamic self-configuration of photonic circuits](https://arxiv.org/abs/2501.09664v1), reading the arXiv v1 main text, methods, and supplementary materials, especially Figures 1, 3, and Supplementary Figure 5: multichannel MZI control, ~10 μs thermal actuator response, and ~10 ms convergence across a 4-stage mesh; evaluated on an adaptive beam coupler.
- [General-purpose programmable photonic processor for advanced radiofrequency applications](https://www.nature.com/articles/s41467-024-45888-7), Nature Communications 15, 1563 (2024): Figure 2 and "The general-purpose photonic processor" section describe optical, electronic, and software layers, alongside driver-dominated 15–90 ms setting/readout latencies; not a commercial OCS benchmark.
- [λλ: A Programming Language for Silicon Photonics](https://dl.acm.org/doi/10.1145/3789240.3829151) and [open-source compiler repository](https://github.com/vaibhavmehta1/LambdaLambda): verified abstract and codebase illustrate compilation and hardware mapping; compilation performance has not been verified against full texts and is not used to infer per-switch reconfiguration latency.
- K. Sato, [Optical switching will innovate intra data center networks](https://doi.org/10.1364/JOCN.495006) (JOCN Invited Tutorial), J. Opt. Commun. Netw. 16(1), A1–A23 (2024): Section 7 outlines a 20 μs link-establishment target, FPGA 500 ns decision latency, and 8.36 μs measured link setup; cited here as an architectural baseline without extrapolating experimental conditions.
- [iPronics ONE-32 Announcement](https://ipronics.com/ipronics-unveils-worlds-first-silicon-photonics-optical-circuit-switch-for-ai-driven-data-centers-optical-network-transformation/), 2025-03-31: vendor-reported `<300 μs` reconfiguration specification and May 2025 availability schedule. This is distinct from the `<30 ns` optical path propagation delay; device specifications have not been independently verified.
- [iPronics ONE Product Page](https://ipronics.com/product/), accessed 2026-10-16: sub-millisecond reconfiguration claims and manufacturing roadmap; supplementary reading.
- [iPronics Raises $125 Million to Scale Programmable Optical Networking for AI Data Centers](https://ipronics.com/ipronics-raises-125-million-to-scale-programmable-optical-networking-for-ai-data-centers/), 2026-09-02: company announcement of Series B funding with NVIDIA participation.
- [Fast switching OCS for AI and Hyperscale Data Centers: Benefits, Technologies and Emerging Applications](https://drive.google.com/file/d/1G0o39qR0wZvvHHfMTyiAe2mB6j5HxelF/view), James Burch (Lumotive) and Vivek Sarathy (iPronics): Slides 3 and 5 discuss technology timescales, Slides 6–9 address use cases, and Slide 10 outlines end-to-end metrics and co-design requirements. Actuator timescales in technology roadmaps do not equate to full switch completion times.
- [Optically-Accelerated AI Clusters Enabled by Interoperable Multi-Vendor OCSs: Use Cases and Opportunities](https://drive.google.com/file/d/1ahYwnygxq6vzFGNZWNrXO7r9fLLhCvg7/view), Iwan Kartawira (iPronics) and Kazuaki Ueda (KDDI), with contributions from Kazuya Anazawa (NTT): Slides 5–6 and 9–13 present CPO + OCS + SDN architecture, workload optimization, multi-vendor paths, and roadmap items; cited without inferring measured control latencies.
- [Zero-Touch OCS Fabrics for AI Scale-Out Infrastructure at Hyperscale](https://drive.google.com/file/d/1c2YvZLrfVKRWOfFyc-rVGNZ9kGx56EaH/view), Kannan Raj and Michael DeMerchant: Slides 6–9 and 13 discuss shared models, service semantics, path verification, closed-loop changes, and consistency requirements.
- [OCP Optical Circuit Switching Project](https://www.opencompute.org/community/optical-circuit-switching): verified project charter, SDN integration direction, and leadership roster; specific presentations cited for architecture and use cases.
- [RFC 7950: The YANG 1.1 Data Modeling Language](https://www.rfc-editor.org/rfc/rfc7950.html), especially Sections 1 and 4: modeling language and representations of configuration, state, and RPC operations.
- [gNMI Specification](https://github.com/openconfig/reference/blob/master/rpc/gnmi/gnmi-specification.md), especially Sections 3.4.2 and 3.4.3: Set responses and transaction semantics; cited from online version accessed during this review.
- [ReconfigNet-Sim: Simulation Principles and Boundaries](https://github.com/ZER0-Nu1L/ReconfigNet-Sim/blob/main/docs/ocs-simulation-principles-and-boundaries.md), [Control Semantics](https://github.com/ZER0-Nu1L/ReconfigNet-Sim/blob/main/docs/ocs-control-semantics.md), and [Agent Architecture](https://github.com/ZER0-Nu1L/ReconfigNet-Sim/blob/main/docs/ocs-agent-architecture.md): design abstractions and limitations of portable switch emulation.

*The experimental data presented here represent a de-identified summary of empirical explorations; raw logs, deployment configurations, and source snapshots remain archived in private lab records. They provide architectural evidence rather than an open, independently reproducible benchmark; official interfaces in public repositories do not yet incorporate all experimental variants. Detailed sources, public citations, and follow-up validation requirements are maintained in `sources.md` in the writing workspace.*
