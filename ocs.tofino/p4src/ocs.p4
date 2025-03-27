#include <core.p4>
#if __TARGET_TOFINO__ == 2
#include <t2na.p4>
#else
#include <tna.p4>
#endif

#include "common/headers.p4"
#include "common/util.p4"

struct metadata_t {
    bit<32> nhop_ipv4;
}

// ---------------------------------------------------------------------------
// Ingress parser
// ---------------------------------------------------------------------------

parser SwitchIngressParser(
    packet_in pkt,
    out header_t hdr,
    out metadata_t ig_md,
    out ingress_intrinsic_metadata_t ig_intr_md) {

    TofinoIngressParser() tofino_parser;

    state start {
        tofino_parser.apply(pkt, ig_intr_md);
        transition parse_ethernet;
    }

    state parse_ethernet {
        pkt.extract(hdr.ethernet);
        transition select(hdr.ethernet.ether_type) {
            ETHERTYPE_IPV4: parse_ipv4;
            default: accept;
        }
    }

    state parse_ipv4 {
        pkt.extract(hdr.ipv4);
        transition accept;
    }
}

// ---------------------------------------------------------------------------
// Ingress Deparser
// ---------------------------------------------------------------------------
control SwitchIngressDeparser(
        packet_out pkt,
        inout header_t hdr,
        in metadata_t ig_md,
        in ingress_intrinsic_metadata_for_deparser_t ig_intr_dprsr_md) {

    apply {
        pkt.emit(hdr);
    }
}

control SwitchIngress(
    inout header_t hdr,
    inout metadata_t ig_md,
    in ingress_intrinsic_metadata_t ig_intr_md,
    in ingress_intrinsic_metadata_from_parser_t ig_intr_prsr_md,
    inout ingress_intrinsic_metadata_for_deparser_t ig_intr_dprsr_md,
    inout ingress_intrinsic_metadata_for_tm_t ig_intr_tm_md) {

    action _drop() {
        ig_intr_dprsr_md.drop_ctl = 0x1;
    }

    action set_nhop(bit<32> nhop_ipv4, bit<9> port) {
        ig_md.nhop_ipv4 = nhop_ipv4;
        ig_intr_tm_md.ucast_egress_port = port;
        hdr.ipv4.ttl = hdr.ipv4.ttl - 1;
    }

    action set_dmac(mac_addr_t dmac) {
        hdr.ethernet.dst_addr = dmac;
    }

    table ipv4_lpm {
        key = {
            hdr.ipv4.dst_addr: lpm;
        }
        actions = {
            set_nhop;
            _drop;
        }
        size = 1024;
        default_action = _drop();
    }

    table forward {
        key = {
            ig_md.nhop_ipv4: exact;
        }
        actions = {
            set_dmac;
            _drop;
        }
        size = 512;
        default_action = _drop();
    }

    apply {
        if (hdr.ipv4.isValid()) {
            if (hdr.ipv4.ttl <= 0) {
                ig_intr_dprsr_md.drop_ctl = 0x1;
                return;
            }
          
            ipv4_lpm.apply();
            if (ig_intr_dprsr_md.drop_ctl == 0x0) {
                forward.apply();
            }
          
            ig_intr_tm_md.bypass_egress = 1w1;
        }
    }
}

Pipeline(SwitchIngressParser(),
         SwitchIngress(),
         SwitchIngressDeparser(),
         EmptyEgressParser(),
         EmptyEgress(),
         EmptyEgressDeparser()) pipe;

Switch(pipe) main;