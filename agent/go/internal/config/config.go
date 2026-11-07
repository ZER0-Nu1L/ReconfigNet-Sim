package config

import (
	"bytes"
	"cmp"
	"encoding/json"
	"fmt"
	"io"
	"os"
	"path/filepath"

	"github.com/reconfig-net-sim/ocs-go-agent/internal/model"
	"gopkg.in/yaml.v3"
)

type Listener struct {
	Host string `json:"host"`
	Port int    `json:"port"`
}

type Device struct {
	ConsistencyMode string `json:"consistency_mode"`
}

type Worker struct {
	Target         string  `json:"target"`
	TimeoutSeconds float64 `json:"timeout_seconds"`
}

type Control struct {
	LeaseSeconds             float64 `json:"lease_seconds"`
	ReconcileIntervalSeconds float64 `json:"reconcile_interval_seconds"`
}

type GoAgent struct {
	Binary string `json:"binary"`
}

// DirectBFRT configures the reusable Tofino pair-table backend.
type DirectBFRT struct {
	Type              string      `json:"type"`
	Target            string      `json:"target"`
	ClientID          uint32      `json:"client_id"`
	DeviceID          uint32      `json:"device_id"`
	PipeID            uint32      `json:"pipe_id"`
	P4Name            string      `json:"p4_name"`
	TableName         string      `json:"table_name"`
	ActionName        string      `json:"action_name"`
	IngressField      string      `json:"ingress_field"`
	EgressField       string      `json:"egress_field"`
	PortMap           map[int]int `json:"logical_to_device"`
	TimeoutSeconds    float64     `json:"timeout_seconds"`
	OwnershipLockFile string      `json:"ownership_lock_file"`
	TwoPhase          bool        `json:"two_phase"`
}

type fileConfig struct {
	FramedAPI             *Listener       `json:"framed_api,omitempty"`
	Mode                  string          `json:"mode"`
	DeploymentProfile     string          `json:"deployment_profile"`
	ModelFile             string          `json:"model_file"`
	CapabilityProfileFile string          `json:"capability_profile_file"`
	EnableDebugger        bool            `json:"enable_debugger"`
	GRPCAPI               Listener        `json:"grpc_api"`
	Device                Device          `json:"device"`
	Worker                Worker          `json:"worker"`
	GoAgent               GoAgent         `json:"go_agent"`
	Control               Control         `json:"control"`
	StartupPolicy         string          `json:"startup_policy"`
	Backend               json.RawMessage `json:"backend"`
}

type ModelData struct {
	Profile           string
	CapabilityProfile string
	Inventory         model.Inventory
	Connections       model.ConnectionSet
}

type CapabilityModel struct {
	Name         string `yaml:"name"`
	Organization string `yaml:"organization"`
	Version      string `yaml:"version"`
}

type CapabilityProfile struct {
	Profile      string
	ModelVersion string
	GNMIVersion  string
	Encodings    []string
	Models       []CapabilityModel
}

type Config struct {
	FramedAPI         *Listener
	DirectBFRT        *DirectBFRT
	Mode              string
	DeploymentProfile string
	GRPCAPI           Listener
	Device            Device
	Worker            Worker
	Model             ModelData
	CapabilityProfile CapabilityProfile
	Control           Control
	StartupPolicy     string
}

type modelFile struct {
	Profile           string `yaml:"profile"`
	CapabilityProfile string `yaml:"capability-profile"`
	Components        struct {
		Components []model.Port `yaml:"component"`
	} `yaml:"openconfig-platform:components"`
	Connections struct {
		Connections []connectionFile `yaml:"port-connection"`
	} `yaml:"oc-optical-switch-connections:optical-switch-connections"`
}

type connectionFile struct {
	Name          string `yaml:"connection-name"`
	Bidirectional *bool  `yaml:"bidirectional,omitempty"`
	NearPortName  string `yaml:"near-port-name,omitempty"`
	FarPortName   string `yaml:"far-port-name,omitempty"`
	Config        *struct {
		Name          string `yaml:"connection-name"`
		Bidirectional *bool  `yaml:"bidirectional"`
		NearPortName  string `yaml:"near-port-name"`
		FarPortName   string `yaml:"far-port-name"`
	} `yaml:"config,omitempty"`
}

