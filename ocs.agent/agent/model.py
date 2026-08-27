import collections
import re

from agent.errors import ConflictError, FailedPreconditionError, ValidationError


_PORT_NAME_RE = re.compile(r'^port-([1-9][0-9]*)$')


class Port(object):
    def __init__(self, name, index):
        self.name = name
        self.index = index

    def as_dict(self):
        return {'name': self.name, 'index': self.index}


class PortInventory(object):
    def __init__(self, ports):
        normalized = []
        for item in ports:
            if isinstance(item, str):
                match = _PORT_NAME_RE.match(item)
                if not match:
                    raise ValidationError(
                        'Port name must use port-<slot>: {}'.format(item))
                normalized.append(Port(item, int(match.group(1))))
                continue
            if not isinstance(item, dict):
                raise ValidationError('Each port must be a string or object')
            name = item.get('name')
            index = item.get('index')
            match = _PORT_NAME_RE.match(name or '')
            if not match:
                raise ValidationError(
                    'Port name must use port-<slot>: {}'.format(name))
            inferred_index = int(match.group(1))
            if index is None:
                index = inferred_index
            if (isinstance(index, bool) or not isinstance(index, int) or
                    index != inferred_index):
                raise ValidationError(
                    'Port {} index must be {}'.format(name, inferred_index))
            normalized.append(Port(name, index))

        normalized.sort(key=lambda port: port.index)
        if len(normalized) < 2 or len(normalized) > 8 or len(normalized) % 2:
            raise ValidationError(
                'Port count must be an even integer between 2 and 8')
        expected = list(range(1, len(normalized) + 1))
        indexes = [port.index for port in normalized]
        if indexes != expected:
            raise ValidationError(
                'Port indexes must be exactly 1..{}'.format(len(normalized)))
        names = [port.name for port in normalized]
        if len(names) != len(set(names)):
            raise ValidationError('Port names must be unique')

        self._ports = tuple(normalized)
        self._by_name = dict((port.name, port) for port in normalized)

    def __len__(self):
        return len(self._ports)

    def __iter__(self):
        return iter(self._ports)

    @property
    def names(self):
        return tuple(port.name for port in self._ports)

    def require(self, name):
        try:
            return self._by_name[name]
        except KeyError:
            raise ValidationError('Unknown port {}'.format(name))

    def by_index(self, index):
        if (isinstance(index, bool) or not isinstance(index, int) or
                not 1 <= index <= len(self._ports)):
            raise ValidationError('Unknown port index {}'.format(index))
        return self._ports[index - 1]


class Connection(object):
    def __init__(self, name, near_port_name, far_port_name,
                 bidirectional=True):
        if not isinstance(name, str) or not name:
            raise ValidationError('connection-name must be a non-empty string')
        if not isinstance(near_port_name, str) or not near_port_name:
            raise ValidationError('near-port-name must be a non-empty string')
        if not isinstance(far_port_name, str) or not far_port_name:
            raise ValidationError('far-port-name must be a non-empty string')
        if bidirectional is not True:
            raise ValidationError(
                'Only bidirectional point-to-point connections are supported')
        self.name = name
        self.near_port_name = near_port_name
        self.far_port_name = far_port_name
        self.bidirectional = True

    def __eq__(self, other):
        return (
            isinstance(other, Connection) and
            self.name == other.name and
            self.near_port_name == other.near_port_name and
            self.far_port_name == other.far_port_name and
            self.bidirectional == other.bidirectional)

    def __ne__(self, other):
        return not self.__eq__(other)

    def as_config_dict(self):
        return {
            'connection-name': self.name,
            'bidirectional': self.bidirectional,
            'near-port-name': self.near_port_name,
            'far-port-name': self.far_port_name,
        }


