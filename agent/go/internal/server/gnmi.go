package server

import (
	"context"
	"encoding/json"
	"fmt"
	"strings"
	"time"

	"github.com/openconfig/gnmi/proto/gnmi"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/agent"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/apierr"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/config"
	"github.com/reconfig-net-sim/ocs-go-agent/internal/model"
	"google.golang.org/grpc/codes"
)

type gnmiServer struct {
	gnmi.UnimplementedGNMIServer
	agent      *agent.Agent
	capability config.CapabilityProfile
}

func (s *gnmiServer) Capabilities(
	_ context.Context,
	request *gnmi.CapabilityRequest,
) (*gnmi.CapabilityResponse, error) {
	if len(request.GetExtension()) != 0 {
		return nil, apierr.GRPC(apierr.New(
			codes.Unimplemented,
			"gNMI Capabilities extensions are not implemented", nil))
	}
	response := &gnmi.CapabilityResponse{GNMIVersion: s.capability.GNMIVersion}
	for _, encoding := range s.capability.Encodings {
		if value, ok := gnmi.Encoding_value[encoding]; ok {
			response.SupportedEncodings = append(
				response.SupportedEncodings, gnmi.Encoding(value))
		}
	}
	for _, advertisedModel := range s.capability.Models {
		response.SupportedModels = append(response.SupportedModels, &gnmi.ModelData{
			Name:         advertisedModel.Name,
			Organization: advertisedModel.Organization,
			Version:      advertisedModel.Version,
		})
	}
	return response, nil
}

func (s *gnmiServer) Get(
	_ context.Context,
	request *gnmi.GetRequest,
) (*gnmi.GetResponse, error) {
	if request.GetEncoding() != gnmi.Encoding_JSON_IETF {
		return nil, apierr.GRPC(apierr.New(
			codes.Unimplemented, "only JSON_IETF Get encoding is supported", nil))
	}
	if len(request.GetUseModels()) != 0 {
		return nil, apierr.GRPC(apierr.New(
			codes.Unimplemented, "Get.use_models filtering is not implemented", nil))
	}
	if len(request.GetExtension()) != 0 {
		return nil, apierr.GRPC(apierr.New(
			codes.Unimplemented, "gNMI Get extensions are not implemented", nil))
	}
	paths := request.GetPath()
	if len(paths) == 0 {
		paths = []*gnmi.Path{{}}
	}
	response := &gnmi.GetResponse{}
	for _, requestedPath := range paths {
		path := joinPath(request.GetPrefix(), requestedPath)
		value, err := selectTree(s.agent.OpenConfigTree(), path)
		if err != nil {
			return nil, apierr.GRPC(err)
		}
		value, err = filterGetData(value, request.GetType())
		if err != nil {
			return nil, apierr.GRPC(err)
		}
		encoded, err := json.Marshal(value)
		if err != nil {
			return nil, apierr.GRPC(err)
		}
		response.Notification = append(response.Notification, &gnmi.Notification{
			Timestamp: time.Now().UnixNano(),
			Update: []*gnmi.Update{{
				Path: path,
				Val:  &gnmi.TypedValue{Value: &gnmi.TypedValue_JsonIetfVal{JsonIetfVal: encoded}},
			}},
		})
	}
	return response, nil
}

