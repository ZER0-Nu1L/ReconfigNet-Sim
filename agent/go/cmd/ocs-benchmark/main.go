package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"log"
	"math"
	"net/http"
	"os"
	"runtime"
	"slices"
	"strings"
	"sync"
	"time"

	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
)

type options struct {
	RuntimeLabel string
	GRPCTarget   string
	HTTPTarget   string
	LegacyHTTP   bool
	Protocols    []string
	Operations   []string
	Strategies   []string
	Transport    string
	Warmup       int
	Iterations   int
	Concurrency  int
	Timeout      time.Duration
}

type sample struct {
	LatencyUS              uint64
	ClientPrepareUS        uint64
	ClientRPCUS            uint64
	ServerUS               uint64
	QueueUS                uint64
	ValidationUS           uint64
	PlanningUS             uint64
	DeleteCommitUS         uint64
	ActualGapUS            uint64
	InstallCommitUS        uint64
	ReadbackUS             uint64
	ProgrammingUS          uint64
	WorkerRPCUS            uint64
	WorkerTotalUS          uint64
	PreconditionReadbackUS uint64
	CachePreconditionUS    uint64
	LeaseRevisionCheckUS   uint64
	DeviceWriteRequests    uint32
	Result                 string
	Permutation            []uint32
}

type summary struct {
	Min  uint64  `json:"min"`
	Mean float64 `json:"mean"`
	P50  uint64  `json:"p50"`
	P95  uint64  `json:"p95"`
	P99  uint64  `json:"p99"`
	Max  uint64  `json:"max"`
}

type runResult struct {
	Protocol                string             `json:"protocol"`
	Operation               string             `json:"operation"`
	Strategy                string             `json:"strategy,omitempty"`
	Transport               string             `json:"transport,omitempty"`
	Iterations              int                `json:"iterations"`
	Concurrency             int                `json:"concurrency"`
	ThroughputOpsS          float64            `json:"throughput_ops_s"`
	CommittedThroughputOpsS float64            `json:"committed_throughput_ops_s,omitempty"`
	SuccessRatePercent      float64            `json:"success_rate_percent,omitempty"`
	Results                 map[string]int     `json:"results,omitempty"`
	ClientLatencyUS         summary            `json:"client_latency_us"`
	ClientPrepareUS         *summary           `json:"client_prepare_us,omitempty"`
	ClientRPCUS             *summary           `json:"client_rpc_us,omitempty"`
	ServerTotalUS           *summary           `json:"server_total_us,omitempty"`
	ProtocolAndWireUS       *summary           `json:"protocol_and_wire_us,omitempty"`
	QueueWaitUS             *summary           `json:"queue_wait_us,omitempty"`
	ValidationUS            *summary           `json:"validation_us,omitempty"`
	PlanningUS              *summary           `json:"planning_us,omitempty"`
	DeleteCommitUS          *summary           `json:"delete_commit_us,omitempty"`
	ActualGapUS             *summary           `json:"actual_gap_us,omitempty"`
	InstallCommitUS         *summary           `json:"install_commit_us,omitempty"`
	ReadbackUS              *summary           `json:"readback_us,omitempty"`
	ProgrammingTotalUS      *summary           `json:"programming_total_us,omitempty"`
	DeviceWorkerRPCUS       *summary           `json:"device_worker_rpc_us,omitempty"`
	DeviceWorkerTotalUS     *summary           `json:"device_worker_total_us,omitempty"`
	PreconditionReadbackUS  *summary           `json:"precondition_readback_us,omitempty"`
	CachePreconditionUS     *summary           `json:"cache_precondition_us,omitempty"`
	LeaseRevisionCheckUS    *summary           `json:"lease_revision_check_us,omitempty"`
	ExclusiveBreakdownUS    map[string]summary `json:"exclusive_breakdown_us,omitempty"`
	MeanDeviceWriteRequests float64            `json:"mean_device_write_requests,omitempty"`
}

type output struct {
	RuntimeLabel string `json:"runtime_label"`
	Benchmark    struct {
		Protocols   []string `json:"protocol_order"`
		Operations  []string `json:"operations"`
		Strategies  []string `json:"strategies"`
		Transport   string   `json:"transport"`
		Warmup      int      `json:"warmup"`
		Iterations  int      `json:"iterations"`
		Concurrency int      `json:"concurrency"`
		Timeout     float64  `json:"timeout_seconds"`
	} `json:"benchmark"`
	ClientRuntime map[string]string `json:"client_runtime"`
	GRPCTarget    string            `json:"grpc_target"`
	HTTPTarget    string            `json:"http_target"`
	PortCount     int               `json:"port_count"`
	InitialPI     []uint32          `json:"initial_pi"`
	Backend       map[string]any    `json:"backend"`
	Runs          []runResult       `json:"runs"`
}

type benchmarkClient interface {
	Acquire(context.Context) error
	Permutation(context.Context) (sample, error)
	Runtime(context.Context) (map[string]any, error)
	Apply(context.Context, []uint32, string, string) (sample, error)
	Close() error
}

type grpcClient struct {
	connection *grpc.ClientConn
	client     ocsv1.OcsOperationsClient
	mu         sync.Mutex
	leaseToken string
	revision   uint64
}

func (c *grpcClient) Acquire(ctx context.Context) error {
	lease, err := c.client.AcquireControl(ctx, &ocsv1.AcquireControlRequest{
		ClientId: "go-benchmark",
	})
	if err != nil {
		return err
	}
	c.leaseToken = lease.GetLeaseToken()
	c.revision = lease.GetRevision()
	return nil
}

