package main

import (
	"context"
	"errors"
	"slices"
	"testing"
	"time"
)

type scopedClient struct {
	current []uint32
	writes  [][]uint32
	failAt  int
}

func (*scopedClient) Acquire(context.Context) error                   { return nil }
func (*scopedClient) Close() error                                    { return nil }
func (*scopedClient) Runtime(context.Context) (map[string]any, error) { return nil, nil }
func (c *scopedClient) Permutation(context.Context) (sample, error) {
	return sample{Permutation: slices.Clone(c.current)}, nil
}
func (c *scopedClient) Apply(_ context.Context, pi []uint32, _, _ string) (sample, error) {
	c.writes = append(c.writes, slices.Clone(pi))
	if c.failAt > 0 && len(c.writes) == c.failAt {
		return sample{}, errors.New("lost response")
	}
	result := "updated"
	if slices.Equal(c.current, pi) {
		result = "unchanged"
	}
	c.current = slices.Clone(pi)
	return sample{Result: result}, nil
}

func scopedOptions() options {
	return options{
		ExpectedInitialPI: []uint32{2, 1, 4, 3}, AlternatePI: []uint32{4, 3, 2, 1},
		Operations: []string{"write"}, Strategies: []string{"DELTA"},
		Transport: "NATIVE_BATCH", Concurrency: 1, Warmup: 3, Iterations: 5,
		Timeout: time.Second, RawSamples: true,
	}
}

func TestScopedBenchmarkUsesOnlyApprovedMappingsAndRestores(t *testing.T) {
	opts := scopedOptions()
	client := &scopedClient{current: slices.Clone(opts.ExpectedInitialPI)}
	runs, err := runProtocol(t.Context(), client, "grpc", opts.ExpectedInitialPI, opts)
	if err != nil {
		t.Fatal(err)
	}
	for _, pi := range client.writes {
		if !slices.Equal(pi, opts.ExpectedInitialPI) && !slices.Equal(pi, opts.AlternatePI) {
			t.Fatalf("unapproved mapping written: %v", pi)
		}
	}
	if !slices.Equal(client.current, opts.ExpectedInitialPI) || len(runs[0].Samples) != opts.Iterations {
		t.Fatalf("mapping not restored or missing samples: %+v", client)
	}
	for index, s := range runs[0].Samples {
		want := opts.AlternatePI
		if index%2 == 1 {
			want = opts.ExpectedInitialPI
		}
		if !slices.Equal(s.Permutation, want) || s.Result != "updated" {
			t.Fatalf("sample does not match alternating write: %+v", s)
		}
	}
}

func TestScopedBenchmarkDriftAfterLeasePreventsAllWrites(t *testing.T) {
	opts := scopedOptions()
	client := &scopedClient{current: opts.AlternatePI}
	if _, err := runProtocol(t.Context(), client, "grpc", opts.ExpectedInitialPI, opts); err == nil || len(client.writes) != 0 {
		t.Fatalf("drift must stop before setup write: %v, %v", client.writes, err)
	}
}

func TestScopedBenchmarkUnknownDoesNotRestoreOrContinue(t *testing.T) {
	opts := scopedOptions()
	opts.Warmup = 0
	client := &scopedClient{current: slices.Clone(opts.ExpectedInitialPI), failAt: 3}
	runs, err := runProtocol(t.Context(), client, "grpc", opts.ExpectedInitialPI, opts)
	if err == nil || len(client.writes) != 3 {
		t.Fatalf("unexpected write after unknown: %v, %v", client.writes, err)
	}
	if runs[0].Samples[0].Result != "error_outcome_unknown" || runs[0].Samples[1].Result != "not_attempted" {
		t.Fatalf("missing failure evidence: %+v", runs[0].Samples)
	}
}

func TestInvalidScopeNeverWrites(t *testing.T) {
	for _, name := range []string{"missing-initial", "missing-alternate", "same", "wrong-size", "invalid", "concurrent", "legacy", "all"} {
		t.Run(name, func(t *testing.T) {
			opts := scopedOptions()
			switch name {
			case "missing-initial":
				opts.ExpectedInitialPI = nil
			case "missing-alternate":
				opts.AlternatePI = nil
			case "same":
				opts.AlternatePI = slices.Clone(opts.ExpectedInitialPI)
			case "wrong-size":
				opts.AlternatePI = []uint32{2, 1}
			case "invalid":
				opts.AlternatePI = []uint32{2, 3, 4, 1}
			case "concurrent":
				opts.Concurrency = 2
			case "legacy":
				opts.LegacyHTTP = true
			case "all":
				opts.Operations = []string{"read", "noop", "write"}
			}
			client := &scopedClient{}
			if _, err := benchmarkApply(t.Context(), client, "grpc", "write", []uint32{2, 1, 4, 3}, "DELTA", opts); err == nil || len(client.writes) != 0 {
				t.Fatalf("invalid scope reached write: %v, %v", client.writes, err)
			}
		})
	}
}

func TestParseScopedMatching(t *testing.T) {
	for _, value := range []string{"0,1", "2,-1", "2,1,", "4294967296,1", "2,3,4,1"} {
		if _, err := parsePI(value); err == nil {
			t.Errorf("accepted %q", value)
		}
	}
	if pi, err := parsePI("2, 1,4,3"); err != nil || !slices.Equal(pi, []uint32{2, 1, 4, 3}) {
		t.Fatalf("valid scoped matching: %v, %v", pi, err)
	}
}
