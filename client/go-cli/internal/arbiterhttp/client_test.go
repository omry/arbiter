package arbiterhttp

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
)

type roundTripFunc func(*http.Request) (*http.Response, error)

func (fn roundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return fn(req)
}

func TestClientProgressiveDiscoveryAndInvocation(t *testing.T) {
	var requests []string
	var runBody string
	client := NewClientWithHTTP(
		"http://arbiter.test",
		&http.Client{
			Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
				requests = append(requests, req.Method+" "+req.URL.Path)
				switch req.Method + " " + req.URL.Path {
				case "GET /api/v1/info":
					return jsonResponse(200, `{"name":"arbiter","version":"1.2.3"}`), nil
				case "GET /api/v1/plugins":
					return jsonResponse(200, `{"plugins":[{"id":"smtp","summary":"Send mail"}]}`), nil
				case "GET /api/v1/plugins/smtp/operations":
					return jsonResponse(200, `{"plugin":"smtp","operations":[{"id":"smtp:send_email"}]}`), nil
				case "GET /api/v1/operations/smtp:send_email":
					return jsonResponse(200, `{"id":"smtp:send_email","input_schema":{"type":"object"}}`), nil
				case "POST /api/v1/operations/smtp:send_email":
					body, err := io.ReadAll(req.Body)
					if err != nil {
						t.Fatal(err)
					}
					runBody = string(body)
					return jsonResponse(200, `{"result":{"ok":true},"artifacts":[],"warnings":[]}`), nil
				default:
					t.Fatalf("unexpected request: %s %s", req.Method, req.URL.Path)
				}
				return nil, nil
			}),
		},
	)

	info, err := client.Info(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	plugins, err := client.Plugins(context.Background())
	if err != nil {
		t.Fatal(err)
	}
	operations, err := client.PluginOperations(context.Background(), "smtp")
	if err != nil {
		t.Fatal(err)
	}
	details, err := client.OperationDetails(context.Background(), "smtp:send_email")
	if err != nil {
		t.Fatal(err)
	}
	result, err := client.RunOperation(
		context.Background(),
		"smtp:send_email",
		map[string]any{"account": "bot"},
	)
	if err != nil {
		t.Fatal(err)
	}

	if info["name"] != "arbiter" {
		t.Fatalf("unexpected info payload: %#v", info)
	}
	if len(plugins["plugins"].([]any)) != 1 {
		t.Fatalf("unexpected plugins payload: %#v", plugins)
	}
	if operations["plugin"] != "smtp" {
		t.Fatalf("unexpected operations payload: %#v", operations)
	}
	if details["id"] != "smtp:send_email" {
		t.Fatalf("unexpected operation details payload: %#v", details)
	}
	if result["result"].(map[string]any)["ok"] != true {
		t.Fatalf("unexpected run payload: %#v", result)
	}
	if runBody != `{"args":{"account":"bot"}}` {
		t.Fatalf("unexpected run body: %s", runBody)
	}
	expectedRequests := strings.Join([]string{
		"GET /api/v1/info",
		"GET /api/v1/plugins",
		"GET /api/v1/plugins/smtp/operations",
		"GET /api/v1/operations/smtp:send_email",
		"POST /api/v1/operations/smtp:send_email",
	}, "\n")
	if strings.Join(requests, "\n") != expectedRequests {
		t.Fatalf("unexpected requests:\n%s", strings.Join(requests, "\n"))
	}
}

func TestClientMapsErrorEnvelope(t *testing.T) {
	client := NewClientWithHTTP(
		"http://arbiter.test",
		&http.Client{
			Transport: roundTripFunc(func(req *http.Request) (*http.Response, error) {
				return jsonResponse(
					400,
					`{"error":{"code":"validation_error","message":"account is required"}}`,
				), nil
			}),
		},
	)

	_, err := client.OperationDetails(context.Background(), "smtp:send_email")
	if err == nil {
		t.Fatal("expected error")
	}
	apiErr, ok := err.(APIError)
	if !ok {
		t.Fatalf("expected APIError, got %T", err)
	}
	if apiErr.StatusCode != 400 || apiErr.Code != "validation_error" {
		t.Fatalf("unexpected API error: %#v", apiErr)
	}
	if !strings.Contains(err.Error(), "account is required") {
		t.Fatalf("unexpected error message: %v", err)
	}
}

func jsonResponse(statusCode int, body string) *http.Response {
	return &http.Response{
		StatusCode: statusCode,
		Header:     http.Header{"Content-Type": []string{"application/json"}},
		Body:       io.NopCloser(bytes.NewBufferString(body)),
	}
}
