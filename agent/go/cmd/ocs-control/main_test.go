package main

import (
	"reflect"
	"testing"

	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
)

func TestParsePI(t *testing.T) {
	got, err := parsePI("6, 3,2,5,4,1")
	if err != nil {
		t.Fatalf("parsePI returned error: %v", err)
	}
	want := []uint32{6, 3, 2, 5, 4, 1}
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("parsePI = %v, want %v", got, want)
	}
}

func TestParsePIRejectsMalformedInput(t *testing.T) {
	for _, value := range []string{"", "1,2,3", "1,two"} {
		if _, err := parsePI(value); err == nil {
			t.Errorf("parsePI(%q) unexpectedly succeeded", value)
		}
	}
}

func TestStrategy(t *testing.T) {
	got, err := strategy("FULL")
	if err != nil || got != ocsv1.ExecutionStrategy_EXECUTION_STRATEGY_FULL {
		t.Fatalf("strategy(FULL) = %v, %v", got, err)
	}
	got, err = strategy("delta")
	if err != nil || got != ocsv1.ExecutionStrategy_EXECUTION_STRATEGY_DELTA {
		t.Fatalf("strategy(delta) = %v, %v", got, err)
	}
	if _, err := strategy("typo"); err == nil {
		t.Fatal("strategy accepted an invalid value")
	}
}

func TestTransport(t *testing.T) {
	for _, value := range []string{"native-batch", "NATIVE_BATCH"} {
		got, err := transport(value)
		if err != nil || got != ocsv1.Transport_TRANSPORT_NATIVE_BATCH {
			t.Fatalf("transport(%q) = %v, %v", value, got, err)
		}
	}
	got, err := transport("sequential")
	if err != nil || got != ocsv1.Transport_TRANSPORT_SEQUENTIAL {
		t.Fatalf("transport(sequential) = %v, %v", got, err)
	}
	if _, err := transport("typo"); err == nil {
		t.Fatal("transport accepted an invalid value")
	}
}

func TestMode(t *testing.T) {
	got, err := mode("DEBUG")
	if err != nil || got != ocsv1.Mode_MODE_DEBUG {
		t.Fatalf("mode(DEBUG) = %v, %v", got, err)
	}
	got, err = mode("ocs")
	if err != nil || got != ocsv1.Mode_MODE_OCS {
		t.Fatalf("mode(ocs) = %v, %v", got, err)
	}
	if _, err := mode("typo"); err == nil {
		t.Fatal("mode accepted an invalid value")
	}
}