type capabilityFile struct {
	Profile      string `yaml:"profile"`
	ModelVersion string `yaml:"model-version"`
	GNMI         struct {
		Version   string            `yaml:"version"`
		Encodings []string          `yaml:"encodings"`
		Models    []CapabilityModel `yaml:"models"`
	} `yaml:"gnmi"`
	Capabilities []struct {
		ID        string `yaml:"id"`
		DraftArea string `yaml:"draft-area"`
		Status    string `yaml:"status"`
		Source    string `yaml:"source,omitempty"`
		Behavior  string `yaml:"behavior,omitempty"`
	} `yaml:"capabilities"`
}

func Load(path string) (Config, error) {
	contents, err := os.ReadFile(path)
	if err != nil {
		return Config{}, fmt.Errorf("open config: %w", err)
	}
	var fields map[string]json.RawMessage
	if err := json.Unmarshal(contents, &fields); err == nil {
		deprecated := []string{
			"agent_runtime", "enable_rest_api", "enable_grpc_api",
			"rest_api", "device_worker",
		}
		for _, name := range deprecated {
			if _, exists := fields[name]; exists {
				return Config{}, fmt.Errorf(
					"deprecated OCS configuration field %q is not supported; use deployment_profile go-split-grpc",
					name)
			}
		}
	}

	var raw fileConfig
	decoder := json.NewDecoder(bytes.NewReader(contents))
	decoder.DisallowUnknownFields()
	if err := decoder.Decode(&raw); err != nil {
		return Config{}, fmt.Errorf("decode config: %w", err)
	}
	if err := requireEOF(decoder); err != nil {
		return Config{}, err
	}
	if raw.StartupPolicy == "" {
		raw.StartupPolicy = "REQUIRE_MATCH"
	}
	if err := validateConfig(&raw); err != nil {
		return Config{}, err
	}
	if override := os.Getenv("OCS_CONSISTENCY_MODE"); override != "" {
		raw.Device.ConsistencyMode = override
		if override != "STRICT_DEVICE" && override != "CACHED_SYNC" &&
			override != "CACHED_ACK" {
			return Config{}, fmt.Errorf("OCS_CONSISTENCY_MODE must be STRICT_DEVICE, CACHED_SYNC, or CACHED_ACK")
		}
	}

	base := filepath.Dir(path)
	modelPath := resolve(base, raw.ModelFile)
	modelData, err := loadModel(modelPath)
	if err != nil {
		return Config{}, err
	}
	capabilityPath := resolve(base, raw.CapabilityProfileFile)
	capability, err := loadCapabilityProfile(capabilityPath)
	if err != nil {
		return Config{}, err
	}
	if modelData.Profile != capability.Profile {
		return Config{}, fmt.Errorf("model profile and capability profile must match")
	}
	if modelData.CapabilityProfile != "" &&
		filepath.Clean(modelData.CapabilityProfile) != filepath.Base(capabilityPath) &&
		filepath.Clean(resolve(filepath.Dir(modelPath), modelData.CapabilityProfile)) != filepath.Clean(capabilityPath) {
		return Config{}, fmt.Errorf("model capability-profile must match capability_profile_file")
	}

	var direct *DirectBFRT
	if raw.DeploymentProfile == "go-direct-bfrt" {
		if raw.Worker.Target != "" || raw.Worker.TimeoutSeconds != 0 {
			return Config{}, fmt.Errorf("go-direct-bfrt must not configure a Python worker")
		}
		direct = &DirectBFRT{}
		decoder := json.NewDecoder(bytes.NewReader(raw.Backend))
		decoder.DisallowUnknownFields()
		if err := decoder.Decode(direct); err != nil {
			return Config{}, fmt.Errorf("decode direct backend: %w", err)
		}
		if direct.Type != "bfrt" || direct.Target == "" || direct.P4Name == "" || direct.TableName == "" || direct.ActionName == "" || direct.IngressField == "" || direct.EgressField == "" || direct.TimeoutSeconds <= 0 || direct.OwnershipLockFile == "" {
			return Config{}, fmt.Errorf("direct BFRT backend requires explicit endpoint, program, table, fields, timeout and ownership lock")
		}
		if direct.PipeID > 65535 {
			return Config{}, fmt.Errorf("pipe_id exceeds 16 bits")
		}
		if !filepath.IsAbs(direct.OwnershipLockFile) {
			return Config{}, fmt.Errorf("ownership_lock_file must be an absolute path")
		}
		if len(direct.PortMap) != modelData.Inventory.Len() {
			return Config{}, fmt.Errorf("logical_to_device must map every inventory port")
		}
		seen := map[int]bool{}
		for logical, device := range direct.PortMap {
			if logical < 1 || logical > modelData.Inventory.Len() || device < 0 || device > 65535 || seen[device] {
				return Config{}, fmt.Errorf("invalid or duplicate port mapping")
			}
			seen[device] = true
		}
	}
	return Config{
		FramedAPI: raw.FramedAPI, DirectBFRT: direct,
		Mode:              raw.Mode,
		DeploymentProfile: raw.DeploymentProfile,
		GRPCAPI:           raw.GRPCAPI,
		Device:            raw.Device,
		Worker:            raw.Worker,
		Model:             modelData,
		CapabilityProfile: capability,
		Control:           raw.Control,
		StartupPolicy:     raw.StartupPolicy,
	}, nil
}

