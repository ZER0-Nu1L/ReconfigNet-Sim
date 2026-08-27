package main

import (
	"testing"
	"time"
)

func TestSampleTrackerMeasuresBlackout(t *testing.T) {
	tracker := &sampleTracker{}
	base := time.Unix(0, 0)
	tracker.reset(7)
	tracker.observe(echoEvent{sampleID: 7, target: "old", received: base})
	tracker.startRequest(base.Add(time.Millisecond))
	tracker.observe(echoEvent{
		sampleID: 7, target: "old", received: base.Add(2 * time.Millisecond),
	})
	tracker.observe(echoEvent{
		sampleID: 7, target: "new", received: base.Add(3500 * time.Microsecond),
	})
	snapshot := tracker.snapshot()
	if snapshot.baselineOldReplies != 1 || snapshot.baselineNewReplies != 0 {
		t.Fatalf("baseline = old:%d new:%d",
			snapshot.baselineOldReplies, snapshot.baselineNewReplies)
	}
	if got := snapshot.firstNew.Sub(snapshot.lastOld); got != 1500*time.Microsecond {
		t.Fatalf("blackout = %s", got)
	}
}

func TestParsePI(t *testing.T) {
	pi, err := parsePI("6,3,2,5,4,1")
	if err != nil {
		t.Fatal(err)
	}
	if len(pi) != 6 || pi[0] != 6 || pi[5] != 1 {
		t.Fatalf("pi = %v", pi)
	}
	if _, err := parsePI("1,2,3"); err == nil {
		t.Fatal("odd pi accepted")
	}
}