func newGRPCClient(target string) (*grpcClient, error) {
	connection, err := grpc.NewClient(
		target, grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, err
	}
	return &grpcClient{
		connection: connection,
		client:     ocsv1.NewOcsOperationsClient(connection),
	}, nil
}

func (c *grpcClient) Close() error {
	if c.leaseToken != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		_, _ = c.client.ReleaseControl(ctx, &ocsv1.ReleaseControlRequest{
			LeaseToken: c.leaseToken,
		})
		cancel()
	}
	return c.connection.Close()
}

func (c *grpcClient) Permutation(ctx context.Context) (sample, error) {
	started := time.Now()
	response, err := c.client.GetPermutation(ctx, &ocsv1.Empty{})
	if err != nil {
		return sample{}, err
	}
	return sample{
		LatencyUS:   uint64(time.Since(started).Microseconds()),
		Permutation: slices.Clone(response.GetPermutation().GetPi()),
	}, nil
}

func (c *grpcClient) Runtime(ctx context.Context) (map[string]any, error) {
	response, err := c.client.GetRuntime(ctx, &ocsv1.Empty{})
	if err != nil {
		return nil, err
	}
	capabilities := response.GetState().GetBackendCapabilities()
	return map[string]any{
		"name":             capabilities.GetBackend(),
		"readback":         capabilities.GetReadback(),
		"native_batch":     capabilities.GetNativeBatch(),
		"dataplane_atomic": capabilities.GetDataplaneAtomic(),
		"transports":       capabilities.GetTransports(),
	}, nil
}

func (c *grpcClient) Apply(
	ctx context.Context,
	permutation []uint32,
	strategy string,
	transport string,
) (sample, error) {
	totalStarted := time.Now()
	c.mu.Lock()
	defer c.mu.Unlock()
	prepareStarted := time.Now()
	request := &ocsv1.ApplyBatchRequest{
		Intent: &ocsv1.ApplyBatchRequest_Permutation{
			Permutation: &ocsv1.Permutation{Pi: permutation},
		},
		Strategy:            strategyProto(strategy),
		Transport:           transportProto(transport),
		HasExpectedRevision: true,
		ExpectedRevision:    c.revision,
	}
	prepareUS := uint64(time.Since(prepareStarted).Microseconds())
	rpcStarted := time.Now()
	response, err := c.client.ApplyBatch(
		metadata.AppendToOutgoingContext(ctx,
			"x-ocs-control-lease", c.leaseToken), request)
	if err != nil {
		return sample{}, err
	}
	rpcUS := uint64(time.Since(rpcStarted).Microseconds())
	c.revision = response.GetState().GetRevision()
	timing := response.GetTiming()
	return sample{
		LatencyUS:              uint64(time.Since(totalStarted).Microseconds()),
		ClientPrepareUS:        prepareUS,
		ClientRPCUS:            rpcUS,
		ServerUS:               timing.GetServerTotalUs(),
		QueueUS:                timing.GetQueueWaitUs(),
		ValidationUS:           timing.GetValidationUs(),
		PlanningUS:             timing.GetPlanningUs(),
		DeleteCommitUS:         timing.GetDeleteCommitUs(),
		ActualGapUS:            timing.GetActualGapUs(),
		InstallCommitUS:        timing.GetInstallCommitUs(),
		ReadbackUS:             timing.GetReadbackUs(),
		ProgrammingUS:          timing.GetProgrammingTotalUs(),
		WorkerRPCUS:            timing.GetDeviceWorkerRpcUs(),
		WorkerTotalUS:          timing.GetDeviceWorkerTotalUs(),
		PreconditionReadbackUS: timing.GetPreconditionReadbackUs(),
		CachePreconditionUS:    timing.GetCachePreconditionUs(),
		LeaseRevisionCheckUS:   timing.GetLeaseRevisionCheckUs(),
		DeviceWriteRequests:    timing.GetDeviceWriteRequests(),
		Result:                 response.GetResult(),
	}, nil
}

type httpClient struct {
	baseURL    string
	client     *http.Client
	close      func()
	mu         sync.Mutex
	leaseToken string
	revision   uint64
}

func (c *httpClient) Acquire(ctx context.Context) error {
	var response struct {
		LeaseToken string `json:"lease_token"`
		Revision   uint64 `json:"revision"`
	}
	if err := c.call(ctx, http.MethodPost, "/ocs_control/acquire",
		map[string]any{"client_id": "go-benchmark"}, nil, &response); err != nil {
		return err
	}
	c.leaseToken = response.LeaseToken
	c.revision = response.Revision
	return nil
}

func newHTTPClient(target string, concurrency int) *httpClient {
	transport := &http.Transport{
		MaxIdleConns:        concurrency,
		MaxIdleConnsPerHost: concurrency,
		MaxConnsPerHost:     concurrency,
		IdleConnTimeout:     30 * time.Second,
		DisableCompression:  true,
	}
	return &httpClient{
		baseURL: "http://" + target,
		client:  &http.Client{Transport: transport},
		close:   transport.CloseIdleConnections,
	}
}

func (c *httpClient) Close() error {
	if c.leaseToken != "" {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		_ = c.call(ctx, http.MethodPost, "/ocs_control/release",
			map[string]any{"lease_token": c.leaseToken}, nil, &struct{}{})
		cancel()
	}
	c.close()
	return nil
}

