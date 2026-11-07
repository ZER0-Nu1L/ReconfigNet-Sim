// Package bfrtclient talks to bf_switchd's BFRT gRPC service directly.
//
// The Go Agent and standalone benchmarks share this protocol adapter. Device
// ownership and recovery policy belong to their callers.
//
// The request layout was captured from the production Python backend
// (ocs_agent/backends/bfrt.py, write RPC intercepted) and the ids are
// discovered at run time from the bfruntime_info JSON returned by
// GetForwardingPipelineConfig, so a recompiled P4 program needs no code change.
package bfrtclient

import (
	"cmp"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"maps"
	"slices"
	"strings"
	"sync/atomic"
	"time"

	bfruntime "github.com/reconfig-net-sim/ocs-go-agent/gen/bfruntime"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials/insecure"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
)

const serviceName = "/bfrt_proto.BfRuntime/"

// A single mixed batch requests rollback on error, not dataplane atomicity.
const bfrtAtomic = bfruntime.WriteRequest_ROLLBACK_ON_ERROR

// Config selects the program, table and fields this client talks to.
type Config struct {
	Target          string        `json:"target"`
	ClientID        uint32        `json:"client_id"`
	DeviceID        uint32        `json:"device_id"`
	PipeID          uint32        `json:"pipe_id"`
	P4Name          string        `json:"p4_name"`
	TableName       string        `json:"table_name"`
	ActionName      string        `json:"action_name"`
	IngressField    string        `json:"ingress_field"`
	EgressField     string        `json:"egress_field"`
	LogicalToDevice map[int]int   `json:"logical_to_device"`
	Timeout         time.Duration `json:"-"`
}

// Client owns one BFRT client id on the device.
type Client struct {
	config          Config
	conn            *grpc.ClientConn
	stream          grpc.ClientStream
	failed          atomic.Bool
	tableID         uint32
	actionID        uint32
	keyFields       map[string]Field
	deviceToLogical map[int]int
}

// Field is one key field of a table.
type Field struct {
	ID         uint32
	WidthBytes int
}

type infoTable struct {
	Name string `json:"name"`
	ID   uint32 `json:"id"`
	Key  []struct {
		Name string `json:"name"`
		ID   uint32 `json:"id"`
		Type struct {
			Type  string `json:"type"`
			Width int    `json:"width"`
		} `json:"type"`
	} `json:"key"`
	ActionSpecs []struct {
		Name string            `json:"name"`
		ID   uint32            `json:"id"`
		Data []json.RawMessage `json:"data"`
	} `json:"action_specs"`
}

type bfrtInfo struct {
	Tables []infoTable `json:"tables"`
}

// Validate checks site-neutral configuration before opening a connection.
func (config Config) Validate() error {
	if config.Target == "" || config.P4Name == "" || config.TableName == "" || config.ActionName == "" || config.IngressField == "" || config.EgressField == "" || config.IngressField == config.EgressField || len(config.LogicalToDevice) < 2 || config.PipeID > 0xffff {
		return fmt.Errorf("explicit BFRT endpoint, pipeline, pair table, distinct fields, ports and valid pipe id are required")
	}
	seen := map[int]bool{}
	for logical, port := range config.LogicalToDevice {
		if logical < 1 || port < 0 || seen[port] {
			return fmt.Errorf("invalid or duplicate device-port mapping")
		}
		seen[port] = true
	}
	return nil
}

// Dial connects, subscribes, reads the pipeline info and binds the program.
func Dial(ctx context.Context, config Config) (*Client, error) {
	if err := config.Validate(); err != nil {
		return nil, err
	}
	config.LogicalToDevice = maps.Clone(config.LogicalToDevice)
	if config.Timeout <= 0 {
		config.Timeout = 2 * time.Second
	}
	conn, err := grpc.NewClient(config.Target,
		grpc.WithTransportCredentials(insecure.NewCredentials()))
	if err != nil {
		return nil, err
	}
	client := &Client{
		config:          config,
		conn:            conn,
		keyFields:       map[string]Field{},
		deviceToLogical: map[int]int{},
	}
	for logical, device := range config.LogicalToDevice {
		client.deviceToLogical[device] = logical
	}
	for _, step := range []func(context.Context) error{
		client.subscribe, client.loadInfo, client.bind,
	} {
		if err := step(ctx); err != nil {
			conn.Close()
			return nil, err
		}
	}
	return client, nil
}