func (s *gnmiServer) Set(
	ctx context.Context,
	request *gnmi.SetRequest,
) (*gnmi.SetResponse, error) {
	if len(request.GetExtension()) != 0 {
		return nil, apierr.GRPC(apierr.New(
			codes.Unimplemented, "gNMI Set extensions are not implemented", nil))
	}
	if len(request.GetUpdate()) != 0 || len(request.GetUnionReplace()) != 0 {
		return nil, apierr.GRPC(apierr.New(
			codes.Unimplemented,
			"Set.update and union_replace are not supported; use replace", nil))
	}
	operations := make([]agent.ConnectionOperation, 0,
		len(request.GetDelete())+len(request.GetReplace()))
	resultPaths := make([]*gnmi.UpdateResult, 0, cap(operations))

	for _, deletePath := range request.GetDelete() {
		path := joinPath(request.GetPrefix(), deletePath)
		names := normalizedNames(path)
		connectionName := connectionNameFromPath(path)
		if len(names) != 2 || names[0] != "optical-switch-connections" ||
			names[1] != "port-connection" || connectionName == "" {
			return nil, apierr.GRPC(apierr.New(
				codes.Unimplemented,
				"delete only supports a complete port-connection entry", nil))
		}
		operations = append(operations, agent.ConnectionOperation{
			Kind: "delete", Name: connectionName,
		})
		resultPaths = append(resultPaths, &gnmi.UpdateResult{
			Path: path,
			Op:   gnmi.UpdateResult_DELETE,
		})
	}

	for _, replace := range request.GetReplace() {
		path := joinPath(request.GetPrefix(), replace.GetPath())
		names := normalizedNames(path)
		value, err := typedJSON(replace.GetVal())
		if err != nil {
			return nil, apierr.GRPC(err)
		}
		if len(names) == 1 && names[0] == "optical-switch-connections" {
			connections, err := connectionSetFromJSON(s.agent.Inventory(), value)
			if err != nil {
				return nil, apierr.GRPC(err)
			}
			operations = append(operations, agent.ConnectionOperation{
				Kind: "replace_all", All: &connections,
			})
		} else if len(names) == 2 && names[0] == "optical-switch-connections" &&
			names[1] == "port-connection" {
			connectionName := connectionNameFromPath(path)
			if connectionName == "" {
				return nil, apierr.GRPC(apierr.New(
					codes.InvalidArgument,
					"port-connection replace requires connection-name key", nil))
			}
			connection, err := connectionFromJSON(value, connectionName)
			if err != nil {
				return nil, apierr.GRPC(err)
			}
			operations = append(operations, agent.ConnectionOperation{
				Kind: "replace", Connection: connection,
			})
		} else {
			return nil, apierr.GRPC(apierr.New(
				codes.Unimplemented,
				fmt.Sprintf("replace path is not supported: %s", pathString(path)), nil))
		}
		resultPaths = append(resultPaths, &gnmi.UpdateResult{
			Path: path,
			Op:   gnmi.UpdateResult_REPLACE,
		})
	}
	if len(operations) == 0 {
		return nil, apierr.GRPC(apierr.New(
			codes.InvalidArgument, "SetRequest contains no operations", nil))
	}
	expectedRevision, err := expectedRevisionFromContext(ctx)
	if err != nil {
		return nil, apierr.GRPC(err)
	}
	result, err := s.agent.ApplyConnectionOperations(
		ctx, operations, expectedRevision, leaseFromContext(ctx))
	if err != nil {
		return nil, apierr.GRPC(err)
	}
	timestamp := time.Now().UnixNano()
	for _, item := range resultPaths {
		item.Timestamp = timestamp
	}
	message, _ := json.Marshal(map[string]any{
		"request_id": result.RequestID,
		"revision":   result.Revision,
		"result":     result.Result,
		"timing":     result.Timing,
	})
	return &gnmi.SetResponse{
		Prefix:    request.GetPrefix(),
		Response:  resultPaths,
		Timestamp: timestamp,
		Message:   &gnmi.Error{Code: 0, Message: string(message)},
	}, nil
}

func joinPath(prefix, path *gnmi.Path) *gnmi.Path {
	result := &gnmi.Path{}
	if prefix != nil {
		result.Origin = prefix.GetOrigin()
		result.Target = prefix.GetTarget()
		result.Elem = append(result.Elem, clonePathElems(prefix.GetElem())...)
	}
	if path != nil {
		if path.GetOrigin() != "" {
			result.Origin = path.GetOrigin()
		}
		if path.GetTarget() != "" {
			result.Target = path.GetTarget()
		}
		result.Elem = append(result.Elem, clonePathElems(path.GetElem())...)
	}
	return result
}

func clonePathElems(elements []*gnmi.PathElem) []*gnmi.PathElem {
	result := make([]*gnmi.PathElem, 0, len(elements))
	for _, element := range elements {
		keys := make(map[string]string, len(element.GetKey()))
		for key, value := range element.GetKey() {
			keys[key] = value
		}
		result = append(result, &gnmi.PathElem{Name: element.GetName(), Key: keys})
	}
	return result
}

