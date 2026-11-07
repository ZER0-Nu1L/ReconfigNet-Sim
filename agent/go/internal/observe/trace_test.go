package observe

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestTracePrivateExclusiveAndDrained(t *testing.T) {
	path := filepath.Join(t.TempDir(), "capture.trace")
	stop, err := Start(path, time.Second)
	if err != nil {
		t.Fatal(err)
	}
	stop()
	stop()
	info, err := os.Stat(path)
	if err != nil || info.Size() == 0 || info.Mode().Perm() != 0o600 {
		t.Fatalf("trace not drained/private: %v %v", info, err)
	}
	if _, err := Start(path, time.Second); err == nil {
		t.Fatal("overwrote evidence")
	}
}

func TestTraceRejectsUnboundedCapture(t *testing.T) {
	for _, duration := range []time.Duration{0, -1, 31 * time.Second} {
		if _, err := Start(filepath.Join(t.TempDir(), "bad.trace"), duration); err == nil {
			t.Fatal("accepted unbounded duration")
		}
	}
	close, err := OnSignal(t.Context(), filepath.Join(t.TempDir(), "server"), time.Second)
	if err != nil {
		t.Fatal(err)
	}
	close()
}
