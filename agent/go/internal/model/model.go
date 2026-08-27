package model

import (
	"fmt"
	"maps"
	"regexp"
	"slices"
)

var portNamePattern = regexp.MustCompile(`^port-([1-9][0-9]*)$`)

type Port struct {
	Name  string `yaml:"name" json:"name"`
	Index uint32 `yaml:"index" json:"index"`
}

type Inventory struct {
	ports  []Port
	byName map[string]Port
}

func NewInventory(ports []Port) (Inventory, error) {
	if len(ports) < 2 || len(ports) > 8 || len(ports)%2 != 0 {
		return Inventory{}, fmt.Errorf("port count must be an even integer between 2 and 8")
	}
	ports = slices.Clone(ports)
	slices.SortFunc(ports, func(a, b Port) int {
		return int(a.Index) - int(b.Index)
	})
	byName := make(map[string]Port, len(ports))
	for i, port := range ports {
		expected := uint32(i + 1)
		if !portNamePattern.MatchString(port.Name) {
			return Inventory{}, fmt.Errorf("port name must use port-<slot>: %s", port.Name)
		}
		if port.Index != expected || port.Name != fmt.Sprintf("port-%d", expected) {
			return Inventory{}, fmt.Errorf("port %s index must be %d", port.Name, expected)
		}
		if _, exists := byName[port.Name]; exists {
			return Inventory{}, fmt.Errorf("port names must be unique")
		}
		byName[port.Name] = port
	}
	return Inventory{ports: ports, byName: byName}, nil
}

func (i Inventory) Ports() []Port {
	return slices.Clone(i.ports)
}

func (i Inventory) Len() int {
	return len(i.ports)
}

func (i Inventory) Port(name string) (Port, bool) {
	port, ok := i.byName[name]
	return port, ok
}

func (i Inventory) ByIndex(index uint32) (Port, bool) {
	if index == 0 || int(index) > len(i.ports) {
		return Port{}, false
	}
	return i.ports[index-1], true
}

type Connection struct {
	Name          string `json:"connection-name"`
	Bidirectional bool   `json:"bidirectional"`
	NearPortName  string `json:"near-port-name"`
	FarPortName   string `json:"far-port-name"`
}

func ValidateConnection(inventory Inventory, connection Connection) error {
	if connection.Name == "" {
		return fmt.Errorf("connection-name must be a non-empty string")
	}
	if connection.NearPortName == "" {
		return fmt.Errorf("near-port-name must be a non-empty string")
	}
	if connection.FarPortName == "" {
		return fmt.Errorf("far-port-name must be a non-empty string")
	}
	if !connection.Bidirectional {
		return fmt.Errorf("only bidirectional point-to-point connections are supported")
	}
	if _, ok := inventory.Port(connection.NearPortName); !ok {
		return fmt.Errorf("unknown port %s", connection.NearPortName)
	}
	if _, ok := inventory.Port(connection.FarPortName); !ok {
		return fmt.Errorf("unknown port %s", connection.FarPortName)
	}
	if connection.NearPortName == connection.FarPortName {
		return fmt.Errorf("connection %s must not connect a port to itself", connection.Name)
	}
	return nil
}

type ConflictError struct {
	Port       string
	Connection string
}

func (e *ConflictError) Error() string {
	return fmt.Sprintf("port %s is already used by connection %s", e.Port, e.Connection)
}

type Pair struct {
	Ingress uint32
	Egress  uint32
}

type ConnectionSet struct {
	inventory Inventory
	byName    map[string]Connection
}

func NewConnectionSet(inventory Inventory, connections []Connection) (ConnectionSet, error) {
	byName := make(map[string]Connection, len(connections))
	occupied := make(map[string]string, len(connections)*2)
	for _, connection := range connections {
		if err := ValidateConnection(inventory, connection); err != nil {
			return ConnectionSet{}, err
		}
		if _, exists := byName[connection.Name]; exists {
			return ConnectionSet{}, fmt.Errorf("duplicate connection-name %s", connection.Name)
		}
		for _, port := range []string{connection.NearPortName, connection.FarPortName} {
			if owner, exists := occupied[port]; exists {
				return ConnectionSet{}, &ConflictError{Port: port, Connection: owner}
			}
			occupied[port] = connection.Name
		}
		byName[connection.Name] = connection
	}
	return ConnectionSet{inventory: inventory, byName: byName}, nil
}