class ConnectionSet(object):
    def __init__(self, inventory, connections=None):
        self.inventory = inventory
        by_name = collections.OrderedDict()
        occupied = {}
        for connection in connections or []:
            if not isinstance(connection, Connection):
                raise ValidationError('ConnectionSet requires Connection objects')
            if connection.name in by_name:
                raise ValidationError(
                    'Duplicate connection-name {}'.format(connection.name))
            inventory.require(connection.near_port_name)
            inventory.require(connection.far_port_name)
            if connection.near_port_name == connection.far_port_name:
                raise ValidationError(
                    'Connection {} must not connect a port to itself'.format(
                        connection.name))
            for port_name in (
                    connection.near_port_name, connection.far_port_name):
                if port_name in occupied:
                    raise ConflictError(port_name, occupied[port_name])
                occupied[port_name] = connection.name
            by_name[connection.name] = connection
        self._by_name = by_name
        self._occupied = occupied

    def __len__(self):
        return len(self._by_name)

    def __iter__(self):
        return iter(self._by_name.values())

    def __eq__(self, other):
        return (
            isinstance(other, ConnectionSet) and
            dict(self._by_name) == dict(other._by_name))

    def __ne__(self, other):
        return not self.__eq__(other)

    def get(self, name):
        return self._by_name.get(name)

    def require(self, name):
        connection = self.get(name)
        if connection is None:
            raise FailedPreconditionError(
                'Unknown connection {}'.format(name),
                {'connection_name': name})
        return connection

    def replace(self, connection):
        connections = [
            item for item in self if item.name != connection.name]
        connections.append(connection)
        return ConnectionSet(self.inventory, connections)

    def delete(self, name):
        self.require(name)
        return ConnectionSet(
            self.inventory,
            [item for item in self if item.name != name])

    def directed_pairs(self):
        pairs = set()
        for connection in self:
            near = self.inventory.require(connection.near_port_name).index
            far = self.inventory.require(connection.far_port_name).index
            pairs.add((near, far))
            pairs.add((far, near))
        return pairs

    def diff(self, target):
        previous_pairs = self.directed_pairs()
        target_pairs = target.directed_pairs()
        return previous_pairs - target_pairs, target_pairs - previous_pairs

    def to_permutation(self):
        if len(self._occupied) != len(self.inventory):
            raise FailedPreconditionError(
                'Active connections are sparse and cannot be represented as pi',
                {
                    'connected_ports': len(self._occupied),
                    'total_ports': len(self.inventory),
                })
        mapping = [0] * len(self.inventory)
        for source, destination in self.directed_pairs():
            mapping[source - 1] = destination
        if any(value == 0 for value in mapping):
            raise FailedPreconditionError(
                'Active connections cannot be represented as pi')
        return mapping

    @classmethod
    def from_permutation(cls, inventory, mapping):
        if not isinstance(mapping, list) or len(mapping) != len(inventory):
            raise ValidationError(
                'pi must contain exactly {} slots'.format(len(inventory)))
        for value in mapping:
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValidationError('pi entries must be integers')
        expected = list(range(1, len(inventory) + 1))
        if sorted(mapping) != expected:
            raise ValidationError(
                'pi must be a permutation of 1..{}'.format(len(inventory)))

        connections = []
        visited = set()
        for source, destination in enumerate(mapping, start=1):
            if source == destination:
                raise ValidationError('pi must not contain self mappings')
            if mapping[destination - 1] != source:
                raise ValidationError('pi must contain symmetric two-way pairs')
            if source in visited:
                continue
            visited.add(source)
            visited.add(destination)
            low = min(source, destination)
            high = max(source, destination)
            near = inventory.by_index(low).name
            far = inventory.by_index(high).name
            connections.append(Connection(
                'conn-{}-{}'.format(near, far), near, far, True))
        return cls(inventory, connections)

    def as_list(self, runtime_status='ready', mode='ocs'):
        if runtime_status == 'error':
            status = 'FAILED'
        elif mode != 'ocs':
            status = 'UNKNOWN'
        else:
            status = 'CONNECTED'
        result = []
        for connection in self:
            config = connection.as_config_dict()
            state = dict(config)
            state['status'] = status
            result.append({
                'connection-name': connection.name,
                'config': config,
                'state': state,
            })
        return result

    def openconfig_tree(self, runtime_status='ready', mode='ocs'):
        peers = {}
        for connection in self:
            peers[connection.near_port_name] = connection.far_port_name
            peers[connection.far_port_name] = connection.near_port_name

        components = []
        for port in self.inventory:
            peer = peers.get(port.name)
            if runtime_status == 'error':
                port_status = 'FAILED'
                connected = False
            elif mode != 'ocs':
                port_status = 'BLOCKED'
                connected = False
            elif peer is None:
                port_status = 'OFF'
                connected = False
            else:
                port_status = 'TUNED'
                connected = True
            connection_state = {'connected': connected}
            if peer is not None:
                connection_state['peer'] = peer
            components.append({
                'name': port.name,
                'config': {'name': port.name},
                'state': {
                    'name': port.name,
                    'type': 'OCP_OCS_PORT',
                },
                'ocp-ocs-port': {
                    'state': {
                        'enabled': True,
                        'index': port.index,
                        'status': port_status,
                        'connection': connection_state,
                    },
                },
            })

        return {
            'oc-optical-switch:optical-switch': {
                'config': {},
                'state': {},
                'port-connection-recovery': {
                    'state': {
                        'port-connection-recovery-capability': 'NO_RECOVERY',
                    },
                },
            },
            'openconfig-platform:components': {
                'component': components,
            },
            'oc-optical-switch-connections:optical-switch-connections': {
                'port-connection': self.as_list(runtime_status, mode),
            },
        }
