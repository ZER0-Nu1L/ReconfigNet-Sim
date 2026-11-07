package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoadDirectConfigurationAndRejectIncompleteMapping(t *testing.T) {
	path := filepath.Join("..", "..", "..", "configs", "tofino", "go-direct-bfrt.example.json")
	loaded, err := Load(path)
	if err != nil {
		t.Fatal(err)
	}
	if loaded.DirectBFRT == nil || loaded.Worker.Target != "" || loaded.FramedAPI == nil {
		t.Fatal("not a standalone direct profile")
	}
	bytes, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	var raw map[string]any
	if err := json.Unmarshal(bytes, &raw); err != nil {
		t.Fatal(err)
	}
	configDir, _ := filepath.Abs(filepath.Dir(path))
	raw["model_file"] = filepath.Join(configDir, "model-6port.yaml")
	raw["capability_profile_file"] = filepath.Join(configDir, "capabilities.yaml")
	cases := []struct {
		name   string
		change func(map[string]any)
	}{
		{"missing_port", func(b map[string]any) { delete(b["logical_to_device"].(map[string]any), "1") }},
		{"unknown_field", func(b map[string]any) { b["unknown_setting"] = true }},
		{"duplicate_device_port", func(b map[string]any) { b["logical_to_device"].(map[string]any)["1"] = float64(2) }},
		{"relative_lock", func(b map[string]any) { b["ownership_lock_file"] = "relative.lock" }},
	}
	for _, item := range cases {
		t.Run(item.name, func(t *testing.T) {
			data, _ := json.Marshal(raw)
			var copy map[string]any
			if err := json.Unmarshal(data, &copy); err != nil {
				t.Fatal(err)
			}
			item.change(copy["backend"].(map[string]any))
			data, _ = json.Marshal(copy)
			bad := filepath.Join(t.TempDir(), "bad.json")
			if err := os.WriteFile(bad, data, 0600); err != nil {
				t.Fatal(err)
			}
			if _, err := Load(bad); err == nil {
				t.Fatal("accepted invalid direct configuration")
			}
		})
	}

}

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