// Close releases the client id and the connection.
func (c *Client) Close() error {
	if c.stream != nil {
		_ = c.stream.CloseSend()
	}
	return c.conn.Close()
}

// RefreshInfo re-issues GetForwardingPipelineConfig. It performs no table work,
// and includes the entire pipeline-info payload; it is not a write RPC floor.
func (c *Client) RefreshInfo(ctx context.Context) error {
	return c.loadInfo(ctx)
}

// TableID reports the discovered table id.
func (c *Client) TableID() uint32 { return c.tableID }

// ActionID reports the discovered action id.
func (c *Client) ActionID() uint32 { return c.actionID }

// KeyField reports a discovered key field.
func (c *Client) KeyField(name string) (Field, bool) {
	field, ok := c.keyFields[name]
	return field, ok
}

func (c *Client) subscribe(ctx context.Context) error {
	stream, err := c.conn.NewStream(ctx, &grpc.StreamDesc{
		StreamName:    "StreamChannel",
		ServerStreams: true,
		ClientStreams: true,
	}, serviceName+"StreamChannel")
	if err != nil {
		return err
	}
	c.stream = stream
	request := &bfruntime.StreamMessageRequest{
		ClientId: c.config.ClientID,
		Update: &bfruntime.StreamMessageRequest_Subscribe{
			Subscribe: &bfruntime.Subscribe{DeviceId: c.config.DeviceID},
		},
	}
	if err := stream.SendMsg(request); err != nil {
		return err
	}
	timer := time.AfterFunc(c.config.Timeout, func() { c.conn.Close() })
	defer timer.Stop()
	for {
		response := &bfruntime.StreamMessageResponse{}
		if err := stream.RecvMsg(response); err != nil {
			return fmt.Errorf("subscribe: %w", err)
		}
		if subscribed := response.GetSubscribe(); subscribed != nil {
			if subscribed.GetStatus().GetCode() != 0 {
				return fmt.Errorf("BFRT subscription rejected: %v", subscribed.GetStatus())
			}
			if !timer.Stop() {
				return fmt.Errorf("BFRT subscription timed out")
			}
			go func() {
				for {
					if err := stream.RecvMsg(&bfruntime.StreamMessageResponse{}); err != nil {
						c.failed.Store(true)
						return
					}
				}
			}()
			return nil
		}
	}
}

func (c *Client) loadInfo(ctx context.Context) error {
	c.tableID, c.actionID = 0, 0
	c.keyFields = map[string]Field{}
	ctx, cancel := context.WithTimeout(ctx, c.config.Timeout)
	defer cancel()
	request := &bfruntime.GetForwardingPipelineConfigRequest{
		DeviceId: c.config.DeviceID,
		ClientId: c.config.ClientID,
	}
	response := &bfruntime.GetForwardingPipelineConfigResponse{}
	if err := c.conn.Invoke(ctx, serviceName+"GetForwardingPipelineConfig",
		request, response); err != nil {
		return err
	}
	for _, config := range response.GetConfig() {
		if config.GetP4Name() != c.config.P4Name {
			continue
		}
		info := bfrtInfo{}
		if err := json.Unmarshal(config.GetBfruntimeInfo(), &info); err != nil {
			return fmt.Errorf("bfruntime_info: %w", err)
		}
		matchedTables := 0
		for _, candidate := range info.Tables {
			if matchesName(candidate.Name, c.config.TableName) {
				matchedTables++
			}
		}
		if matchedTables != 1 {
			return fmt.Errorf("pair table must match exactly one BFRT table, got %d", matchedTables)
		}
		for _, table := range info.Tables {
			if !matchesName(table.Name, c.config.TableName) {
				continue
			}
			if len(table.Key) != 2 {
				return fmt.Errorf("BFRT adapter requires a two-key pair table")
			}
			c.tableID = table.ID
			for _, key := range table.Key {
				width := (key.Type.Width + 7) / 8
				if width == 0 {
					width = 4
				}
				if key.Type.Width < 1 || key.Type.Width > 32 {
					return fmt.Errorf("unsupported key width for %s", key.Name)
				}
				for _, port := range c.config.LogicalToDevice {
					if uint64(port) >= uint64(1)<<key.Type.Width {
						return fmt.Errorf("device port does not fit field %s", key.Name)
					}
				}
				c.keyFields[key.Name] = Field{ID: key.ID, WidthBytes: width}
			}
			matchedActions := 0
			for _, action := range table.ActionSpecs {
				if matchesName(action.Name, c.config.ActionName) {
					matchedActions++
					if len(action.Data) != 0 {
						return fmt.Errorf("BFRT adapter requires a parameterless permit action")
					}
					c.actionID = action.ID
				}
			}
			if matchedActions != 1 {
				return fmt.Errorf("permit action must match exactly one BFRT action, got %d", matchedActions)
			}
		}
		if c.tableID != 0 && c.actionID != 0 {
			for _, name := range []string{c.config.IngressField, c.config.EgressField} {
				f, exists := c.keyFields[name]
				if !exists || f.WidthBytes < 1 || f.WidthBytes > 4 {
					return fmt.Errorf("invalid or missing field %s", name)
				}
			}
			return nil
		}
	}
	return fmt.Errorf("table %s not found in the pipeline info", c.config.TableName)
}