func requireEOF(decoder *json.Decoder) error {
	var extra any
	if err := decoder.Decode(&extra); err == io.EOF {
		return nil
	} else if err != nil {
		return fmt.Errorf("decode trailing config data: %w", err)
	}
	return fmt.Errorf("configuration must contain exactly one JSON object")
}

func validateConfig(config *fileConfig) error {
	if config.Mode != "l2" && config.Mode != "l3" {
		return fmt.Errorf("mode must be either l2 or l3")
	}
	if config.DeploymentProfile != "go-split-grpc" && config.DeploymentProfile != "go-direct-bfrt" {
		return fmt.Errorf("deployment_profile must be go-split-grpc or go-direct-bfrt for the Go Agent")
	}
	if config.ModelFile == "" {
		return fmt.Errorf("model_file must be a non-empty string")
	}
	if config.CapabilityProfileFile == "" {
		return fmt.Errorf("capability_profile_file must be a non-empty string")
	}
	if config.FramedAPI != nil {
		if err := validateListener("framed_api", *config.FramedAPI); err != nil {
			return err
		}
	}
	if err := validateListener("grpc_api", config.GRPCAPI); err != nil {
		return err
	}
	if config.DeploymentProfile == "go-split-grpc" && config.Worker.Target == "" {
		return fmt.Errorf("worker.target must be a non-empty string")
	}
	if config.DeploymentProfile == "go-split-grpc" && config.Worker.TimeoutSeconds <= 0 {
		return fmt.Errorf("worker.timeout_seconds must be greater than zero")
	}
	if config.Device.ConsistencyMode != "STRICT_DEVICE" &&
		config.Device.ConsistencyMode != "CACHED_SYNC" &&
		config.Device.ConsistencyMode != "CACHED_ACK" {
		return fmt.Errorf("device.consistency_mode must be STRICT_DEVICE, CACHED_SYNC, or CACHED_ACK")
	}
	if config.Control.LeaseSeconds <= 0 {
		return fmt.Errorf("control.lease_seconds must be greater than zero")
	}
	if config.Control.ReconcileIntervalSeconds <= 0 {
		return fmt.Errorf("control.reconcile_interval_seconds must be greater than zero")
	}
	if config.StartupPolicy != "REQUIRE_MATCH" &&
		config.StartupPolicy != "REAPPLY_DESIRED" {
		return fmt.Errorf("startup_policy must be REQUIRE_MATCH or REAPPLY_DESIRED")
	}
	return nil
}

func validateListener(name string, listener Listener) error {
	if listener.Host == "" {
		return fmt.Errorf("%s.host must be a non-empty string", name)
	}
	if listener.Port < 1 || listener.Port > 65535 {
		return fmt.Errorf("%s.port must be between 1 and 65535", name)
	}
	return nil
}

