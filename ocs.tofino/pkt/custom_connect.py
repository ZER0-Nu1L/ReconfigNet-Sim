

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