// matchesName accepts both the short name from the Agent config and the
// fully qualified name used by the compiler ("pipe.SwitchIngress.ocs_mapping").
func matchesName(actual, wanted string) bool {
	if actual == wanted {
		return true
	}
	suffix := "." + wanted
	return strings.HasSuffix(actual, suffix)
}

func (c *Client) bind(ctx context.Context) error {
	ctx, cancel := context.WithTimeout(ctx, c.config.Timeout)
	defer cancel()
	request := &bfruntime.SetForwardingPipelineConfigRequest{
		DeviceId: c.config.DeviceID,
		ClientId: c.config.ClientID,
		Action:   bfruntime.SetForwardingPipelineConfigRequest_BIND,
		Config:   []*bfruntime.ForwardingPipelineConfig{{P4Name: c.config.P4Name}},
	}
	return c.conn.Invoke(ctx, serviceName+"SetForwardingPipelineConfig",
		request, &bfruntime.SetForwardingPipelineConfigResponse{})
}

// WriteAtomic sends one mixed batch using ROLLBACK_ON_ERROR.
// It does not guarantee a dataplane transition without intermediate states.
func (c *Client) WriteAtomic(ctx context.Context, deletes, inserts [][2]int) error {
	ctx, cancel := context.WithTimeout(ctx, c.config.Timeout)
	defer cancel()
	request, err := c.buildRequest(deletes, bfruntime.Update_DELETE)
	if err != nil {
		return err
	}
	insertRequest, err := c.buildRequest(inserts, bfruntime.Update_INSERT)
	if err != nil {
		return err
	}
	request.Updates = append(request.Updates, insertRequest.Updates...)
	request.Atomicity = bfrtAtomic

	return c.sendWrite(ctx, request)
}

// WritePairs applies deletes then inserts, matching the two-request sequence
// the Python backend uses for the deployed pairs layout.
func (c *Client) WritePairs(ctx context.Context, deletes, inserts [][2]int) error {
	// Validate both requests before sending any mutation.
	remove, err := c.buildRequest(deletes, bfruntime.Update_DELETE)
	if err != nil {
		return err
	}
	add, err := c.buildRequest(inserts, bfruntime.Update_INSERT)
	if err != nil {
		return err
	}
	for index, request := range []*bfruntime.WriteRequest{remove, add} {
		if len(request.Updates) == 0 {
			continue
		}
		rpcCtx := ctx
		if md, ok := metadata.FromOutgoingContext(ctx); ok {
			if ids := md.Get("x-ocs-request-id"); len(ids) == 1 {
				md = md.Copy()
				md.Set("x-ocs-request-id", ids[0]+[]string{"-delete", "-insert"}[index])
				rpcCtx = metadata.NewOutgoingContext(ctx, md)
			}
		}
		if err := c.sendWrite(rpcCtx, request); err != nil {
			return err
		}
	}
	return nil
}