func FromPermutation(inventory Inventory, permutation []uint32) (ConnectionSet, error) {
	if len(permutation) != inventory.Len() {
		return ConnectionSet{}, fmt.Errorf("pi must contain exactly %d slots", inventory.Len())
	}
	seen := make([]bool, inventory.Len()+1)
	for source, destination := range permutation {
		if destination == 0 || int(destination) > inventory.Len() || seen[destination] {
			return ConnectionSet{}, fmt.Errorf("pi must be a permutation of 1..%d", inventory.Len())
		}
		seen[destination] = true
		if destination == uint32(source+1) {
			return ConnectionSet{}, fmt.Errorf("pi must not contain self mappings")
		}
	}
	connections := make([]Connection, 0, inventory.Len()/2)
	visited := make([]bool, inventory.Len()+1)
	for sourceIndex, destination := range permutation {
		source := uint32(sourceIndex + 1)
		if permutation[destination-1] != source {
			return ConnectionSet{}, fmt.Errorf("pi must contain symmetric two-way pairs")
		}
		if visited[source] {
			continue
		}
		visited[source] = true
		visited[destination] = true
		low, high := min(source, destination), max(source, destination)
		near, _ := inventory.ByIndex(low)
		far, _ := inventory.ByIndex(high)
		connections = append(connections, Connection{
			Name:          fmt.Sprintf("conn-%s-%s", near.Name, far.Name),
			Bidirectional: true,
			NearPortName:  near.Name,
			FarPortName:   far.Name,
		})
	}
	return NewConnectionSet(inventory, connections)
}

func (s ConnectionSet) Connections() []Connection {
	names := slices.Sorted(maps.Keys(s.byName))
	connections := make([]Connection, 0, len(names))
	for _, name := range names {
		connections = append(connections, s.byName[name])
	}
	return connections
}

func (s ConnectionSet) Get(name string) (Connection, bool) {
	connection, ok := s.byName[name]
	return connection, ok
}

func (s ConnectionSet) Replace(connection Connection) (ConnectionSet, error) {
	connections := make([]Connection, 0, len(s.byName)+1)
	for _, existing := range s.Connections() {
		if existing.Name != connection.Name {
			connections = append(connections, existing)
		}
	}
	connections = append(connections, connection)
	return NewConnectionSet(s.inventory, connections)
}

func (s ConnectionSet) Delete(name string) (ConnectionSet, error) {
	if _, exists := s.byName[name]; !exists {
		return ConnectionSet{}, fmt.Errorf("unknown connection %s", name)
	}
	connections := make([]Connection, 0, len(s.byName)-1)
	for _, connection := range s.Connections() {
		if connection.Name != name {
			connections = append(connections, connection)
		}
	}
	return NewConnectionSet(s.inventory, connections)
}

func (s ConnectionSet) Pairs() map[Pair]struct{} {
	pairs := make(map[Pair]struct{}, len(s.byName)*2)
	for _, connection := range s.byName {
		near, _ := s.inventory.Port(connection.NearPortName)
		far, _ := s.inventory.Port(connection.FarPortName)
		pairs[Pair{Ingress: near.Index, Egress: far.Index}] = struct{}{}
		pairs[Pair{Ingress: far.Index, Egress: near.Index}] = struct{}{}
	}
	return pairs
}

func (s ConnectionSet) Equal(other ConnectionSet) bool {
	return maps.Equal(s.byName, other.byName)
}

func (s ConnectionSet) Permutation() ([]uint32, error) {
	if len(s.byName)*2 != s.inventory.Len() {
		return nil, fmt.Errorf("active connections are sparse and cannot be represented as pi")
	}
	permutation := make([]uint32, s.inventory.Len())
	for pair := range s.Pairs() {
		permutation[pair.Ingress-1] = pair.Egress
	}
	for _, destination := range permutation {
		if destination == 0 {
			return nil, fmt.Errorf("active connections cannot be represented as pi")
		}
	}
	return permutation, nil
}

func AllToAllPairs(inventory Inventory) map[Pair]struct{} {
	pairs := make(map[Pair]struct{}, inventory.Len()*(inventory.Len()-1))
	for _, source := range inventory.Ports() {
		for _, destination := range inventory.Ports() {
			if source.Index != destination.Index {
				pairs[Pair{Ingress: source.Index, Egress: destination.Index}] = struct{}{}
			}
		}
	}
	return pairs
}