func (c *httpClient) Permutation(ctx context.Context) (sample, error) {
	started := time.Now()
	var response struct {
		PI []uint32 `json:"pi"`
	}
	if err := c.call(ctx, http.MethodGet, "/ocs_mapping", nil, nil, &response); err != nil {
		return sample{}, err
	}
	return sample{
		LatencyUS:   uint64(time.Since(started).Microseconds()),
		Permutation: response.PI,
	}, nil
}

func (c *httpClient) Runtime(ctx context.Context) (map[string]any, error) {
	var response struct {
		Backend map[string]any `json:"backend_capabilities"`
	}
	if err := c.call(
		ctx, http.MethodGet, "/ocs_mode", nil, nil, &response,
	); err != nil {
		return nil, err
	}
	if response.Backend == nil {
		return nil, fmt.Errorf("HTTP runtime omitted backend_capabilities")
	}
	if name, exists := response.Backend["backend"]; exists {
		response.Backend["name"] = name
		delete(response.Backend, "backend")
	}
	return response.Backend, nil
}

func (c *httpClient) Apply(
	ctx context.Context,
	permutation []uint32,
	strategy string,
	transport string,
) (sample, error) {
	totalStarted := time.Now()
	c.mu.Lock()
	defer c.mu.Unlock()
	prepareStarted := time.Now()
	payload := map[string]any{
		"new_pi":    permutation,
		"strategy":  strategy,
		"transport": transport,
	}
	encoded, err := json.Marshal(payload)
	if err != nil {
		return sample{}, err
	}
	headers := map[string]string{
		"Content-Type":            "application/json",
		"X-OCS-Control-Lease":     c.leaseToken,
		"X-OCS-Expected-Revision": fmt.Sprint(c.revision),
	}
	prepareUS := uint64(time.Since(prepareStarted).Microseconds())
	var response struct {
		Result   string `json:"result"`
		Revision uint64 `json:"revision"`
		Timing   struct {
			ServerUS               uint64 `json:"server_total_us"`
			QueueUS                uint64 `json:"queue_wait_us"`
			ValidationUS           uint64 `json:"validation_us"`
			PlanningUS             uint64 `json:"planning_us"`
			DeleteCommitUS         uint64 `json:"delete_commit_us"`
			ActualGapUS            uint64 `json:"actual_gap_us"`
			InstallCommitUS        uint64 `json:"install_commit_us"`
			ReadbackUS             uint64 `json:"readback_us"`
			ProgrammingUS          uint64 `json:"programming_total_us"`
			WorkerRPCUS            uint64 `json:"device_worker_rpc_us"`
			WorkerTotalUS          uint64 `json:"device_worker_total_us"`
			PreconditionReadbackUS uint64 `json:"precondition_readback_us"`
			CachePreconditionUS    uint64 `json:"cache_precondition_us"`
			LeaseRevisionCheckUS   uint64 `json:"lease_revision_check_us"`
			DeviceWriteRequests    uint32 `json:"device_write_requests"`
		} `json:"timing"`
	}
	rpcStarted := time.Now()
	if err := c.call(ctx, http.MethodPost, "/ocs_mapping", encoded, headers, &response); err != nil {
		return sample{}, err
	}
	rpcUS := uint64(time.Since(rpcStarted).Microseconds())
	c.revision = response.Revision
	return sample{
		LatencyUS:              uint64(time.Since(totalStarted).Microseconds()),
		ClientPrepareUS:        prepareUS,
		ClientRPCUS:            rpcUS,
		ServerUS:               response.Timing.ServerUS,
		QueueUS:                response.Timing.QueueUS,
		ValidationUS:           response.Timing.ValidationUS,
		PlanningUS:             response.Timing.PlanningUS,
		DeleteCommitUS:         response.Timing.DeleteCommitUS,
		ActualGapUS:            response.Timing.ActualGapUS,
		InstallCommitUS:        response.Timing.InstallCommitUS,
		ReadbackUS:             response.Timing.ReadbackUS,
		ProgrammingUS:          response.Timing.ProgrammingUS,
		WorkerRPCUS:            response.Timing.WorkerRPCUS,
		WorkerTotalUS:          response.Timing.WorkerTotalUS,
		PreconditionReadbackUS: response.Timing.PreconditionReadbackUS,
		CachePreconditionUS:    response.Timing.CachePreconditionUS,
		LeaseRevisionCheckUS:   response.Timing.LeaseRevisionCheckUS,
		DeviceWriteRequests:    response.Timing.DeviceWriteRequests,
		Result:                 response.Result,
	}, nil
}

func (c *httpClient) call(
	ctx context.Context,
	method string,
	path string,
	payload any,
	headers map[string]string,
	response any,
) error {
	var body io.Reader
	if encoded, ok := payload.([]byte); ok {
		body = bytes.NewReader(encoded)
	} else if payload != nil {
		encoded, err := json.Marshal(payload)
		if err != nil {
			return err
		}
		body = bytes.NewReader(encoded)
	}
	request, err := http.NewRequestWithContext(ctx, method, c.baseURL+path, body)
	if err != nil {
		return err
	}
	if payload != nil {
		request.Header.Set("Content-Type", "application/json")
	}
	for name, value := range headers {
		request.Header.Set(name, value)
	}
	httpResponse, err := c.client.Do(request)
	if err != nil {
		return err
	}
	defer httpResponse.Body.Close()
	if httpResponse.StatusCode >= 400 {
		raw, _ := io.ReadAll(httpResponse.Body)
		return fmt.Errorf("HTTP %s returned %d: %s", path, httpResponse.StatusCode, raw)
	}
	return json.NewDecoder(httpResponse.Body).Decode(response)
}

type legacyHTTPClient struct {
	baseURL string
	client  *http.Client
	close   func()
}