func normalizedNames(path *gnmi.Path) []string {
	names := make([]string, 0, len(path.GetElem()))
	for _, element := range path.GetElem() {
		name := element.GetName()
		if colon := strings.LastIndex(name, ":"); colon >= 0 {
			name = name[colon+1:]
		}
		names = append(names, name)
	}
	return names
}

func connectionNameFromPath(path *gnmi.Path) string {
	for _, element := range path.GetElem() {
		name := element.GetName()
		if colon := strings.LastIndex(name, ":"); colon >= 0 {
			name = name[colon+1:]
		}
		if name == "port-connection" {
			return element.GetKey()["connection-name"]
		}
	}
	return ""
}

func selectTree(tree map[string]any, path *gnmi.Path) (any, error) {
	names := normalizedNames(path)
	if len(names) == 0 {
		return tree, nil
	}
	switch names[0] {
	case "components":
		root := tree["openconfig-platform:components"].(map[string]any)
		if len(names) == 1 {
			return root, nil
		}
		if names[1] != "component" {
			return nil, unsupportedPath(path)
		}
		components := root["component"].([]any)
		requested := ""
		for _, element := range path.GetElem() {
			if strings.HasSuffix(element.GetName(), "component") {
				requested = element.GetKey()["name"]
			}
		}
		if requested == "" {
			return traverse(map[string]any{"component": components}, names[2:], path)
		}
		for _, item := range components {
			component := item.(map[string]any)
			if component["name"] == requested {
				return traverse(component, names[2:], path)
			}
		}
		return nil, apierr.New(codes.InvalidArgument, "unknown component "+requested, nil)
	case "optical-switch":
		root := tree["oc-optical-switch:optical-switch"].(map[string]any)
		return traverse(root, names[1:], path)
	case "optical-switch-connections":
		root := tree["oc-optical-switch-connections:optical-switch-connections"].(map[string]any)
		if len(names) == 1 {
			return root, nil
		}
		if names[1] != "port-connection" {
			return nil, unsupportedPath(path)
		}
		connections := root["port-connection"].([]any)
		requested := connectionNameFromPath(path)
		if requested == "" {
			return traverse(map[string]any{"port-connection": connections}, names[2:], path)
		}
		for _, item := range connections {
			connection := item.(map[string]any)
			if connection["connection-name"] == requested {
				return traverse(connection, names[2:], path)
			}
		}
		return nil, apierr.New(codes.InvalidArgument, "unknown connection "+requested, nil)
	default:
		return nil, unsupportedPath(path)
	}
}

func traverse(value any, names []string, path *gnmi.Path) (any, error) {
	current := value
	for _, name := range names {
		object, ok := current.(map[string]any)
		if !ok {
			return nil, unsupportedPath(path)
		}
		current, ok = object[name]
		if !ok {
			return nil, unsupportedPath(path)
		}
	}
	return current, nil
}

func filterGetData(value any, dataType gnmi.GetRequest_DataType) (any, error) {
	if dataType == gnmi.GetRequest_ALL || dataType == gnmi.GetRequest_OPERATIONAL {
		return value, nil
	}
	if dataType != gnmi.GetRequest_CONFIG && dataType != gnmi.GetRequest_STATE {
		return nil, apierr.New(
			codes.Unimplemented,
			fmt.Sprintf("unsupported gNMI Get data type %d", dataType), nil)
	}
	excluded := "state"
	if dataType == gnmi.GetRequest_STATE {
		excluded = "config"
	}
	return filterValue(value, excluded), nil
}

func filterValue(value any, excluded string) any {
	switch typed := value.(type) {
	case []any:
		result := make([]any, 0, len(typed))
		for _, item := range typed {
			result = append(result, filterValue(item, excluded))
		}
		return result
	case map[string]any:
		result := make(map[string]any, len(typed))
		for key, child := range typed {
			normalized := key
			if colon := strings.LastIndex(key, ":"); colon >= 0 {
				normalized = key[colon+1:]
			}
			if normalized != excluded {
				result[key] = filterValue(child, excluded)
			}
		}
		return result
	default:
		return value
	}
}

