package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"net"
	"os"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
)

type options struct {
	Controller string
	SourceIP   string
	OldTarget  string
	NewTarget  string
	Port       int
	OldPI      []uint32
	NewPI      []uint32
	Samples    int
	Interval   time.Duration
	Settle     time.Duration
	Timeout    time.Duration
	DelayUS    uint64
}

type apiTiming struct {
	ServerTotalUS       uint64 `json:"server_total_us"`
	ProgrammingTotalUS  uint64 `json:"programming_total_us"`
	DeleteCommitUS      uint64 `json:"delete_commit_us"`
	InstallCommitUS     uint64 `json:"install_commit_us"`
	WorkerRPCUS         uint64 `json:"device_worker_rpc_us"`
	WorkerTotalUS       uint64 `json:"device_worker_total_us"`
	SouthboundQueueUS   uint64 `json:"southbound_queue_wait_us"`
	DeviceWriteRequests uint32 `json:"device_write_requests"`
}

type sampleResult struct {
	Sample                    int       `json:"sample"`
	Success                   bool      `json:"success"`
	BaselineOldReplies        uint64    `json:"baseline_old_replies"`
	BaselineNewReplies        uint64    `json:"baseline_new_replies"`
	OldRepliesAfterRequest    uint64    `json:"old_replies_after_request"`
	NewRepliesAfterRequest    uint64    `json:"new_replies_after_request"`
	RequestToAckUS            int64     `json:"request_to_ack_us"`
	RequestToFirstNewUS       *int64    `json:"request_to_first_new_us"`
	LastOldToFirstNewUS       *int64    `json:"last_old_to_first_new_blackout_us"`
	LastOldRelativeRequestUS  *int64    `json:"last_old_relative_request_us"`
	FirstNewRelativeRequestUS *int64    `json:"first_new_relative_request_us"`
	APIResult                 string    `json:"api_result"`
	Revision                  uint64    `json:"revision"`
	APITiming                 apiTiming `json:"api_timing"`
	Error                     string    `json:"error,omitempty"`
}

type output struct {
	Schema     string         `json:"schema"`
	Controller string         `json:"controller"`
	SourceIP   string         `json:"source_ip"`
	OldTarget  string         `json:"old_target_ip"`
	NewTarget  string         `json:"new_target_ip"`
	Port       int            `json:"port"`
	OldPI      []uint32       `json:"old_pi"`
	NewPI      []uint32       `json:"new_pi"`
	IntervalUS int64          `json:"probe_interval_us"`
	SettleMS   int64          `json:"settle_ms"`
	Samples    []sampleResult `json:"samples"`
}

type echoEvent struct {
	sampleID uint64
	target   string
	received time.Time
}

type sampleTracker struct {
	mu                     sync.Mutex
	id                     uint64
	requestStarted         time.Time
	baselineOldReplies     uint64
	baselineNewReplies     uint64
	oldRepliesAfterRequest uint64
	newRepliesAfterRequest uint64
	lastOld                time.Time
	firstNew               time.Time
}

func (s *sampleTracker) reset(id uint64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.id = id
	s.requestStarted = time.Time{}
	s.baselineOldReplies = 0
	s.baselineNewReplies = 0
	s.oldRepliesAfterRequest = 0
	s.newRepliesAfterRequest = 0
	s.lastOld = time.Time{}
	s.firstNew = time.Time{}
}

func (s *sampleTracker) startRequest(now time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.requestStarted = now
}

func (s *sampleTracker) observe(event echoEvent) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if event.sampleID != s.id {
		return
	}
	if s.requestStarted.IsZero() {
		if event.target == "old" {
			s.baselineOldReplies++
			s.lastOld = event.received
		} else {
			s.baselineNewReplies++
		}
		return
	}
	if event.target == "old" {
		s.oldRepliesAfterRequest++
		s.lastOld = event.received
	} else {
		s.newRepliesAfterRequest++
		if s.firstNew.IsZero() {
			s.firstNew = event.received
		}
	}
}

func (s *sampleTracker) snapshot() sampleTracker {
	s.mu.Lock()
	defer s.mu.Unlock()
	return sampleTracker{
		id:                     s.id,
		requestStarted:         s.requestStarted,
		baselineOldReplies:     s.baselineOldReplies,
		baselineNewReplies:     s.baselineNewReplies,
		oldRepliesAfterRequest: s.oldRepliesAfterRequest,
		newRepliesAfterRequest: s.newRepliesAfterRequest,
		lastOld:                s.lastOld,
		firstNew:               s.firstNew,
	}
}