func newLegacyHTTPClient(target string, concurrency int) *legacyHTTPClient {
	transport := &http.Transport{
		MaxIdleConns:        concurrency,
		MaxIdleConnsPerHost: concurrency,
		MaxConnsPerHost:     concurrency,
		IdleConnTimeout:     30 * time.Second,
		DisableCompression:  true,
	}
	return &legacyHTTPClient{
		baseURL: "http://" + target,
		client:  &http.Client{Transport: transport},
		close:   transport.CloseIdleConnections,
	}
}

func (c *legacyHTTPClient) Close() error {
	c.close()
	return nil
}

func (c *legacyHTTPClient) Permutation(ctx context.Context) (sample, error) {
	started := time.Now()
	request, err := http.NewRequestWithContext(
		ctx, http.MethodGet, c.baseURL+"/ocs_mapping", nil)
	if err != nil {
		return sample{}, err
	}
	response, err := c.client.Do(request)
	if err != nil {
		return sample{}, err
	}
	defer response.Body.Close()
	if response.StatusCode >= 400 {
		return sample{}, fmt.Errorf("legacy HTTP GET returned %d", response.StatusCode)
	}
	var payload struct {
		PI []uint32 `json:"pi"`
	}
	if err := json.NewDecoder(response.Body).Decode(&payload); err != nil {
		return sample{}, err
	}
	return sample{
		LatencyUS:   uint64(time.Since(started).Microseconds()),
		Permutation: payload.PI,
	}, nil
}

func (c *legacyHTTPClient) Apply(
	ctx context.Context,
	permutation []uint32,
	_ string,
	_ string,
) (sample, error) {
	body, err := json.Marshal(map[string]any{"new_pi": permutation})
	if err != nil {
		return sample{}, err
	}
	request, err := http.NewRequestWithContext(
		ctx, http.MethodPost, c.baseURL+"/ocs_mapping", bytes.NewReader(body))
	if err != nil {
		return sample{}, err
	}
	request.Header.Set("Content-Type", "application/json")
	started := time.Now()
	response, err := c.client.Do(request)
	if err != nil {
		return sample{}, err
	}
	defer response.Body.Close()
	raw, err := io.ReadAll(response.Body)
	if err != nil {
		return sample{}, err
	}
	latency := uint64(time.Since(started).Microseconds())
	if response.StatusCode == http.StatusConflict {
		return sample{LatencyUS: latency, Result: "rejected"}, nil
	}
	if response.StatusCode >= 400 {
		return sample{}, fmt.Errorf(
			"legacy HTTP POST returned %d: %s", response.StatusCode, raw)
	}
	return sample{
		LatencyUS:           latency,
		DeviceWriteRequests: uint32(2 * len(permutation)),
		Result:              "updated",
	}, nil
}

func main() {
	parsed, err := parseOptions()
	if err != nil {
		log.Fatal(err)
	}
	var result output
	if parsed.LegacyHTTP {
		result, err = runLegacyHTTP(context.Background(), parsed)
	} else {
		result, err = run(context.Background(), parsed)
	}
	if err != nil {
		log.Fatal(err)
	}
	encoder := json.NewEncoder(os.Stdout)
	encoder.SetIndent("", "  ")
	if err := encoder.Encode(result); err != nil {
		log.Fatal(err)
	}
}

func runLegacyHTTP(ctx context.Context, options options) (output, error) {
	client := newLegacyHTTPClient(options.HTTPTarget, options.Concurrency)
	defer client.Close()
	requestContext, cancel := context.WithTimeout(ctx, options.Timeout)
	initialSample, err := client.Permutation(requestContext)
	cancel()
	if err != nil {
		return output{}, err
	}
	if err := validatePermutation(initialSample.Permutation); err != nil {
		return output{}, err
	}

	result := output{
		RuntimeLabel: options.RuntimeLabel,
		ClientRuntime: map[string]string{
			"language": "go",
			"go":       runtime.Version(),
			"http":     "net/http-server-forced-connection-close",
		},
		HTTPTarget: options.HTTPTarget,
		PortCount:  len(initialSample.Permutation),
		InitialPI:  initialSample.Permutation,
		Backend: map[string]any{
			"name":             "legacy-p4app-sequential",
			"readback":         false,
			"native_batch":     false,
			"dataplane_atomic": false,
			"transports":       []string{"SEQUENTIAL"},
		},
	}
	result.Benchmark.Protocols = []string{"http"}
	result.Benchmark.Operations = []string{"write"}
	result.Benchmark.Strategies = []string{"FULL"}
	result.Benchmark.Transport = "SEQUENTIAL"
	result.Benchmark.Warmup = options.Warmup
	result.Benchmark.Iterations = options.Iterations
	result.Benchmark.Concurrency = options.Concurrency
	result.Benchmark.Timeout = options.Timeout.Seconds()

	run, err := benchmarkLegacyHTTPWrite(
		ctx, client, initialSample.Permutation, options)
	if err != nil {
		return output{}, err
	}
	result.Runs = []runResult{run}
	return result, nil
}

