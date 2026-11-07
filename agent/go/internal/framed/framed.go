// Package framed implements sequential OCS protobuf RPC without automatic write retries.
package framed

import (
	"context"
	"encoding/binary"
	"errors"
	"fmt"
	"io"
	"net"
	"strings"
	"sync"
	"time"

	statuspb "google.golang.org/genproto/googleapis/rpc/status"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
)

const MaxFrame = 1 << 20

type Call struct {
	ID                      uint64
	Method, Lease, Revision string
	Compact                 bool
	Payload                 []byte
}

func Read(r io.Reader) ([]byte, error) {
	var header [4]byte
	if _, err := io.ReadFull(r, header[:]); err != nil {
		return nil, err
	}
	n := binary.BigEndian.Uint32(header[:])
	if n == 0 || n > MaxFrame {
		return nil, fmt.Errorf("invalid frame size %d", n)
	}
	body := make([]byte, int(n))
	_, err := io.ReadFull(r, body)
	return body, err
}

func Write(w io.Writer, body []byte) error {
	if len(body) == 0 || len(body) > MaxFrame {
		return fmt.Errorf("invalid frame size %d", len(body))
	}
	packet := binary.BigEndian.AppendUint32(make([]byte, 0, len(body)+4), uint32(len(body)))
	packet = append(packet, body...)
	for len(packet) > 0 {
		n, err := w.Write(packet)
		if err != nil {
			return err
		}
		if n == 0 {
			return io.ErrShortWrite
		}
		packet = packet[n:]
	}
	return nil
}

func Encode(call Call) ([]byte, error) {
	b := append([]byte("OCS1"), make([]byte, 8)...)
	binary.BigEndian.PutUint64(b[4:12], call.ID)
	for _, s := range []string{call.Method, call.Lease, call.Revision} {
		if len(s) > 65535 {
			return nil, errors.New("metadata too long")
		}
		b = binary.BigEndian.AppendUint16(b, uint16(len(s)))
		b = append(b, s...)
	}
	var flags byte
	if call.Compact {
		flags = 1
	}
	b = append(b, flags)
	return append(b, call.Payload...), nil
}

func Decode(b []byte) (Call, error) {
	var c Call
	if len(b) < 19 || string(b[:4]) != "OCS1" {
		return c, errors.New("invalid request header")
	}
	c.ID = binary.BigEndian.Uint64(b[4:12])
	b = b[12:]
	for _, dst := range []*string{&c.Method, &c.Lease, &c.Revision} {
		if len(b) < 2 {
			return c, io.ErrUnexpectedEOF
		}
		n := int(binary.BigEndian.Uint16(b))
		b = b[2:]
		if len(b) < n {
			return c, io.ErrUnexpectedEOF
		}
		*dst = string(b[:n])
		b = b[n:]
	}
	if len(b) < 1 || b[0] > 1 {
		return c, errors.New("invalid flags")
	}
	c.Compact = b[0] == 1
	c.Payload = b[1:]
	return c, nil
}

type Handler func(context.Context, string, []byte) (proto.Message, error)

// ServeConn serves a persistent connection until shutdown, idle expiry or malformed input.
func ServeConn(ctx context.Context, conn net.Conn, handle Handler, timeout time.Duration) {
	defer conn.Close()
	stop := context.AfterFunc(ctx, func() { conn.Close() })
	defer stop()
	for {
		// Refresh on every exchange: the connection has no fixed lifetime.
		conn.SetReadDeadline(time.Now().Add(5 * time.Minute))
		body, err := Read(conn)
		if err != nil {
			return
		}
		call, err := Decode(body)
		if err != nil {
			return
		}
		md := metadata.MD{"x-ocs-control-lease": {call.Lease}, "x-ocs-expected-revision": {call.Revision}}
		if call.Compact {
			md["x-ocs-compact-reply"] = []string{"true"}
		}
		callCtx, cancel := context.WithTimeout(metadata.NewIncomingContext(ctx, md), timeout)
		reply, callErr := handle(callCtx, call.Method, call.Payload)
		cancel()
		var payload []byte
		if callErr == nil {
			payload, callErr = proto.Marshal(reply)
		}
		st, _ := proto.Marshal(status.Convert(callErr).Proto())
		response := binary.BigEndian.AppendUint64([]byte("OCR1"), call.ID)
		response = binary.BigEndian.AppendUint32(response, uint32(len(st)))
		response = append(response, st...)
		response = append(response, payload...)
		conn.SetWriteDeadline(time.Now().Add(timeout))
		if Write(conn, response) != nil {
			return
		}
	}
}

