package main

import (
	"testing"
	"time"
)

func TestPacingDoesNotCatchUpAfterSlowCompletion(t *testing.T) {
	var starts []time.Time
	_, _, err := parallel(options{Iterations: 4, Concurrency: 1, RequestInterval: 10 * time.Millisecond}, func(i int) (sample, error) {
		starts = append(starts, time.Now())
		if i == 0 {
			time.Sleep(30 * time.Millisecond)
		}
		return sample{}, nil
	})
	if err != nil {
		t.Fatal(err)
	}
	for i := 1; i < len(starts); i++ {
		if starts[i].Sub(starts[i-1]) < 9*time.Millisecond {
			t.Fatal("catch-up burst")
		}
	}
}