type probeLoop struct {
	connection *net.UDPConn
	oldTarget  *net.UDPAddr
	newTarget  *net.UDPAddr
	interval   time.Duration
	currentID  atomic.Uint64
	sequence   atomic.Uint64
	tracker    *sampleTracker
	stop       chan struct{}
	done       chan struct{}
}

func newProbeLoop(sourceIP string, oldTarget string, newTarget string,
	port int, interval time.Duration, tracker *sampleTracker) (*probeLoop, error) {
	local := &net.UDPAddr{IP: net.ParseIP(sourceIP), Port: 0}
	if local.IP == nil {
		return nil, fmt.Errorf("invalid source IP %q", sourceIP)
	}
	connection, err := net.ListenUDP("udp", local)
	if err != nil {
		return nil, err
	}
	oldAddress := &net.UDPAddr{IP: net.ParseIP(oldTarget), Port: port}
	newAddress := &net.UDPAddr{IP: net.ParseIP(newTarget), Port: port}
	if oldAddress.IP == nil || newAddress.IP == nil {
		connection.Close()
		return nil, fmt.Errorf("old and new targets must be IP addresses")
	}
	return &probeLoop{
		connection: connection,
		oldTarget:  oldAddress,
		newTarget:  newAddress,
		interval:   interval,
		tracker:    tracker,
		stop:       make(chan struct{}),
		done:       make(chan struct{}),
	}, nil
}

func (p *probeLoop) start() {
	go p.receive()
	go p.send()
}

func (p *probeLoop) close() {
	close(p.stop)
	p.connection.Close()
	<-p.done
}

func (p *probeLoop) send() {
	ticker := time.NewTicker(p.interval)
	defer ticker.Stop()
	for {
		select {
		case <-p.stop:
			return
		case <-ticker.C:
			id := p.currentID.Load()
			if id == 0 {
				continue
			}
			sequence := p.sequence.Add(1)
			for target, address := range map[string]*net.UDPAddr{
				"old": p.oldTarget, "new": p.newTarget,
			} {
				payload := fmt.Sprintf("ocs-fast:%d:%s:%d", id, target, sequence)
				_, _ = p.connection.WriteToUDP([]byte(payload), address)
			}
		}
	}
}

func (p *probeLoop) receive() {
	defer close(p.done)
	buffer := make([]byte, 2048)
	for {
		_ = p.connection.SetReadDeadline(time.Now().Add(100 * time.Millisecond))
		count, _, err := p.connection.ReadFromUDP(buffer)
		if err != nil {
			if netError, ok := err.(net.Error); ok && netError.Timeout() {
				select {
				case <-p.stop:
					return
				default:
					continue
				}
			}
			return
		}
		parts := strings.Split(string(buffer[:count]), ":")
		if len(parts) != 4 || parts[0] != "ocs-fast" {
			continue
		}
		id, err := strconv.ParseUint(parts[1], 10, 64)
		if err != nil || (parts[2] != "old" && parts[2] != "new") {
			continue
		}
		p.tracker.observe(echoEvent{
			sampleID: id, target: parts[2], received: time.Now(),
		})
	}
}

func main() {
	parsed, err := parseOptions()
	if err != nil {
		fatal(err)
	}
	result, err := run(context.Background(), parsed)
	if err != nil {
		fatal(err)
	}
	encoded, err := json.MarshalIndent(result, "", "  ")
	if err != nil {
		fatal(err)
	}
	fmt.Println(string(encoded))
}