func benchmarkLegacyHTTPWrite(
	ctx context.Context,
	client *legacyHTTPClient,
	initial []uint32,
	options options,
) (runResult, error) {
	allMappings := matchingPermutations(len(initial))
	candidates := make([][]uint32, 0, len(allMappings)-1)
	for _, candidate := range allMappings {
		if !slices.Equal(candidate, initial) {
			candidates = append(candidates, candidate)
		}
	}
	sequentialCandidates := [][]uint32{candidates[0], initial}
	current := slices.Clone(initial)
	for index := range options.Warmup {
		target := sequentialCandidates[index%len(sequentialCandidates)]
		requestContext, cancel := context.WithTimeout(ctx, options.Timeout)
		item, err := client.Apply(requestContext, target, "FULL", "SEQUENTIAL")
		cancel()
		if err != nil {
			return runResult{}, err
		}
		if item.Result != "updated" {
			return runResult{}, fmt.Errorf("legacy warmup was %s", item.Result)
		}
		current = slices.Clone(target)
	}
	if !slices.Equal(current, initial) {
		if err := legacyRestore(ctx, client, initial, options.Timeout); err != nil {
			return runResult{}, err
		}
	}

	if options.Concurrency == 1 {
		candidates = sequentialCandidates
	} else if options.Iterations > len(candidates) {
		return runResult{}, fmt.Errorf(
			"legacy write benchmark needs at most %d iterations at concurrency >1",
			len(candidates))
	}
	samples, elapsed, err := parallel(options, func(index int) (sample, error) {
		requestContext, cancel := context.WithTimeout(ctx, options.Timeout)
		defer cancel()
		return client.Apply(
			requestContext, candidates[index%len(candidates)], "FULL", "SEQUENTIAL")
	})
	if err != nil {
		return runResult{}, err
	}
	if err := legacyRestore(ctx, client, initial, options.Timeout); err != nil {
		return runResult{}, err
	}
	result := buildResult(
		"http", "write", "FULL", "SEQUENTIAL", samples, elapsed, options)
	updated := result.Results["updated"]
	result.CommittedThroughputOpsS = (result.ThroughputOpsS * float64(updated) / float64(options.Iterations))
	result.SuccessRatePercent = (float64(updated) * 100 / float64(options.Iterations))
	result.ServerTotalUS = nil
	result.ProtocolAndWireUS = nil
	result.QueueWaitUS = nil
	result.ValidationUS = nil
	result.PlanningUS = nil
	result.DeleteCommitUS = nil
	result.ActualGapUS = nil
	result.InstallCommitUS = nil
	result.ReadbackUS = nil
	result.ProgrammingTotalUS = nil
	result.DeviceWorkerRPCUS = nil
	result.DeviceWorkerTotalUS = nil
	result.PreconditionReadbackUS = nil
	result.ExclusiveBreakdownUS = nil
	return result, nil
}

func legacyRestore(
	ctx context.Context,
	client *legacyHTTPClient,
	initial []uint32,
	timeout time.Duration,
) error {
	requestContext, cancel := context.WithTimeout(ctx, timeout)
	state, err := client.Permutation(requestContext)
	cancel()
	if err != nil || slices.Equal(state.Permutation, initial) {
		return err
	}
	requestContext, cancel = context.WithTimeout(ctx, timeout)
	result, err := client.Apply(requestContext, initial, "FULL", "SEQUENTIAL")
	cancel()
	if err != nil {
		return err
	}
	if result.Result != "updated" {
		return fmt.Errorf("legacy restore was %s", result.Result)
	}
	return nil
}

func run(ctx context.Context, options options) (output, error) {
	discovery, err := makeClient(options.Protocols[0], options)
	if err != nil {
		return output{}, err
	}
	defer discovery.Close()
	requestContext, cancel := context.WithTimeout(ctx, options.Timeout)
	initialSample, err := discovery.Permutation(requestContext)
	cancel()
	if err != nil {
		return output{}, err
	}
	if err := validatePermutation(initialSample.Permutation); err != nil {
		return output{}, err
	}
	requestContext, cancel = context.WithTimeout(ctx, options.Timeout)
	backend, err := discovery.Runtime(requestContext)
	cancel()
	if err != nil {
		return output{}, err
	}

	result := output{
		RuntimeLabel: options.RuntimeLabel,
		ClientRuntime: map[string]string{
			"language": "go",
			"go":       runtime.Version(),
			"grpc":     grpc.Version,
			"http":     "net/http-persistent-http/1.1",
		},
		GRPCTarget: options.GRPCTarget,
		HTTPTarget: options.HTTPTarget,
		PortCount:  len(initialSample.Permutation),
		InitialPI:  initialSample.Permutation,
		Backend:    backend,
	}
	result.Benchmark.Protocols = options.Protocols
	result.Benchmark.Operations = options.Operations
	result.Benchmark.Strategies = options.Strategies
	result.Benchmark.Transport = options.Transport
	result.Benchmark.Warmup = options.Warmup
	result.Benchmark.Iterations = options.Iterations
	result.Benchmark.Concurrency = options.Concurrency
	result.Benchmark.Timeout = options.Timeout.Seconds()

	for _, protocol := range options.Protocols {
		client, err := makeClient(protocol, options)
		if err != nil {
			return output{}, err
		}
		requestContext, cancel := context.WithTimeout(ctx, options.Timeout)
		err = client.Acquire(requestContext)
		cancel()
		if err != nil {
			client.Close()
			return output{}, err
		}
		protocolRuns, runErr := runProtocol(
			ctx, client, protocol, initialSample.Permutation, options)
		closeErr := client.Close()
		if runErr != nil {
			return output{}, runErr
		}
		if closeErr != nil {
			return output{}, closeErr
		}
		result.Runs = append(result.Runs, protocolRuns...)
	}
	return result, nil
}