func typedJSON(value *gnmi.TypedValue) (map[string]any, error) {
	var raw []byte
	switch typed := value.GetValue().(type) {
	case *gnmi.TypedValue_JsonIetfVal:
		raw = typed.JsonIetfVal
	case *gnmi.TypedValue_JsonVal:
		raw = typed.JsonVal
	default:
		return nil, apierr.New(
			codes.InvalidArgument,
			"Set values must use JSON_IETF or JSON encoding", nil)
	}
	var result map[string]any
	decoder := json.NewDecoder(strings.NewReader(string(raw)))
	if err := decoder.Decode(&result); err != nil {
		return nil, apierr.New(
			codes.InvalidArgument, "Set value must contain valid JSON", nil)
	}
	return result, nil
}

func connectionFromJSON(value map[string]any, pathName string) (model.Connection, error) {
	allowed := map[string]bool{
		"connection-name": true, "config": true, "bidirectional": true,
		"near-port-name": true, "far-port-name": true,
	}
	for key := range value {
		if !allowed[key] {
			return model.Connection{}, apierr.New(
				codes.Unimplemented,
				"connection write contains unsupported fields: "+key, nil)
		}
	}
	configValue := value
	if nested, exists := value["config"]; exists {
		var ok bool
		configValue, ok = nested.(map[string]any)
		if !ok {
			return model.Connection{}, apierr.New(
				codes.InvalidArgument, "connection config must be an object", nil)
		}
	}
	for key := range configValue {
		if key != "connection-name" && key != "bidirectional" &&
			key != "near-port-name" && key != "far-port-name" {
			return model.Connection{}, apierr.New(
				codes.Unimplemented,
				"connection config contains unsupported fields: "+key, nil)
		}
	}
	name := stringValue(configValue["connection-name"])
	if name == "" {
		name = stringValue(value["connection-name"])
	}
	if pathName != "" {
		if name != "" && name != pathName {
			return model.Connection{}, apierr.New(
				codes.InvalidArgument,
				"connection name in value must match path key "+pathName, nil)
		}
		name = pathName
	}
	bidirectional := true
	if raw, exists := configValue["bidirectional"]; exists {
		var ok bool
		bidirectional, ok = raw.(bool)
		if !ok {
			return model.Connection{}, apierr.New(
				codes.InvalidArgument, "bidirectional must be a boolean", nil)
		}
	}
	return model.Connection{
		Name:          name,
		Bidirectional: bidirectional,
		NearPortName:  stringValue(configValue["near-port-name"]),
		FarPortName:   stringValue(configValue["far-port-name"]),
	}, nil
}

func connectionSetFromJSON(
	inventory model.Inventory,
	value map[string]any,
) (model.ConnectionSet, error) {
	if nested, exists := value["oc-optical-switch-connections:optical-switch-connections"]; exists {
		var ok bool
		value, ok = nested.(map[string]any)
		if !ok {
			return model.ConnectionSet{}, apierr.New(
				codes.InvalidArgument, "connections subtree must be an object", nil)
		}
	}
	if len(value) != 1 {
		return model.ConnectionSet{}, apierr.New(
			codes.Unimplemented,
			"connections subtree contains unsupported fields", nil)
	}
	rawConnections, ok := value["port-connection"].([]any)
	if !ok {
		return model.ConnectionSet{}, apierr.New(
			codes.InvalidArgument,
			"connections subtree must contain port-connection list", nil)
	}
	connections := make([]model.Connection, 0, len(rawConnections))
	for _, raw := range rawConnections {
		object, ok := raw.(map[string]any)
		if !ok {
			return model.ConnectionSet{}, apierr.New(
				codes.InvalidArgument, "each connection must be an object", nil)
		}
		connection, err := connectionFromJSON(object, "")
		if err != nil {
			return model.ConnectionSet{}, err
		}
		connections = append(connections, connection)
	}
	connectionSet, err := model.NewConnectionSet(inventory, connections)
	if err != nil {
		return model.ConnectionSet{}, apierr.New(codes.InvalidArgument, err.Error(), nil)
	}
	return connectionSet, nil
}

func stringValue(value any) string {
	result, _ := value.(string)
	return result
}

func unsupportedPath(path *gnmi.Path) error {
	return apierr.New(
		codes.Unimplemented,
		"unsupported gNMI path "+pathString(path), nil)
}

func pathString(path *gnmi.Path) string {
	parts := make([]string, 0, len(path.GetElem()))
	for _, element := range path.GetElem() {
		parts = append(parts, element.GetName())
	}
	return "/" + strings.Join(parts, "/")
}
