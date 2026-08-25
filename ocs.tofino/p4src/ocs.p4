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

    Checksum() ipv4_checksum;

    apply {
        if (hdr.ipv4.isValid()) {
            hdr.ipv4.hdr_checksum = ipv4_checksum.update({
                hdr.ipv4.version,
                hdr.ipv4.ihl,
                hdr.ipv4.diffserv,
                hdr.ipv4.total_len,
                hdr.ipv4.identification,
                hdr.ipv4.flags,
                hdr.ipv4.frag_offset,
                hdr.ipv4.ttl,
                hdr.ipv4.protocol,
                hdr.ipv4.src_addr,
                hdr.ipv4.dst_addr});
        }
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

    // Using ALPM to replace traditional LPM
    Alpm(number_partitions = 1024, subtrees_per_partition = 2) algo_lpm;


    action _drop() {
        ig_intr_dprsr_md.drop_ctl = 0x1; // TNA
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
        alpm = algo_lpm;
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


    table ocs_mapping {
        key = {
            ig_intr_md.ingress_port : exact;
            ig_intr_tm_md.ucast_egress_port : exact;
        }
        actions = {
            NoAction;
            _drop;
        }
        size = 64;
        const default_action = _drop;
    }

    apply {
        if (hdr.ipv4.isValid()) {
            if (hdr.ipv4.ttl <= 1) {
                _drop();
                return;
            }
          
            ipv4_lpm.apply();
            forward.apply();
            ocs_mapping.apply();
            
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