func runProtocol(
	ctx context.Context,
	client benchmarkClient,
	protocol string,
	initial []uint32,
	options options,
) ([]runResult, error) {
	runs := make([]runResult, 0)
	if slices.Contains(options.Operations, "read") {
		run, err := benchmarkRead(ctx, client, protocol, options)
		if err != nil {
			return nil, err
		}
		runs = append(runs, run)
	}
	for _, operation := range []string{"noop", "write"} {
		if !slices.Contains(options.Operations, operation) {
			continue
		}
		for _, strategy := range options.Strategies {
			run, err := benchmarkApply(
				ctx, client, protocol, operation, initial,
				strategy, options)
			if err != nil {
				return nil, err
			}
			runs = append(runs, run)
		}
	}
	return runs, nil
}

func benchmarkRead(
	ctx context.Context,
	client benchmarkClient,
	protocol string,
	options options,
) (runResult, error) {
	for range options.Warmup {
		requestContext, cancel := context.WithTimeout(ctx, options.Timeout)
		_, err := client.Permutation(requestContext)
		cancel()
		if err != nil {
			return runResult{}, err
		}
	}
	samples, elapsed, err := parallel(options, func(index int) (sample, error) {
		requestContext, cancel := context.WithTimeout(ctx, options.Timeout)
		defer cancel()
		return client.Permutation(requestContext)
	})
	if err != nil {
		return runResult{}, err
	}
	return buildResult(protocol, "read", "", "", samples, elapsed, options), nil
}

func benchmarkApply(
	ctx context.Context,
	client benchmarkClient,
	protocol string,
	operation string,
	initial []uint32,
	strategy string,
	options options,
) (runResult, error) {
	if err := applyOnce(ctx, client, initial, strategy, options); err != nil {
		return runResult{}, err
	}
	allMappings := matchingPermutations(len(initial))
	candidates := make([][]uint32, 0, len(allMappings)-1)
	for _, candidate := range allMappings {
		if !slices.Equal(candidate, initial) {
			candidates = append(candidates, candidate)
		}
	}
	if operation == "noop" {
		candidates = [][]uint32{initial}
	} else if options.Concurrency == 1 {
		candidates = [][]uint32{candidates[0], initial}
	} else if options.Iterations > len(candidates) {
		return runResult{}, fmt.Errorf(
			"write benchmark needs at most %d iterations at concurrency >1", len(candidates))
	}
	for index := range options.Warmup {
		if err := applyOnce(
			ctx, client, candidates[index%len(candidates)], strategy, options); err != nil {
			return runResult{}, err
		}
	}
	if err := applyOnce(ctx, client, initial, strategy, options); err != nil {
		return runResult{}, err
	}
	samples, elapsed, err := parallel(options, func(index int) (sample, error) {
		requestContext, cancel := context.WithTimeout(ctx, options.Timeout)
		defer cancel()
		return client.Apply(
			requestContext, candidates[index%len(candidates)],
			strategy, options.Transport)
	})
	if err != nil {
		return runResult{}, err
	}
	if operation == "write" {
		for _, item := range samples {
			if item.Result != "updated" {
				return runResult{}, fmt.Errorf("write benchmark contaminated by %q result", item.Result)
			}
		}
	}
	if err := applyOnce(ctx, client, initial, strategy, options); err != nil {
		return runResult{}, err
	}
	return buildResult(
		protocol, operation, strategy, options.Transport,
		samples, elapsed, options), nil
}

func applyOnce(
	ctx context.Context,
	client benchmarkClient,
	permutation []uint32,
	strategy string,
	options options,
) error {
	requestContext, cancel := context.WithTimeout(ctx, options.Timeout)
	defer cancel()
	_, err := client.Apply(requestContext, permutation, strategy, options.Transport)
	return err
}

func parallel(
	options options,
	call func(int) (sample, error),
) ([]sample, time.Duration, error) {
	started := time.Now()
	results := make([]sample, options.Iterations)
	errCh := make(chan error, options.Iterations)
	jobs := make(chan int)
	var wg sync.WaitGroup
	for range options.Concurrency {
		wg.Go(func() {
			for index := range jobs {
				value, err := call(index)
				if err != nil {
					errCh <- err
					continue
				}
				results[index] = value
			}
		})
	}
	for index := range options.Iterations {
		jobs <- index
	}
	close(jobs)
	wg.Wait()
	close(errCh)
	if err := errors.Join(slices.Collect(func(yield func(error) bool) {
		for err := range errCh {
			if !yield(err) {
				return
			}
		}
	})...); err != nil {
		return nil, 0, err
	}
	return results, time.Since(started), nil
}

