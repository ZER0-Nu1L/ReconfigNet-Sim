// ocs-qualify runs an explicitly scoped, fail-stop paired-write exercise.
package main

import (
	"context"
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"os/signal"
	"strconv"
	"strings"
	"syscall"
	"time"

	"github.com/reconfig-net-sim/ocs-go-agent/client"
	ocsv1 "github.com/reconfig-net-sim/ocs-go-agent/gen/ocsv1"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/qualification"
)

func main() {
	target := flag.String("target", "", "gRPC endpoint or framed://host:port")
	initial := flag.String("initial-pi", "", "required initial and final permutation")
	alternate := flag.String("alternate-pi", "", "temporary permutation; scope must be reserved")
	output := flag.String("output", "", "new JSONL event file (never overwritten)")
	execute := flag.Bool("execute", false, "confirm exclusive writer window and real paired updates")
	duration := flag.Duration("duration", 24*time.Hour, "continuous exercise duration")
	interval := flag.Duration("interval", 5*time.Second, "sample and lease renewal interval (<=5s)")
	writeInterval := flag.Duration("write-interval", time.Minute, "paired update interval")
	timeout := flag.Duration("timeout", 3*time.Second, "per RPC timeout")
	compact := flag.Bool("compact", false, "request compact operation replies")
	flag.Parse()
	if err := run(*target, *initial, *alternate, *output, *execute, qualification.Options{Duration: *duration, Interval: *interval, WriteInterval: *writeInterval, Timeout: *timeout, Compact: *compact}); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(1)
	}
}

func parse(text string) ([]uint32, error) {
	var result []uint32
	for token := range strings.SplitSeq(text, ",") {
		v, err := strconv.ParseUint(strings.TrimSpace(token), 10, 32)
		if err != nil {
			return nil, err
		}
		result = append(result, uint32(v))
	}
	return result, nil
}

func run(target, initial, alternate, output string, execute bool, o qualification.Options) error {
	if !execute || target == "" || output == "" {
		return fmt.Errorf("--execute, --target and new --output are required")
	}
	var err error
	if o.Initial, err = parse(initial); err != nil {
		return err
	}
	if o.Alternate, err = parse(alternate); err != nil {
		return err
	}
	if err = qualification.Validate(o); err != nil {
		return err
	}
	file, err := os.OpenFile(output, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return err
	}
	defer file.Close()
	ctx, cancel := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer cancel()
	dialCtx, dialCancel := context.WithTimeout(ctx, o.Timeout)
	conn, err := client.Dial(dialCtx, target)
	dialCancel()
	if err != nil {
		return err
	}
	defer conn.Close()
	session, err := qualification.New(ocsv1.NewOcsOperationsClient(conn), o)
	if err != nil {
		return err
	}
	encoder := json.NewEncoder(file)
	return session.Run(ctx, func(e qualification.Event) error {
		if err := encoder.Encode(e); err != nil {
			return err
		}
		return file.Sync()
	})
}