func loadModel(path string) (ModelData, error) {
	var raw modelFile
	if err := decodeYAML(path, &raw); err != nil {
		return ModelData{}, fmt.Errorf("decode model: %w", err)
	}
	if raw.Profile == "" {
		return ModelData{}, fmt.Errorf("profile must be a non-empty string")
	}
	inventory, err := model.NewInventory(raw.Components.Components)
	if err != nil {
		return ModelData{}, err
	}
	connections := make([]model.Connection, 0, len(raw.Connections.Connections))
	for _, item := range raw.Connections.Connections {
		connection := model.Connection{
			Name:          item.Name,
			Bidirectional: true,
			NearPortName:  item.NearPortName,
			FarPortName:   item.FarPortName,
		}
		if item.Bidirectional != nil {
			connection.Bidirectional = *item.Bidirectional
		}
		if item.Config != nil {
			connection.Name = cmp.Or(item.Name, item.Config.Name)
			connection.NearPortName = item.Config.NearPortName
			connection.FarPortName = item.Config.FarPortName
			if item.Config.Bidirectional != nil {
				connection.Bidirectional = *item.Config.Bidirectional
			}
		}
		connections = append(connections, connection)
	}
	connectionSet, err := model.NewConnectionSet(inventory, connections)
	if err != nil {
		return ModelData{}, err
	}
	return ModelData{
		Profile:           raw.Profile,
		CapabilityProfile: raw.CapabilityProfile,
		Inventory:         inventory,
		Connections:       connectionSet,
	}, nil
}

func loadCapabilityProfile(path string) (CapabilityProfile, error) {
	var raw capabilityFile
	if err := decodeYAML(path, &raw); err != nil {
		return CapabilityProfile{}, fmt.Errorf("decode capability profile: %w", err)
	}
	if raw.Profile == "" || raw.ModelVersion == "" || raw.GNMI.Version == "" {
		return CapabilityProfile{}, fmt.Errorf("capability profile is missing required metadata")
	}
	if len(raw.GNMI.Encodings) == 0 || len(raw.GNMI.Models) == 0 {
		return CapabilityProfile{}, fmt.Errorf("capability profile must define gNMI encodings and models")
	}
	validStatuses := map[string]bool{
		"SUPPORTED": true, "DERIVED": true, "PLANNED": true,
		"UNSUPPORTED": true, "OUT_OF_SCOPE": true,
	}
	seen := make(map[string]bool, len(raw.Capabilities))
	for _, capability := range raw.Capabilities {
		if capability.ID == "" || capability.DraftArea == "" || !validStatuses[capability.Status] {
			return CapabilityProfile{}, fmt.Errorf("invalid capability profile entry %q", capability.ID)
		}
		if seen[capability.ID] {
			return CapabilityProfile{}, fmt.Errorf("duplicate capability id %s", capability.ID)
		}
		seen[capability.ID] = true
	}
	for _, advertisedModel := range raw.GNMI.Models {
		if advertisedModel.Name == "" || advertisedModel.Organization == "" || advertisedModel.Version == "" {
			return CapabilityProfile{}, fmt.Errorf("each gNMI model must define name, organization, and version")
		}
	}
	return CapabilityProfile{
		Profile:      raw.Profile,
		ModelVersion: raw.ModelVersion,
		GNMIVersion:  raw.GNMI.Version,
		Encodings:    raw.GNMI.Encodings,
		Models:       raw.GNMI.Models,
	}, nil
}

func decodeYAML(path string, target any) error {
	file, err := os.Open(path)
	if err != nil {
		return err
	}
	defer file.Close()
	decoder := yaml.NewDecoder(file)
	decoder.KnownFields(true)
	if err := decoder.Decode(target); err != nil {
		return err
	}
	var extra any
	if err := decoder.Decode(&extra); err != io.EOF {
		if err == nil {
			return fmt.Errorf("YAML must contain exactly one document")
		}
		return err
	}
	return nil
}

func resolve(base, path string) string {
	if filepath.IsAbs(path) {
		return path
	}
	return filepath.Join(base, path)
}