func buildResult(
	protocol, operation, strategy, transport string,
	samples []sample,
	elapsed time.Duration,
	options options,
) runResult {
	latencies := collect(samples, func(value sample) uint64 { return value.LatencyUS })
	result := runResult{
		Protocol:        protocol,
		Operation:       operation,
		Strategy:        strategy,
		Transport:       transport,
		Iterations:      options.Iterations,
		Concurrency:     options.Concurrency,
		ThroughputOpsS:  float64(options.Iterations) / elapsed.Seconds(),
		ClientLatencyUS: summarize(latencies),
	}
	if operation == "read" {
		return result
	}
	result.Results = make(map[string]int)
	var totalWrites uint64
	for _, item := range samples {
		result.Results[item.Result]++
		totalWrites += uint64(item.DeviceWriteRequests)
	}
	server := summarize(collect(samples, func(value sample) uint64 { return value.ServerUS }))
	overhead := summarize(collect(samples, func(value sample) uint64 {
		return value.LatencyUS - min(value.LatencyUS, value.ServerUS)
	}))
	queue := summarize(collect(samples, func(value sample) uint64 { return value.QueueUS }))
	validation := summarize(collect(samples, func(value sample) uint64 { return value.ValidationUS }))
	planning := summarize(collect(samples, func(value sample) uint64 { return value.PlanningUS }))
	deleteCommit := summarize(collect(samples, func(value sample) uint64 { return value.DeleteCommitUS }))
	actualGap := summarize(collect(samples, func(value sample) uint64 { return value.ActualGapUS }))
	installCommit := summarize(collect(samples, func(value sample) uint64 { return value.InstallCommitUS }))
	readback := summarize(collect(samples, func(value sample) uint64 { return value.ReadbackUS }))
	programming := summarize(collect(samples, func(value sample) uint64 { return value.ProgrammingUS }))
	workerRPC := summarize(collect(samples, func(value sample) uint64 { return value.WorkerRPCUS }))
	workerTotal := summarize(collect(samples, func(value sample) uint64 { return value.WorkerTotalUS }))
	preconditionReadback := summarize(collect(samples, func(value sample) uint64 {
		return value.PreconditionReadbackUS
	}))
	result.ServerTotalUS = &server
	clientPrepare := summarize(collect(samples, func(value sample) uint64 {
		return value.ClientPrepareUS
	}))
	clientRPC := summarize(collect(samples, func(value sample) uint64 {
		return value.ClientRPCUS
	}))
	result.ClientPrepareUS = &clientPrepare
	result.ClientRPCUS = &clientRPC
	result.ProtocolAndWireUS = &overhead
	result.QueueWaitUS = &queue
	result.ValidationUS = &validation
	result.PlanningUS = &planning
	result.DeleteCommitUS = &deleteCommit
	result.ActualGapUS = &actualGap
	result.InstallCommitUS = &installCommit
	result.ReadbackUS = &readback
	result.ProgrammingTotalUS = &programming
	result.DeviceWorkerRPCUS = &workerRPC
	result.DeviceWorkerTotalUS = &workerTotal
	result.PreconditionReadbackUS = &preconditionReadback
	cachePrecondition := summarize(collect(samples, func(value sample) uint64 {
		return value.CachePreconditionUS
	}))
	leaseRevision := summarize(collect(samples, func(value sample) uint64 {
		return value.LeaseRevisionCheckUS
	}))
	result.CachePreconditionUS = &cachePrecondition
	result.LeaseRevisionCheckUS = &leaseRevision
	result.ExclusiveBreakdownUS = exclusiveBreakdown(samples)
	result.MeanDeviceWriteRequests = float64(totalWrites) / float64(len(samples))
	return result
}

func exclusiveBreakdown(samples []sample) map[string]summary {
	selectors := map[string]func(sample) uint64{
		"client_non_server": func(value sample) uint64 {
			return residual(value.LatencyUS, value.ServerUS)
		},
		"queue_wait": func(value sample) uint64 { return value.QueueUS },
		"lease_revision_check": func(value sample) uint64 {
			return value.LeaseRevisionCheckUS
		},
		"validation": func(value sample) uint64 { return value.ValidationUS },
		"core_residual": func(value sample) uint64 {
			if value.WorkerRPCUS > 0 {
				return residual(
					value.ServerUS, value.QueueUS,
					value.LeaseRevisionCheckUS, value.ValidationUS,
					value.WorkerRPCUS)
			}
			return residual(
				value.ServerUS, value.QueueUS,
				value.LeaseRevisionCheckUS, value.ValidationUS,
				value.PlanningUS, value.ProgrammingUS)
		},
		"device_worker_rpc_overhead": func(value sample) uint64 {
			return residual(value.WorkerRPCUS, value.WorkerTotalUS)
		},
		"device_worker_non_programming": func(value sample) uint64 {
			return residual(
				value.WorkerTotalUS, value.PreconditionReadbackUS,
				value.CachePreconditionUS, value.PlanningUS,
				value.ProgrammingUS)
		},
		"precondition_readback": func(value sample) uint64 {
			return value.PreconditionReadbackUS
		},
		"cache_precondition": func(value sample) uint64 {
			return value.CachePreconditionUS
		},
		"planning":       func(value sample) uint64 { return value.PlanningUS },
		"delete_commit":  func(value sample) uint64 { return value.DeleteCommitUS },
		"actual_gap":     func(value sample) uint64 { return value.ActualGapUS },
		"install_commit": func(value sample) uint64 { return value.InstallCommitUS },
		"readback":       func(value sample) uint64 { return value.ReadbackUS },
		"programming_residual": func(value sample) uint64 {
			return residual(
				value.ProgrammingUS, value.DeleteCommitUS, value.ActualGapUS,
				value.InstallCommitUS, value.ReadbackUS)
		},
	}
	result := make(map[string]summary, len(selectors))
	for name, selector := range selectors {
		result[name] = summarize(collect(samples, selector))
	}
	return result
}

func residual(total uint64, parts ...uint64) uint64 {
	var used uint64
	for _, part := range parts {
		used += part
	}
	if used >= total {
		return 0
	}
	return total - used
}

func collect(samples []sample, selectValue func(sample) uint64) []uint64 {
	values := make([]uint64, 0, len(samples))
	for _, value := range samples {
		values = append(values, selectValue(value))
	}
	return values
}

func summarize(values []uint64) summary {
	ordered := slices.Sorted(slices.Values(values))
	var total uint64
	for _, value := range ordered {
		total += value
	}
	return summary{
		Min:  ordered[0],
		Mean: float64(total) / float64(len(ordered)),
		P50:  percentile(ordered, 50),
		P95:  percentile(ordered, 95),
		P99:  percentile(ordered, 99),
		Max:  ordered[len(ordered)-1],
	}
}

