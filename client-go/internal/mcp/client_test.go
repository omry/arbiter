package mcp_test

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"testing"

	"github.com/omry/arbiter/client-go/internal/mcp"
)

func TestClientListToolsInitializesSession(t *testing.T) {
	transport := &fakeTransport{
		t: t,
		handle: func(request map[string]any, headers http.Header) fakeResponse {
			method, _ := request["method"].(string)
			switch method {
			case "initialize":
				return fakeResponse{
					header: http.Header{"Mcp-Session-Id": []string{"session-1"}},
					body: map[string]any{
						"jsonrpc": "2.0",
						"id":      request["id"],
						"result": map[string]any{
							"protocolVersion": "2025-06-18",
						},
					},
				}
			case "notifications/initialized":
				if headers.Get("Mcp-Session-Id") != "session-1" {
					t.Fatalf("missing session id on initialized notification")
				}
				return fakeResponse{status: http.StatusAccepted}
			case "tools/list":
				if headers.Get("Mcp-Session-Id") != "session-1" {
					t.Fatalf("missing session id on tools/list")
				}
				return fakeResponse{
					body: map[string]any{
						"jsonrpc": "2.0",
						"id":      request["id"],
						"result": map[string]any{
							"tools": []map[string]any{
								{
									"name":        "info",
									"description": "Discover Arbiter.",
									"inputSchema": map[string]any{"type": "object"},
								},
							},
						},
					},
				}
			default:
				t.Fatalf("unexpected method: %s", method)
				return fakeResponse{}
			}
		},
	}

	client := mcp.NewClientWithHTTP("http://arbiter.test/mcp", &http.Client{Transport: transport})
	if err := client.Initialize(t.Context(), "arbiter-go", "test"); err != nil {
		t.Fatal(err)
	}
	tools, err := client.ListTools(t.Context())
	if err != nil {
		t.Fatal(err)
	}

	if len(tools) != 1 || tools[0].Name != "info" {
		t.Fatalf("unexpected tools: %#v", tools)
	}
	expected := []string{"initialize", "notifications/initialized", "tools/list"}
	if len(transport.methods) != len(expected) {
		t.Fatalf("unexpected method count: %#v", transport.methods)
	}
	for index, method := range expected {
		if transport.methods[index] != method {
			t.Fatalf("method[%d] = %q, want %q", index, transport.methods[index], method)
		}
	}
}

func TestCallToolReturnsStructuredContent(t *testing.T) {
	transport := fakeMCPTransport(t, func(request map[string]any) map[string]any {
		params, _ := request["params"].(map[string]any)
		if params["name"] != "info" {
			t.Fatalf("unexpected tool name: %#v", params["name"])
		}
		arguments, _ := params["arguments"].(map[string]any)
		if arguments["kind"] != "plugins" {
			t.Fatalf("unexpected arguments: %#v", arguments)
		}
		return map[string]any{
			"content": []map[string]any{{"type": "text", "text": "ok"}},
			"structuredContent": map[string]any{
				"kind":    "plugins",
				"plugins": []map[string]any{{"id": "smtp"}},
			},
		}
	})

	client := mcp.NewClientWithHTTP("http://arbiter.test/mcp", &http.Client{Transport: transport})
	if err := client.Initialize(t.Context(), "arbiter-go", "test"); err != nil {
		t.Fatal(err)
	}
	result, err := client.CallTool(t.Context(), "info", map[string]any{"kind": "plugins"})
	if err != nil {
		t.Fatal(err)
	}
	payload, ok := mcp.Payload(result).(map[string]any)
	if !ok {
		t.Fatalf("unexpected payload: %#v", mcp.Payload(result))
	}
	if payload["kind"] != "plugins" {
		t.Fatalf("unexpected kind: %#v", payload["kind"])
	}
}

func TestCallToolReturnsToolError(t *testing.T) {
	transport := fakeMCPTransport(t, func(request map[string]any) map[string]any {
		return map[string]any{
			"isError": true,
			"content": []map[string]any{
				{"type": "text", "text": "Error executing tool info: unknown info kind: tests"},
			},
		}
	})

	client := mcp.NewClientWithHTTP("http://arbiter.test/mcp", &http.Client{Transport: transport})
	if err := client.Initialize(t.Context(), "arbiter-go", "test"); err != nil {
		t.Fatal(err)
	}
	_, err := client.CallTool(t.Context(), "info", map[string]any{"kind": "tests"})

	if err == nil || err.Error() != "unknown info kind: tests" {
		t.Fatalf("unexpected error: %v", err)
	}
}