func run(ctx context.Context, options options) (output, error) {
	connection, err := grpc.NewClient(
		options.Controller,
		grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return output{}, err
	}
	defer connection.Close()
	client := ocsv1.NewOcsOperationsClient(connection)

	leaseCtx, cancel := context.WithTimeout(ctx, options.Timeout)
	lease, err := client.AcquireControl(
		leaseCtx, &ocsv1.AcquireControlRequest{ClientId: "ocs-reconfig-probe"})
	cancel()
	if err != nil {
		return output{}, err
	}
	defer func() {
		releaseCtx, releaseCancel := context.WithTimeout(
			context.Background(), options.Timeout)
		defer releaseCancel()
		_, _ = client.ReleaseControl(releaseCtx, &ocsv1.ReleaseControlRequest{
			LeaseToken: lease.GetLeaseToken(),
		})
	}()
	revision := lease.GetRevision()
	writeCtx := metadata.AppendToOutgoingContext(
		ctx, "x-ocs-control-lease", lease.GetLeaseToken())

	apply := func(mapping []uint32) (*ocsv1.OperationReply, error) {
		requestCtx, requestCancel := context.WithTimeout(writeCtx, options.Timeout)
		defer requestCancel()
		reply, callErr := client.ApplyBatch(requestCtx, &ocsv1.ApplyBatchRequest{
			Intent: &ocsv1.ApplyBatchRequest_Permutation{
				Permutation: &ocsv1.Permutation{Pi: mapping},
			},
			Strategy:            ocsv1.ExecutionStrategy_EXECUTION_STRATEGY_DELTA,
			Transport:           ocsv1.Transport_TRANSPORT_NATIVE_BATCH,
			DelayUs:             options.DelayUS,
			HasExpectedRevision: true,
			ExpectedRevision:    revision,
		})
		if callErr == nil {
			revision = reply.GetState().GetRevision()
		}
		return reply, callErr
	}

	if _, err := apply(options.OldPI); err != nil {
		return output{}, fmt.Errorf("establish old mapping: %w", err)
	}
	time.Sleep(options.Settle)

	tracker := &sampleTracker{}
	probes, err := newProbeLoop(
		options.SourceIP, options.OldTarget, options.NewTarget,
		options.Port, options.Interval, tracker)
	if err != nil {
		return output{}, err
	}
	probes.start()
	defer probes.close()

	result := output{
		Schema:     "reconfig-net-ocs-fast-switch/v1",
		Controller: options.Controller,
		SourceIP:   options.SourceIP,
		OldTarget:  options.OldTarget,
		NewTarget:  options.NewTarget,
		Port:       options.Port,
		OldPI:      options.OldPI,
		NewPI:      options.NewPI,
		IntervalUS: options.Interval.Microseconds(),
		SettleMS:   options.Settle.Milliseconds(),
	}

	for index := 1; index <= options.Samples; index++ {
		id := uint64(index)
		tracker.reset(id)
		probes.currentID.Store(id)
		baselineDeadline := time.Now().Add(options.Timeout)
		for {
			snapshot := tracker.snapshot()
			if snapshot.baselineOldReplies >= 3 {
				break
			}
			if time.Now().After(baselineDeadline) {
				return output{}, fmt.Errorf(
					"sample %d did not establish old-path baseline", index)
			}
			time.Sleep(options.Interval)
		}

		requestStarted := time.Now()
		tracker.startRequest(requestStarted)
		reply, callErr := apply(options.NewPI)
		ack := time.Now()
		record := sampleResult{
			Sample:         index,
			RequestToAckUS: ack.Sub(requestStarted).Microseconds(),
		}
		if callErr != nil {
			record.Error = callErr.Error()
			result.Samples = append(result.Samples, record)
			break
		}
		record.APIResult = reply.GetResult()
		record.Revision = reply.GetState().GetRevision()
		timing := reply.GetTiming()
		record.APITiming = apiTiming{
			ServerTotalUS:       timing.GetServerTotalUs(),
			ProgrammingTotalUS:  timing.GetProgrammingTotalUs(),
			DeleteCommitUS:      timing.GetDeleteCommitUs(),
			InstallCommitUS:     timing.GetInstallCommitUs(),
			WorkerRPCUS:         timing.GetDeviceWorkerRpcUs(),
			WorkerTotalUS:       timing.GetDeviceWorkerTotalUs(),
			SouthboundQueueUS:   timing.GetSouthboundQueueWaitUs(),
			DeviceWriteRequests: timing.GetDeviceWriteRequests(),
		}

		deadline := time.Now().Add(options.Timeout)
		for tracker.snapshot().firstNew.IsZero() && time.Now().Before(deadline) {
			time.Sleep(options.Interval)
		}
		snapshot := tracker.snapshot()
		record.BaselineOldReplies = snapshot.baselineOldReplies
		record.BaselineNewReplies = snapshot.baselineNewReplies
		record.OldRepliesAfterRequest = snapshot.oldRepliesAfterRequest
		record.NewRepliesAfterRequest = snapshot.newRepliesAfterRequest
		if !snapshot.firstNew.IsZero() {
			requestToFirst := snapshot.firstNew.Sub(requestStarted).Microseconds()
			firstRelative := requestToFirst
			record.RequestToFirstNewUS = &requestToFirst
			record.FirstNewRelativeRequestUS = &firstRelative
			if !snapshot.lastOld.IsZero() {
				blackout := snapshot.firstNew.Sub(snapshot.lastOld).Microseconds()
				lastRelative := snapshot.lastOld.Sub(requestStarted).Microseconds()
				record.LastOldToFirstNewUS = &blackout
				record.LastOldRelativeRequestUS = &lastRelative
			}
		}
		record.Success = (callErr == nil && record.APIResult == "updated" &&
			record.RequestToFirstNewUS != nil &&
			record.BaselineOldReplies >= 3 &&
			record.BaselineNewReplies == 0)
		result.Samples = append(result.Samples, record)

		probes.currentID.Store(0)
		if _, err := apply(options.OldPI); err != nil {
			return result, fmt.Errorf("restore old mapping: %w", err)
		}
		time.Sleep(options.Settle)
	}
	probes.currentID.Store(0)
	return result, nil
}

