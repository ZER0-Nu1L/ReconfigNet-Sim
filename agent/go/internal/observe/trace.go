// Package observe provides bounded, opt-in runtime tracing without a network endpoint.
package observe

import (
	"context"
	"fmt"
	"log"
	"os"
	"os/signal"
	"path/filepath"
	"runtime/trace"
	"sync"
	"syscall"
	"time"
)

// Start creates a private, exclusive trace file and stops on timeout or Close.
// The caller must call the returned function before exiting the process.
func Start(path string, duration time.Duration) (func(), error) {
	if duration <= 0 || duration > 30*time.Second {
		return nil, fmt.Errorf("trace duration must be in (0, 30s]")
	}
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return nil, err
	}
	if err := trace.Start(f); err != nil {
		f.Close()
		return nil, err
	}
	var once sync.Once
	stop := func() { once.Do(func() { trace.Stop(); f.Close() }) }
	timer := time.AfterFunc(duration, stop)
	return func() { timer.Stop(); stop() }, nil
}

// OnSignal captures prefix.NNN.trace on SIGUSR1. Captures cannot overlap;
// termination drains the active trace before returning. No signal is installed
// unless explicitly enabled by the caller.
func OnSignal(ctx context.Context, prefix string, duration time.Duration) (func(), error) {
	if !filepath.IsAbs(prefix) || duration <= 0 || duration > 30*time.Second {
		return nil, fmt.Errorf("trace requires an absolute prefix and duration in (0, 30s]")
	}
	if info, err := os.Stat(filepath.Dir(prefix)); err != nil || !info.IsDir() {
		return nil, fmt.Errorf("trace parent directory must exist")
	}
	ctx, cancel := context.WithCancel(ctx)
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGUSR1)
	done := make(chan struct{})
	go func() {
		defer close(done)
		defer signal.Stop(signals)
		for sequence := 1; ; sequence++ {
			select {
			case <-ctx.Done():
				return
			case <-signals:
			}
			path := fmt.Sprintf("%s.%03d.trace", prefix, sequence)
			stop, err := Start(path, duration)
			if err != nil {
				log.Printf("runtime trace refused: %v", err)
				continue
			}
			log.Printf("runtime trace started: %s", path)
			timer := time.NewTimer(duration)
		active:
			for {
				select {
				case <-ctx.Done():
					break active
				case <-timer.C:
					break active
				case <-signals: // Ignore duplicate triggers during an active capture.
				}
			}
			timer.Stop()
			stop()
			log.Printf("runtime trace finished: %s", path)
		}
	}()
	return func() { cancel(); <-done }, nil
}