func (c *Client) sendWrite(ctx context.Context, request *bfruntime.WriteRequest) error {
	ctx, cancel := context.WithTimeout(ctx, c.config.Timeout)
	defer cancel()
	if c.failed.Load() {
		return fmt.Errorf("BFRT subscription lost")
	}
	response := &bfruntime.WriteResponse{}
	if err := c.conn.Invoke(ctx, serviceName+"Write", request, response); err != nil {
		return err
	}
	for _, item := range response.GetStatus() {
		if item.GetCanonicalCode() != 0 {
			return status.Error(codes.Code(item.GetCanonicalCode()), item.GetMessage())
		}
	}
	return nil
}

func (c *Client) buildRequest(pairs [][2]int, updateType bfruntime.Update_Type) (*bfruntime.WriteRequest, error) {
	ingress, ok := c.keyFields[c.config.IngressField]
	if !ok {
		return nil, fmt.Errorf("key field %s is unknown", c.config.IngressField)
	}
	egress, ok := c.keyFields[c.config.EgressField]
	if !ok {
		return nil, fmt.Errorf("key field %s is unknown", c.config.EgressField)
	}
	ordered := slices.Clone(pairs)
	slices.SortFunc(ordered, func(a, b [2]int) int { return cmp.Or(cmp.Compare(a[0], b[0]), cmp.Compare(a[1], b[1])) })
	request := &bfruntime.WriteRequest{
		Target:   c.targetDevice(),
		ClientId: c.config.ClientID,
		Updates:  make([]*bfruntime.Update, 0, len(ordered)),
	}
	for _, pair := range ordered {
		ingressPort, err := c.toDevice(pair[0])
		if err != nil {
			return nil, err
		}
		egressPort, err := c.toDevice(pair[1])
		if err != nil {
			return nil, err
		}
		entry := &bfruntime.TableEntry{
			TableId: c.tableID,
			Value: &bfruntime.TableEntry_Key{Key: &bfruntime.TableKey{
				Fields: []*bfruntime.KeyField{
					c.exactField(ingress, ingressPort),
					c.exactField(egress, egressPort),
				},
			}},
		}
		if updateType == bfruntime.Update_INSERT {
			entry.Data = &bfruntime.TableData{ActionId: c.actionID}
		}
		request.Updates = append(request.Updates, &bfruntime.Update{
			Type: updateType,
			Entity: &bfruntime.Entity{
				Entity: &bfruntime.Entity_TableEntry{TableEntry: entry},
			},
		})
	}
	return request, nil
}

func (c *Client) exactField(field Field, value int) *bfruntime.KeyField {
	encoded := make([]byte, field.WidthBytes)
	for i := range field.WidthBytes {
		encoded[field.WidthBytes-1-i] = byte(uint64(value) >> (8 * i))
	}

	return &bfruntime.KeyField{
		FieldId: field.ID,
		MatchType: &bfruntime.KeyField_Exact_{
			Exact: &bfruntime.KeyField_Exact{Value: encoded},
		},
	}
}

func (c *Client) targetDevice() *bfruntime.TargetDevice {
	return &bfruntime.TargetDevice{
		DeviceId:  c.config.DeviceID,
		PipeId:    c.config.PipeID,
		Direction: 0xff,
		PrsrId:    0xff,
	}
}

