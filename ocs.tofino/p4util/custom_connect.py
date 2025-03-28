# NOTE: custom connect with hard codes
import json, os
# from scapy.all import get_if_list
# NOTE: 

def load_config():
    '''
    Load configuration from JSON file
    '''
    config_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../config/project_conf.json')
    if not os.path.exists(config_file):
        print("Warning: Configuration file not found. Using default configuration.")
        config = {}
    else:
        with open(config_file, 'r') as file:
            config = json.load(file)
    # # use case:
    # num_hosts = config.get('num_hosts', 8)
    return config

def hostIP(hostID, mask=False, mode='l3'):
    if mode == 'l3':
        if mask == True:
            return "10.0.%d.10/24" % (hostID)
        else:
            return "10.0.%d.10" % (hostID)
    elif mode == 'l2':
        if mask == True:
            return "10.0.10.%d/24" % (hostID)
        else:
            return "10.0.10.%d" % (hostID)
    else:
        assert mode != 'l2' and mode != 'l3'
        exit(1)

def hostMAC(hostID):
    return '00:00:00:00:00:%02x' % (hostID)

def switchIP(hostID, mode='l3'):
    if mode == 'l3':
        return "10.0.%d.1" % (hostID)
    elif mode == 'l2':
        return "10.0.10.0" % (hostID)
    else:
        assert mode != 'l2' and mode != 'l3'
        exit(1)

def switchMAC(hostID):
    return '00:aa:bb:00:00:%02x' % (hostID)


'''
NOTE: Tofino virtual port mapping
+----------------+                      +----------+
|  Tofino Model  |                      |   Host   |
|                |                      |          |
|       Port 0  -+-  veth0  ─── veth1  -+- host1   |
|       Port 1  -+-  veth2  ─── veth3  -+- host2   |
|       Port 2  -+-  veth4  ─── veth5  -+- host3   |
|       Port 3  -+-  veth6  ─── veth7  -+- host4   |
|                |                      |          |
|        ...    -+-   ...   ───  ...   -+-  ...    |
|                |                      |          |
|       Port i  -+-   2*i   ─── 2*i+1  -+-  i-1    |
|                |                      |          |
|        ...    -+-  .....  ───  ...   -+-  ...    |
|                |                      |          |
|       Port 31 -+-  veth62 ─── veth63 -+- host32  |
|                |                      |          |
+----------------+                      +----------+
'''

full_if_list = [ 'veth1', 'veth0', 'veth3', 'veth2', 'veth5', 'veth4', 'veth7', 'veth6', 'veth9', 'veth8', 'veth11', 'veth10', 'veth13', 'veth12', 'veth15', 'veth14', 'veth17', 'veth16', 'veth19', 'veth18', 'veth21', 'veth20', 'veth23', 'veth22', 'veth25', 'veth24', 'veth27', 'veth26', 'veth29', 'veth28', 'veth31', 'veth30', 'veth33', 'veth32', 'veth35', 'veth34', 'veth37', 'veth36', 'veth39', 'veth38', 'veth41', 'veth40', 'veth43', 'veth42', 'veth45', 'veth44', 'veth47', 'veth46', 'veth49', 'veth48', 'veth51', 'veth50', 'veth53', 'veth52', 'veth55', 'veth54', 'veth57', 'veth56', 'veth59', 'veth58', 'veth61', 'veth60', 'veth63', 'veth62', 'veth251', 'veth250']

def get_host_interface(hostID):
    iface_name = "veth" + str(int(hostID * 2 - 1))    

    if iface_name in full_if_list:
        return iface_name
    else:
        print("ERROR: Cannot find host %d interface" % hostID)
        exit(1)

def get_switch_port(hostID):
    return hostID - 1;