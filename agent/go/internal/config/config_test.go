package config

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadP4appConfig(t *testing.T) {
	configPath := filepath.Join(
		"..", "..", "..", "configs", "p4app", "go-split-grpc.json")
	loaded, err := Load(configPath)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.Model.Profile != "p4app-v1" {
		t.Fatalf("profile = %q", loaded.Model.Profile)
	}
	if loaded.Model.Inventory.Len() != 8 {
		t.Fatalf("ports = %d", loaded.Model.Inventory.Len())
	}
	if loaded.DeploymentProfile != "go-split-grpc" {
		t.Fatalf("deployment profile = %q", loaded.DeploymentProfile)
	}
	if loaded.Device.ConsistencyMode != "CACHED_SYNC" {
		t.Fatalf("consistency mode = %q", loaded.Device.ConsistencyMode)
	}
	if loaded.Worker.Target != "unix:///tmp/ocs-device-worker.sock" {
		t.Fatalf("worker target = %q", loaded.Worker.Target)
	}
	permutation, err := loaded.Model.Connections.Permutation()
	if err != nil {
		t.Fatal(err)
	}
	if len(permutation) != 8 || permutation[0] != 2 {
		t.Fatalf("permutation = %v", permutation)
	}
}

func TestConfigRejectsUnknownFields(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, []byte(`{"unknown":true}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil {
		t.Fatal("Load accepted an unknown field")
	}
}

func TestConfigRejectsDeprecatedRuntimeFields(t *testing.T) {
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, []byte(`{"agent_runtime":"go-split"}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Load(path); err == nil ||
		!strings.Contains(err.Error(), "deprecated OCS configuration field") {
		t.Fatalf("Load error = %v", err)
	}
}