// ReadPairs returns the logical mapping the device currently holds.
func (c *Client) ReadPairs(ctx context.Context) ([][2]int, error) {
	ctx, cancel := context.WithTimeout(ctx, c.config.Timeout)
	defer cancel()
	if c.failed.Load() {
		return nil, fmt.Errorf("BFRT subscription lost; restart the device connection before recovery")
	}
	ingress, ok := c.keyFields[c.config.IngressField]
	if !ok {
		return nil, fmt.Errorf("key field %s is unknown", c.config.IngressField)
	}
	egress, ok := c.keyFields[c.config.EgressField]
	if !ok {
		return nil, fmt.Errorf("key field %s is unknown", c.config.EgressField)
	}
	request := &bfruntime.ReadRequest{
		ClientId: c.config.ClientID,
		Target:   c.targetDevice(),
		Entities: []*bfruntime.Entity{{
			Entity: &bfruntime.Entity_TableEntry{
				TableEntry: &bfruntime.TableEntry{
					TableId:       c.tableID,
					TableReadFlag: &bfruntime.TableReadFlag{FromHw: true},
				},
			},
		}},
	}
	stream, err := c.conn.NewStream(ctx, &grpc.StreamDesc{
		StreamName:    "Read",
		ServerStreams: true,
	}, serviceName+"Read")
	if err != nil {
		return nil, err
	}
	if err := stream.SendMsg(request); err != nil {
		return nil, err
	}
	if err := stream.CloseSend(); err != nil {
		return nil, err
	}
	pairs := [][2]int{}
	for {
		response := &bfruntime.ReadResponse{}
		if err := stream.RecvMsg(response); err != nil {
			if errors.Is(err, io.EOF) {
				break
			}
			return nil, err
		}
		for _, item := range response.GetStatus() {
			if item.GetCanonicalCode() != 0 {
				return nil, fmt.Errorf("BFRT read rejected: %v", item)
			}
		}
		for _, entity := range response.GetEntities() {
			entry := entity.GetTableEntry()
			if entry == nil || entry.GetIsDefaultEntry() {
				continue
			}
			if entry.GetTableId() != c.tableID {
				return nil, fmt.Errorf("BFRT returned an unexpected table")
			}
			if entry.GetData() == nil || entry.GetData().GetActionId() != c.actionID {
				return nil, fmt.Errorf("BFRT read contains a missing or unexpected pair action")
			}
			ingressValue, egressValue := 0, 0
			hasIngress, hasEgress := false, false
			for _, field := range entry.GetKey().GetFields() {
				switch field.GetFieldId() {
				case ingress.ID:
					if hasIngress || len(field.GetExact().GetValue()) < 1 || len(field.GetExact().GetValue()) > ingress.WidthBytes {
						return nil, fmt.Errorf("BFRT returned malformed ingress key")
					}
					ingressValue = decodeValue(field)
					hasIngress = field.GetExact() != nil
				case egress.ID:
					if hasEgress || len(field.GetExact().GetValue()) < 1 || len(field.GetExact().GetValue()) > egress.WidthBytes {
						return nil, fmt.Errorf("BFRT returned malformed egress key")
					}
					egressValue = decodeValue(field)
					hasEgress = field.GetExact() != nil
				}
			}
			if !hasIngress || !hasEgress {
				return nil, fmt.Errorf("BFRT returned incomplete pair keys")
			}
			in, inOK := c.deviceToLogical[ingressValue]
			out, outOK := c.deviceToLogical[egressValue]
			if !inOK || !outOK {
				return nil, fmt.Errorf("BFRT read contains unmapped ports")
			}
			pairs = append(pairs, [2]int{in, out})
		}
	}
	return pairs, nil
}

// toDevice maps a logical port to the device port the pipeline matches on.
func (c *Client) toDevice(logicalPort int) (int, error) {

	device, ok := c.config.LogicalToDevice[logicalPort]
	if !ok {
		return 0, fmt.Errorf("logical port %d has no device mapping", logicalPort)
	}
	return device, nil
}

func decodeValue(field *bfruntime.KeyField) int {
	value := 0
	for _, b := range field.GetExact().GetValue() {
		value = value<<8 | int(b)
	}
	return value
}

// IsDuplicate requires a definitive duplicate-entry response. A timeout is not
// evidence that rollback completed, even if an immediate read sees old state.
func IsDuplicate(err error) bool {
	s, ok := status.FromError(err)
	if !ok {
		return false
	}
	if s.Code() == codes.AlreadyExists {
		return true
	}
	if s.Code() != codes.Unknown {
		return false
	}
	for _, detail := range s.Details() {
		if item, ok := detail.(*bfruntime.Error); ok && item.GetCanonicalCode() == int32(codes.AlreadyExists) {
			return true
		}
	}
	return false
}
