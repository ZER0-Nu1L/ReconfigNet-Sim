#include <core.p4>
#include <v1model.p4>

#include "include/header.p4"
#include "include/parser.p4"

control ingress(inout headers hdr, inout metadata meta, inout standard_metadata_t standard_metadata) {
    action _drop() {
        mark_to_drop(standard_metadata);
    }
    action set_nhop(bit<32> nhop_ipv4, bit<9> port) {
        meta.ingress_metadata.nhop_ipv4 = nhop_ipv4;
        standard_metadata.egress_spec = port;
        hdr.ipv4.ttl = hdr.ipv4.ttl + 1;
    }
    action set_dmac(bit<48> dmac) {
        hdr.ethernet.dstAddr = dmac;
    }
    // action set_egress_port(bit<9> port) {
    //     standard_metadata.egress_spec = port;
    // }

    // table arp_forward {
    //     key = {
    //         hdr.arp.tpa: exact;
    //     }
    //     actions = {
    //         set_egress_port;
    //         _drop;
    //         NoAction;
    //     }
    //     size = 256;
    //     default_action = _drop();
    // }

    table ipv4_lpm {
        actions = {
            _drop;
            set_nhop;
            NoAction;
        }
        key = {
            hdr.ipv4.dstAddr: lpm;
        }
        size = 1024;
        default_action = _drop();
    }
    table forward {
        actions = {
            set_dmac;
            _drop;
            NoAction;
        }
        key = {
            meta.ingress_metadata.nhop_ipv4: exact;
        }
        size = 512;
        default_action = NoAction();
    }
    apply {
        // if (hdr.arp.isValid()) {
        //     arp_forward.apply();
        // }
        if (hdr.ipv4.isValid() && hdr.ipv4.ttl > 0) {
          ipv4_lpm.apply();
          forward.apply();
        }
    }
}

control egress(inout headers hdr, inout metadata meta, inout standard_metadata_t standard_metadata) {
    action rewrite_mac(bit<48> smac) {
        hdr.ethernet.srcAddr = smac;
    }
    action _drop() {
        mark_to_drop(standard_metadata);
    }
    table send_frame {
        actions = {
            rewrite_mac;
            _drop;
            NoAction;
        }
        key = {
            standard_metadata.egress_port: exact;
        }
        size = 256;
        default_action = NoAction();
    }

    table ocs_mapping {
        key = {
            standard_metadata.ingress_port: exact;
            standard_metadata.egress_port: exact;
        }
        actions = {
            NoAction;
            _drop;
        }
        size = 64;
        default_action = _drop();
    }

    apply {
        if (hdr.ipv4.isValid()) { // || hdr.arp.isValid()
          ocs_mapping.apply();
          send_frame.apply();
        }
    }
}

V1Switch(ParserImpl(), verifyChecksum(), ingress(), egress(), computeChecksum(), DeparserImpl()) main;