func percentile(ordered []uint64, percent float64) uint64 {
	index := int(math.Ceil((percent/100)*float64(len(ordered)))) - 1
	return ordered[max(0, min(index, len(ordered)-1))]
}

func matchingPermutations(portCount int) [][]uint32 {
	ports := make([]uint32, portCount)
	for index := range portCount {
		ports[index] = uint32(index + 1)
	}
	var pair func([]uint32, [][2]uint32)
	result := make([][]uint32, 0)
	pair = func(remaining []uint32, pairs [][2]uint32) {
		if len(remaining) == 0 {
			permutation := make([]uint32, portCount)
			for _, connection := range pairs {
				permutation[connection[0]-1] = connection[1]
				permutation[connection[1]-1] = connection[0]
			}
			result = append(result, permutation)
			return
		}
		first := remaining[0]
		for index := 1; index < len(remaining); index++ {
			next := make([]uint32, 0, len(remaining)-2)
			next = append(next, remaining[1:index]...)
			next = append(next, remaining[index+1:]...)
			pair(next, append(pairs, [2]uint32{first, remaining[index]}))
		}
	}
	pair(ports, nil)
	return result
}

func validatePermutation(permutation []uint32) error {
	if len(permutation) < 2 || len(permutation)%2 != 0 {
		return fmt.Errorf("pi must have a positive even number of ports")
	}
	want := make([]uint32, len(permutation))
	for index := range len(want) {
		want[index] = uint32(index + 1)
	}
	if !slices.Equal(slices.Sorted(slices.Values(permutation)), want) {
		return fmt.Errorf("pi must be a permutation of 1..N")
	}
	for source, destination := range permutation {
		if destination == uint32(source+1) || permutation[destination-1] != uint32(source+1) {
			return fmt.Errorf("pi must contain symmetric non-self pairs")
		}
	}
	return nil
}

func makeClient(protocol string, options options) (benchmarkClient, error) {
	if protocol == "grpc" {
		return newGRPCClient(options.GRPCTarget)
	}
	if protocol == "http" {
		return newHTTPClient(options.HTTPTarget, options.Concurrency), nil
	}
	return nil, fmt.Errorf("unknown protocol %s", protocol)
}

func parseOptions() (options, error) {
	runtimeLabel := flag.String("runtime", "unspecified", "agent runtime label")
	grpcTarget := flag.String("grpc-target", "127.0.0.1:9339", "OcsOperations target")
	httpTarget := flag.String("http-target", "127.0.0.1:5000", "HTTP target")
	legacyHTTP := flag.Bool(
		"legacy-http", false,
		"benchmark the pre-model HTTP new_pi API without gRPC discovery")
	protocol := flag.String("protocol", "both", "http, grpc, or both")
	operation := flag.String("operation", "all", "read, noop, write, or all")
	strategy := flag.String("strategy", "both", "full, delta, or both")
	transport := flag.String("transport", "native-batch", "sequential or native-batch")
	warmup := flag.Int("warmup", 10, "warmup calls")
	iterations := flag.Int("iterations", 100, "measured calls")
	concurrency := flag.Int("concurrency", 1, "parallel calls")
	timeout := flag.Duration("timeout", 10*time.Second, "per-call timeout")
	flag.Parse()
	if *warmup < 0 || *iterations < 1 || *concurrency < 1 {
		return options{}, fmt.Errorf("warmup must be >=0; iterations and concurrency must be >=1")
	}
	protocols, err := expand(*protocol, "both", []string{"http", "grpc"})
	if err != nil {
		return options{}, err
	}
	operations, err := expand(*operation, "all", []string{"read", "noop", "write"})
	if err != nil {
		return options{}, err
	}
	strategies, err := expand(*strategy, "both", []string{"FULL", "DELTA"})
	if err != nil {
		return options{}, err
	}
	transportName := strings.ToUpper(strings.ReplaceAll(*transport, "-", "_"))
	if transportName != "SEQUENTIAL" && transportName != "NATIVE_BATCH" {
		return options{}, fmt.Errorf("transport must be sequential or native-batch")
	}
	return options{
		RuntimeLabel: *runtimeLabel,
		GRPCTarget:   *grpcTarget,
		HTTPTarget:   *httpTarget,
		LegacyHTTP:   *legacyHTTP,
		Protocols:    protocols,
		Operations:   operations,
		Strategies:   strategies,
		Transport:    transportName,
		Warmup:       *warmup,
		Iterations:   *iterations,
		Concurrency:  *concurrency,
		Timeout:      *timeout,
	}, nil
}

func expand(value, all string, choices []string) ([]string, error) {
	if strings.EqualFold(value, all) {
		return slices.Clone(choices), nil
	}
	for _, choice := range choices {
		if strings.EqualFold(value, choice) {
			return []string{choice}, nil
		}
	}
	return nil, fmt.Errorf("value %q must be %s", value, strings.Join(choices, ", "))
}

func strategyProto(strategy string) ocsv1.ExecutionStrategy {
	if strategy == "DELTA" {
		return ocsv1.ExecutionStrategy_EXECUTION_STRATEGY_DELTA
	}
	return ocsv1.ExecutionStrategy_EXECUTION_STRATEGY_FULL
}

func transportProto(transport string) ocsv1.Transport {
	if transport == "NATIVE_BATCH" {
		return ocsv1.Transport_TRANSPORT_NATIVE_BATCH
	}
	return ocsv1.Transport_TRANSPORT_SEQUENTIAL
}
