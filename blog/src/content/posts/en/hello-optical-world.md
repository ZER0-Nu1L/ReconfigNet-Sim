---
title: Hello Optical World
author: Changbo Wu
description: A place to share what building ReconfigNet-Sim teaches us about the integration, control, and deployment problems surrounding reconfigurable optical networks.
date: "2026-09-15"
draft: false
---

Welcome to the ReconfigNet-Sim blog.

Reconfigurable optical networks raise questions that reach well beyond the switch itself. When connectivity changes, controllers, endpoints, deployment tooling, and recovery procedures must all adapt in concert. Our goal is to make those integration problems easier to investigate.

## Why we are here

[ReconfigNet-Sim](https://github.com/ZER0-Nu1L/ReconfigNet-Sim) uses programmable switches to surface and examine these problems before real optical hardware is available. It gives us a place to build the system around the switch, make control behavior measurable, and test our assumptions early.

The boundary matters. Packet-switch emulation covers behavior at the control and integration layer — control logic, connection workflows, validation and recovery, and deployment assumptions — but not optical propagation, signal quality, physical switching mechanisms, or link reacquisition. We will stay explicit about the line between what we observe and what those observations can support; any claim about a real OCS still needs physical-hardware evidence.

## What we will share

This blog is a home for the research notes and engineering lessons that come out of that work: the questions we run into, the choices we make, and the evidence that helps us understand the system. We aim to make those lessons useful to everyone working on reconfigurable networks — researchers and industry practitioners alike, whether you are studying them or building, deploying, and operating them.

Posts will be available in both English and Chinese, with links between the two versions. For the implementation and its current boundaries, start with the [project README](https://github.com/ZER0-Nu1L/ReconfigNet-Sim#readme) and the [simulation boundary document](https://github.com/ZER0-Nu1L/ReconfigNet-Sim/blob/main/docs/ocs-simulation-principles-and-boundaries.md).

Hello, optical world. Beyond the optics, there is a whole system to build.
