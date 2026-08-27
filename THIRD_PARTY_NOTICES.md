# Third-party notices

ReconfigNet-Sim is distributed under the MIT License in the root
[`LICENSE`](LICENSE) file. The components listed below retain their own
copyright and license terms.

## Intel Open P4 Studio common P4 sources

The following files under `targets/tofino/p4/common/` are based on the
publicly released Open P4 Studio files below:

- [`headers.p4`](targets/tofino/p4/common/headers.p4) is based on
  [`p4_16_programs/common/headers.p4`](https://github.com/p4lang/open-p4studio/blob/0e81a468930b6f29ddf7744250b472d709944dcc/pkgsrc/p4-examples/p4_16_programs/common/headers.p4).
  Its functional body matches that upstream file.
- [`util.p4`](targets/tofino/p4/common/util.p4) is based on
  [`p4_16_programs/common/util.p4`](https://github.com/p4lang/open-p4studio/blob/0e81a468930b6f29ddf7744250b472d709944dcc/pkgsrc/p4-examples/p4_16_programs/common/util.p4).
  ReconfigNet-Sim keeps two local egress parser/deparser changes needed by
  its OCS pipeline; those changes are identified in the file header.

The upstream files are Copyright (C) 2024 Intel Corporation and are licensed
under the Apache License, Version 2.0. The license text is included in
[`LICENSES/Apache-2.0.txt`](LICENSES/Apache-2.0.txt).

The Open P4 Studio project is an independent upstream project. Mentioning it
here is an attribution and provenance record, not an endorsement by Intel or
an indication that ReconfigNet-Sim is an Intel product.

## P4App

[`third_party/p4app`](third_party/p4app) is a pinned git submodule from
[p4lang/p4app](https://github.com/p4lang/p4app), currently at commit
`c04625eacbe6febe3f0255bf9ea3f79829d3e1a6`. Its source distribution includes
its own Apache License 2.0 text at
[`third_party/p4app/LICENSE`](third_party/p4app/LICENSE).

## OpenConfig gNMI

The Go Agent depends on
[`github.com/openconfig/gnmi`](https://github.com/openconfig/gnmi) version
`v0.14.1`, as declared in [`agent/go/go.mod`](agent/go/go.mod). The checked-in
Python gNMI bindings are generated from the upstream gNMI protocol sources.
The upstream project is Apache-2.0 licensed; its notices apply to those
generated bindings and to the dependency obtained during Go builds.

## Generated code and runtime dependencies

Protocol-buffer files under `agent/go/gen/` and
`agent/python/ocs_agent/proto/` are generated artifacts. The `.proto` files
under `agent/proto/` are authored for this project; generated gNMI files retain
the upstream package metadata. Runtime dependencies are installed from their
respective package registries and are not vendored by this repository; users
must observe the license terms distributed by those packages.
