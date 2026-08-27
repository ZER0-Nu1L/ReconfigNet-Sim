package apierr

import (
	"encoding/json"
	"errors"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

type Error struct {
	Code      codes.Code     `json:"-"`
	CodeName  string         `json:"error_code"`
	Message   string         `json:"error"`
	Details   map[string]any `json:"details,omitempty"`
	RequestID uint64         `json:"request_id,omitzero"`
	Timing    any            `json:"timing,omitempty"`
	Restored  bool           `json:"restored,omitzero"`
}

func New(code codes.Code, message string, details map[string]any) *Error {
	return &Error{
		Code:     code,
		CodeName: codeName(code),
		Message:  message,
		Details:  details,
	}
}

func codeName(code codes.Code) string {
	switch code {
	case codes.InvalidArgument:
		return "INVALID_ARGUMENT"
	case codes.NotFound:
		return "NOT_FOUND"
	case codes.FailedPrecondition:
		return "FAILED_PRECONDITION"
	case codes.Aborted:
		return "ABORTED"
	case codes.Unimplemented:
		return "UNIMPLEMENTED"
	case codes.Unavailable:
		return "UNAVAILABLE"
	case codes.Canceled:
		return "CANCELLED"
	case codes.DeadlineExceeded:
		return "DEADLINE_EXCEEDED"
	case codes.ResourceExhausted:
		return "RESOURCE_EXHAUSTED"
	default:
		return "INTERNAL"
	}
}

func (e *Error) Error() string {
	return e.Message
}

func As(err error) *Error {
	var target *Error
	if errors.As(err, &target) {
		return target
	}
	return New(codes.Internal, err.Error(), nil)
}

func GRPC(err error) error {
	apiError := As(err)
	payload, marshalErr := json.Marshal(map[string]any{
		"error":      apiError.Message,
		"error_code": apiError.CodeName,
		"details":    apiError.Details,
		"request_id": apiError.RequestID,
		"timing":     apiError.Timing,
	})
	if marshalErr != nil {
		return status.Error(apiError.Code, apiError.Message)
	}
	return status.Error(apiError.Code, string(payload))
}

func HTTPStatus(code codes.Code) int {
	switch code {
	case codes.InvalidArgument:
		return 400
	case codes.NotFound:
		return 404
	case codes.FailedPrecondition, codes.Aborted:
		return 409
	case codes.Unimplemented:
		return 501
	case codes.Unavailable:
		return 503
	case codes.ResourceExhausted:
		return 429
	default:
		return 500
	}
}