// Client adapts generated OcsOperations stubs to one in-flight call per connection.
type Client struct {
	mu     sync.Mutex
	conn   net.Conn
	next   uint64
	failed bool
}

func Dial(ctx context.Context, target string) (*Client, error) {
	network, address := "tcp", strings.TrimPrefix(target, "framed://")
	if rest, ok := strings.CutPrefix(target, "framed-unix://"); ok {
		network, address = "unix", rest
	}
	conn, err := (&net.Dialer{Timeout: 10 * time.Second}).DialContext(ctx, network, address)
	if err != nil {
		return nil, err
	}
	return &Client{conn: conn}, nil
}
func (c *Client) Close() error { return c.conn.Close() }
func (c *Client) Invoke(ctx context.Context, method string, args, reply any, opts ...grpc.CallOption) error {
	c.mu.Lock()
	defer c.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return status.FromContextError(err).Err()
	}
	if c.failed {
		return status.Error(codes.Unavailable, "connection outcome unknown; reconnect and query authoritative state")
	}
	payload, err := proto.Marshal(args.(proto.Message))
	if err != nil {
		return err
	}
	md, _ := metadata.FromOutgoingContext(ctx)
	first := func(key string) string {
		v := md.Get(key)
		if len(v) > 0 {
			return v[0]
		}
		return ""
	}
	c.next++
	body, err := Encode(Call{ID: c.next, Method: method, Lease: first("x-ocs-control-lease"), Revision: first("x-ocs-expected-revision"), Compact: first("x-ocs-compact-reply") == "true", Payload: payload})
	if err != nil {
		return err
	}
	deadline := time.Now().Add(10 * time.Second)
	if d, ok := ctx.Deadline(); ok && d.Before(deadline) {
		deadline = d
	}
	c.conn.SetDeadline(deadline)
	done := make(chan struct{})
	stop := context.AfterFunc(ctx, func() { c.conn.SetDeadline(time.Now()); close(done) })
	defer func() {
		if !stop() {
			<-done
		}
	}()
	poison := func(err error) error {
		c.failed = true
		c.conn.Close()
		return status.Errorf(codes.Unavailable, "exchange outcome unknown: %v", err)
	}
	if err := Write(c.conn, body); err != nil {
		return poison(err)
	}
	response, err := Read(c.conn)
	if err != nil {
		return poison(err)
	}
	if len(response) < 16 || string(response[:4]) != "OCR1" || binary.BigEndian.Uint64(response[4:12]) != c.next {
		return poison(errors.New("invalid response correlation"))
	}
	n := int(binary.BigEndian.Uint32(response[12:16]))
	response = response[16:]
	if n > len(response) {
		return poison(io.ErrUnexpectedEOF)
	}
	if n > 0 {
		st := &statuspb.Status{}
		if err := proto.Unmarshal(response[:n], st); err != nil {
			return poison(err)
		}
		if err := status.FromProto(st).Err(); err != nil {
			return err
		}
	}
	if err := proto.Unmarshal(response[n:], reply.(proto.Message)); err != nil {
		return poison(err)
	}
	return nil
}
func (*Client) NewStream(context.Context, *grpc.StreamDesc, string, ...grpc.CallOption) (grpc.ClientStream, error) {
	return nil, status.Error(codes.Unimplemented, "framed supports unary calls")
}
