# OCS control semantics

This repository implements an IPv4/MAC packet-level simulation of an optical
circuit switch. It is not a transparent optical OCS: the P4 pipeline parses
packets, decrements TTL, recomputes the IPv4 header checksum and applies
endpoint forwarding entries.

An OCS mapping is a fixed-point-free symmetric permutation. Every slot is
paired with exactly one different slot, and applying the mapping twice returns
the source slot. The `debug` mode instead permits every non-self source and
destination pair. Full mesh is diagnostic behavior and is not an OCS mapping.

Mode and mapping changes use break-before-make table programming. The old
entries are removed before an optional requested gap and the new entries are
then installed. The requested `delay_us` or `delay_ms` simulates a physical
reconfiguration gap; it is not a measurement of optical hardware timing.

REST updates are serialized, idempotent and revisioned. A no-op request does
not clear tables or increment the revision. A failed programming operation
attempts to restore the previous entries and reports the failure. BMv2/P4App
and Tofino BFRT adapters should retain matching observable semantics.

ARP is not forwarded by the current pipeline, so hardware experiments may
need static neighbors. That is a testbed constraint, not a property of a real
OCS. Site addresses, MACs, physical ports and controller listen addresses do
not belong in this repository; Tofino startup requires an explicit
`OCS_CONFIG_FILE` deployment profile.

The checked-in gRPC schema is a proposal until a server implementation and
interoperability tests are present.