func TestClientSelectsMatchingSSEEventByRequestID(t *testing.T) {
	transport := &fakeTransport{
		t: t,
		handle: func(request map[string]any, headers http.Header) fakeResponse {
			method, _ := request["method"].(string)
			switch method {
			case "initialize":
				return fakeResponse{
					body: map[string]any{
						"jsonrpc": "2.0",
						"id":      request["id"],
						"result":  map[string]any{},
					},
				}
			case "notifications/initialized":
				return fakeResponse{status: http.StatusAccepted}
			case "tools/list":
				return fakeResponse{
					header: http.Header{"Content-Type": []string{"text/event-stream"}},
					rawBody: "event: message\n" +
						"data: {\"jsonrpc\":\"2.0\",\"method\":\"notifications/progress\",\"params\":{}}\n\n" +
						"event: message\n" +
						"data: {\"jsonrpc\":\"2.0\",\"id\":999,\"result\":{\"tools\":[]}}\n\n" +
						"event: message\n" +
						"data: {\"jsonrpc\":\"2.0\",\"id\":2,\"result\":{\"tools\":[{\"name\":\"info\"}]}}\n\n",
				}
			default:
				t.Fatalf("unexpected method: %s", method)
				return fakeResponse{}
			}
		},
	}

	client := mcp.NewClientWithHTTP("http://arbiter.test/mcp", &http.Client{Transport: transport})
	if err := client.Initialize(t.Context(), "arbiter-go", "test"); err != nil {
		t.Fatal(err)
	}
	tools, err := client.ListTools(t.Context())

	if err != nil {
		t.Fatal(err)
	}
	if len(tools) != 1 || tools[0].Name != "info" {
		t.Fatalf("unexpected tools: %#v", tools)
	}
}

type fakeResponse struct {
	status  int
	header  http.Header
	body    map[string]any
	rawBody string
}

type fakeTransport struct {
	t       *testing.T
	handle  func(map[string]any, http.Header) fakeResponse
	methods []string
}

func (f *fakeTransport) RoundTrip(r *http.Request) (*http.Response, error) {
	var request map[string]any
	if err := json.NewDecoder(r.Body).Decode(&request); err != nil {
		f.t.Fatal(err)
	}
	method, _ := request["method"].(string)
	f.methods = append(f.methods, method)
	response := f.handle(request, r.Header)
	status := response.status
	if status == 0 {
		status = http.StatusOK
	}
	body := []byte(response.rawBody)
	if response.body != nil {
		var err error
		body, err = json.Marshal(response.body)
		if err != nil {
			f.t.Fatal(err)
		}
	}
	header := response.header
	if header == nil {
		header = http.Header{}
	}
	if header.Get("Content-Type") == "" {
		header.Set("Content-Type", "application/json")
	}
	return &http.Response{
		StatusCode: status,
		Status:     http.StatusText(status),
		Header:     header,
		Body:       io.NopCloser(bytes.NewReader(body)),
		Request:    r,
	}, nil
}

func fakeMCPTransport(t *testing.T, call func(map[string]any) map[string]any) *fakeTransport {
	t.Helper()
	return &fakeTransport{
		t: t,
		handle: func(request map[string]any, headers http.Header) fakeResponse {
			method, _ := request["method"].(string)
			switch method {
			case "initialize":
				return fakeResponse{
					body: map[string]any{
						"jsonrpc": "2.0",
						"id":      request["id"],
						"result":  map[string]any{},
					},
				}
			case "notifications/initialized":
				return fakeResponse{status: http.StatusAccepted}
			case "tools/call":
				return fakeResponse{
					body: map[string]any{
						"jsonrpc": "2.0",
						"id":      request["id"],
						"result":  call(request),
					},
				}
			default:
				t.Fatalf("unexpected method: %s", method)
				return fakeResponse{}
			}
		},
	}
}
