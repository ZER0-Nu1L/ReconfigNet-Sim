package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestLegacyHTTPClientApplyTracksUpdatedAndRejected(t *testing.T) {
	responses := []int{http.StatusOK, http.StatusConflict}
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodPost || request.URL.Path != "/ocs_mapping" {
			t.Fatalf("unexpected request %s %s", request.Method, request.URL.Path)
		}
		var payload struct {
			PI []uint32 `json:"new_pi"`
		}
		if err := json.NewDecoder(request.Body).Decode(&payload); err != nil {
			t.Fatalf("decode request: %v", err)
		}
		if len(payload.PI) != 2 || payload.PI[0] != 2 || payload.PI[1] != 1 {
			t.Fatalf("unexpected permutation %v", payload.PI)
		}
		status := responses[0]
		responses = responses[1:]
		writer.WriteHeader(status)
		_, _ = writer.Write([]byte(`{"status":"success"}`))
	}))
	defer server.Close()

	target := strings.TrimPrefix(server.URL, "http://")
	client := newLegacyHTTPClient(target, 1)
	defer client.Close()

	updated, err := client.Apply(context.Background(), []uint32{2, 1}, "", "")
	if err != nil {
		t.Fatalf("updated call: %v", err)
	}
	if updated.Result != "updated" || updated.DeviceWriteRequests != 4 {
		t.Fatalf("unexpected updated sample %+v", updated)
	}

	rejected, err := client.Apply(context.Background(), []uint32{2, 1}, "", "")
	if err != nil {
		t.Fatalf("rejected call: %v", err)
	}
	if rejected.Result != "rejected" || rejected.DeviceWriteRequests != 0 {
		t.Fatalf("unexpected rejected sample %+v", rejected)
	}
}

func TestHTTPClientDiscoversRuntimeWithoutGRPC(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(writer http.ResponseWriter, request *http.Request) {
		if request.Method != http.MethodGet || request.URL.Path != "/ocs_mode" {
			t.Fatalf("unexpected request %s %s", request.Method, request.URL.Path)
		}
		writer.Header().Set("Content-Type", "application/json")
		_, _ = writer.Write([]byte(`{
			"backend_capabilities": {
				"backend": "p4app",
				"readback": true,
				"native_batch": true,
				"transports": ["SEQUENTIAL", "NATIVE_BATCH"]
			}
		}`))
	}))
	defer server.Close()

	client := newHTTPClient(strings.TrimPrefix(server.URL, "http://"), 1)
	defer client.Close()
	backend, err := client.Runtime(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	if backend["name"] != "p4app" || backend["native_batch"] != true {
		t.Fatalf("backend = %#v", backend)
	}
}

func TestExclusiveBreakdownUsesPerSampleResiduals(t *testing.T) {
	breakdown := exclusiveBreakdown([]sample{{
		LatencyUS:              100,
		ServerUS:               90,
		QueueUS:                5,
		ValidationUS:           3,
		PlanningUS:             2,
		DeleteCommitUS:         10,
		ActualGapUS:            1,
		InstallCommitUS:        15,
		ReadbackUS:             12,
		ProgrammingUS:          40,
		WorkerRPCUS:            70,
		WorkerTotalUS:          60,
		PreconditionReadbackUS: 15,
		CachePreconditionUS:    2,
		LeaseRevisionCheckUS:   4,
	}})
	want := map[string]uint64{
		"client_non_server":             10,
		"queue_wait":                    5,
		"lease_revision_check":          4,
		"validation":                    3,
		"core_residual":                 8,
		"device_worker_rpc_overhead":    10,
		"device_worker_non_programming": 1,
		"precondition_readback":         15,
		"cache_precondition":            2,
		"planning":                      2,
		"delete_commit":                 10,
		"actual_gap":                    1,
		"install_commit":                15,
		"readback":                      12,
		"programming_residual":          2,
	}
	for name, value := range want {
		if breakdown[name].P50 != value {
			t.Errorf("%s p50 = %d, want %d", name, breakdown[name].P50, value)
		}
	}
	var total uint64
	for _, item := range breakdown {
		total += item.P50
	}
	if total != 100 {
		t.Errorf("exclusive p50 components sum to %d, want client latency 100", total)
	}
}
