package model

import (
	"slices"
	"testing"
)

func testInventory(t *testing.T) Inventory {
	t.Helper()
	inventory, err := NewInventory([]Port{
		{Name: "port-1", Index: 1},
		{Name: "port-2", Index: 2},
		{Name: "port-3", Index: 3},
		{Name: "port-4", Index: 4},
		{Name: "port-5", Index: 5},
		{Name: "port-6", Index: 6},
		{Name: "port-7", Index: 7},
		{Name: "port-8", Index: 8},
	})
	if err != nil {
		t.Fatal(err)
	}
	return inventory
}

func TestPermutationRoundTrip(t *testing.T) {
	inventory := testInventory(t)
	want := []uint32{4, 3, 2, 1, 8, 7, 6, 5}
	connections, err := FromPermutation(inventory, want)
	if err != nil {
		t.Fatal(err)
	}
	got, err := connections.Permutation()
	if err != nil {
		t.Fatal(err)
	}
	if !slices.Equal(got, want) {
		t.Fatalf("permutation = %v, want %v", got, want)
	}
}

func TestConnectionConflictReportsOwner(t *testing.T) {
	inventory := testInventory(t)
	_, err := NewConnectionSet(inventory, []Connection{
		{
			Name: "first", Bidirectional: true,
			NearPortName: "port-1", FarPortName: "port-2",
		},
		{
			Name: "second", Bidirectional: true,
			NearPortName: "port-1", FarPortName: "port-3",
		},
	})
	conflict, ok := err.(*ConflictError)
	if !ok {
		t.Fatalf("error = %T %v, want *ConflictError", err, err)
	}
	if conflict.Port != "port-1" || conflict.Connection != "first" {
		t.Fatalf("conflict = %+v", conflict)
	}
}

func FuzzPermutationValidation(f *testing.F) {
	f.Add([]byte{2, 1, 4, 3, 6, 5, 8, 7})
	f.Add([]byte{1, 2, 3, 4, 5, 6, 7, 8})
	inventory := func() Inventory {
		value, err := NewInventory([]Port{
			{Name: "port-1", Index: 1}, {Name: "port-2", Index: 2},
			{Name: "port-3", Index: 3}, {Name: "port-4", Index: 4},
			{Name: "port-5", Index: 5}, {Name: "port-6", Index: 6},
			{Name: "port-7", Index: 7}, {Name: "port-8", Index: 8},
		})
		if err != nil {
			panic(err)
		}
		return value
	}()
	f.Fuzz(func(t *testing.T, data []byte) {
		permutation := make([]uint32, len(data))
		for index, value := range data {
			permutation[index] = uint32(value)
		}
		connections, err := FromPermutation(inventory, permutation)
		if err != nil {
			return
		}
		roundTrip, err := connections.Permutation()
		if err != nil {
			t.Fatal(err)
		}
		if !slices.Equal(roundTrip, permutation) {
			t.Fatalf("round trip = %v, input %v", roundTrip, permutation)
		}
	})
}