func parseOptions() (options, error) {
	controller := flag.String("controller", "127.0.0.1:9339", "gRPC target")
	sourceIP := flag.String("source-ip", "", "local data-plane source IP")
	oldTarget := flag.String("old-target-ip", "", "old connected target IP")
	newTarget := flag.String("new-target-ip", "", "new connected target IP")
	port := flag.Int("port", 47910, "UDP echo port")
	oldPIText := flag.String("old-pi", "", "comma-separated old permutation")
	newPIText := flag.String("new-pi", "", "comma-separated new permutation")
	samples := flag.Int("samples", 50, "measured old-to-new transitions")
	interval := flag.Duration("interval", 200*time.Microsecond, "probe interval")
	settle := flag.Duration("settle", 50*time.Millisecond, "mapping settle time")
	timeout := flag.Duration("timeout", 3*time.Second, "per-stage timeout")
	delayUS := flag.Uint64("delay-us", 0, "requested delete-to-install gap")
	flag.Parse()
	oldPI, err := parsePI(*oldPIText)
	if err != nil {
		return options{}, fmt.Errorf("old pi: %w", err)
	}
	newPI, err := parsePI(*newPIText)
	if err != nil {
		return options{}, fmt.Errorf("new pi: %w", err)
	}
	if len(oldPI) != len(newPI) {
		return options{}, fmt.Errorf("old and new pi must have the same length")
	}
	if *sourceIP == "" || *oldTarget == "" || *newTarget == "" {
		return options{}, fmt.Errorf(
			"source-ip, old-target-ip, and new-target-ip are required")
	}
	if *samples < 1 || *interval <= 0 || *settle < 0 || *timeout <= 0 {
		return options{}, fmt.Errorf(
			"samples/interval/timeout must be positive and settle non-negative")
	}
	return options{
		Controller: *controller,
		SourceIP:   *sourceIP,
		OldTarget:  *oldTarget,
		NewTarget:  *newTarget,
		Port:       *port,
		OldPI:      oldPI,
		NewPI:      newPI,
		Samples:    *samples,
		Interval:   *interval,
		Settle:     *settle,
		Timeout:    *timeout,
		DelayUS:    *delayUS,
	}, nil
}

func parsePI(value string) ([]uint32, error) {
	parts := strings.Split(value, ",")
	if value == "" || len(parts)%2 != 0 {
		return nil, fmt.Errorf("pi must contain an even number of ports")
	}
	pi := make([]uint32, len(parts))
	for index, part := range parts {
		parsed, err := strconv.ParseUint(strings.TrimSpace(part), 10, 32)
		if err != nil {
			return nil, fmt.Errorf("invalid pi entry %q", part)
		}
		pi[index] = uint32(parsed)
	}
	return pi, nil
}

func fatal(err error) {
	fmt.Fprintln(os.Stderr, err)
	os.Exit(1)
}
