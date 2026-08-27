# NOTE: custom_topo with hard codes
from mininet.topo import Topo
from mininet.log import info

switch_name = "s1"

def hostIP(i, mask=False, mode='l3'):
    if mode == 'l3':
        if mask == True:
            return "10.0.%d.10/24" % (i)
        else:
            return "10.0.%d.10" % (i)
    elif mode == 'l2':
        if mask == True:
            return "10.0.10.%d/24" % (i)
        else:
            return "10.0.10.%d" % (i)
    else:
        assert mode != 'l2' and mode != 'l3'
        exit(1)

def hostMAC(i):
    return '00:00:00:00:00:%02x' % (i)

def switchIP(i, mode='l3'):
    if mode == 'l3':
        return "10.0.%d.1" % (i)
    elif mode == 'l2':
        return "10.0.10.0" % (i)
    else:
        assert mode != 'l2' and mode != 'l3'
        exit(1)

def switchMAC(i):
    return '00:aa:bb:00:00:%02x' % (i)


class CustomTopo(Topo):
    def __init__(self, num_hosts, mode, **opts):
        Topo.__init__(self, **opts)
        switch = self.addSwitch(switch_name)

        for i in range(1, num_hosts+1):
            host = self.addHost('h%d' % i, ip = hostIP(i, mask=True), mac = hostMAC(i))
            self.addLink(host, switch, port2=i) # port2: dest port
            # NOTE: Only responsible for L2 connectivity
            # gateway, etc. does not seem to be set by default


def setup_host_entries(net, num_hosts, mode='l3'):
    for i in range(1, num_hosts+1):
        h = net.get('h%d' % (i))
        if mode == "l2":
            h.setDefaultRoute("dev %s" % h.defaultIntf().name)
        elif mode == "l3":
            h.setARP(switchIP(i), switchMAC(i))
            h.setDefaultRoute("dev %s via %s" % (h.defaultIntf().name, switchIP(i)))
        else:
            assert mode != 'l2' and mode != 'l3'
            exit()