package mcp

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

const protocolVersion = "2025-06-18"

type Client struct {
	url       string
	http      *http.Client
	nextID    int
	sessionID string
}

type Tool struct {
	Name        string         `json:"name"`
	Description string         `json:"description,omitempty"`
	InputSchema map[string]any `json:"inputSchema,omitempty"`
}

type ToolCallResult struct {
	Content           []map[string]any `json:"content,omitempty"`
	StructuredContent any              `json:"structuredContent,omitempty"`
	IsError           bool             `json:"isError,omitempty"`
	Raw               map[string]any   `json:"-"`
}

type rpcRequest struct {
	JSONRPC string `json:"jsonrpc"`
	ID      int    `json:"id,omitempty"`
	Method  string `json:"method"`
	Params  any    `json:"params,omitempty"`
}

type rpcResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      int             `json:"id,omitempty"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   *rpcError       `json:"error,omitempty"`
}

type rpcError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
	Data    any    `json:"data,omitempty"`
}

func NewClient(url string) *Client {
	return &Client{
		url:    url,
		http:   &http.Client{Timeout: 30 * time.Second},
		nextID: 1,
	}
}

func NewClientWithHTTP(url string, httpClient *http.Client) *Client {
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 30 * time.Second}
	}
	return &Client{
		url:    url,
		http:   httpClient,
		nextID: 1,
	}
}

func (c *Client) Initialize(ctx context.Context, clientName string, clientVersion string) error {
	params := map[string]any{
		"protocolVersion": protocolVersion,
		"capabilities":    map[string]any{},
		"clientInfo": map[string]any{
			"name":    clientName,
			"version": clientVersion,
		},
	}
	if _, err := c.request(ctx, "initialize", params); err != nil {
		return err
	}
	if err := c.notify(ctx, "notifications/initialized", map[string]any{}); err != nil {
		return err
	}
	return nil
}

func (c *Client) ListTools(ctx context.Context) ([]Tool, error) {
	result, err := c.request(ctx, "tools/list", map[string]any{})
	if err != nil {
		return nil, err
	}
	var payload struct {
		Tools []Tool `json:"tools"`
	}
	if err := json.Unmarshal(result, &payload); err != nil {
		return nil, fmt.Errorf("decode tools/list result: %w", err)
	}
	return payload.Tools, nil
}

func (c *Client) CallTool(ctx context.Context, name string, arguments map[string]any) (ToolCallResult, error) {
	params := map[string]any{
		"name":      name,
		"arguments": arguments,
	}
	result, err := c.request(ctx, "tools/call", params)
	if err != nil {
		return ToolCallResult{}, err
	}
	var payload ToolCallResult
	if err := json.Unmarshal(result, &payload); err != nil {
		return ToolCallResult{}, fmt.Errorf("decode tools/call result: %w", err)
	}
	_ = json.Unmarshal(result, &payload.Raw)
	if payload.IsError {
		return payload, errors.New(toolErrorMessage(payload))
	}
	return payload, nil
}

func Payload(result ToolCallResult) any {
	if result.StructuredContent != nil {
		return result.StructuredContent
	}
	return result
}

func (c *Client) request(ctx context.Context, method string, params any) (json.RawMessage, error) {
	id := c.nextID
	c.nextID++
	response, err := c.post(ctx, rpcRequest{
		JSONRPC: "2.0",
		ID:      id,
		Method:  method,
		Params:  params,
	}, id)
	if err != nil {
		return nil, err
	}
	if response.Error != nil {
		return nil, fmt.Errorf("MCP %s failed: %s", method, response.Error.Message)
	}
	return response.Result, nil
}

func (c *Client) notify(ctx context.Context, method string, params any) error {
	_, err := c.post(ctx, map[string]any{
		"jsonrpc": "2.0",
		"method":  method,
		"params":  params,
	}, 0)
	return err
}

func (c *Client) post(ctx context.Context, payload any, expectedID int) (rpcResponse, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return rpcResponse{}, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.url, bytes.NewReader(body))
	if err != nil {
		return rpcResponse{}, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Accept", "application/json, text/event-stream")
	if c.sessionID != "" {
		req.Header.Set("Mcp-Session-Id", c.sessionID)
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return rpcResponse{}, err
	}
	defer resp.Body.Close()

	if sessionID := resp.Header.Get("Mcp-Session-Id"); sessionID != "" {
		c.sessionID = sessionID
	}
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return rpcResponse{}, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		message := strings.TrimSpace(string(data))
		if message == "" {
			message = resp.Status
		}
		return rpcResponse{}, fmt.Errorf("MCP HTTP %d: %s", resp.StatusCode, message)
	}
	if len(bytes.TrimSpace(data)) == 0 {
		if expectedID != 0 {
			return rpcResponse{}, fmt.Errorf("MCP response missing body for request id %d", expectedID)
		}
		return rpcResponse{}, nil
	}
	return decodeRPCResponse(data, resp.Header.Get("Content-Type"), expectedID)
}

func decodeRPCResponse(data []byte, contentType string, expectedID int) (rpcResponse, error) {
	if strings.Contains(contentType, "text/event-stream") || bytes.HasPrefix(bytes.TrimSpace(data), []byte("event:")) || bytes.HasPrefix(bytes.TrimSpace(data), []byte("data:")) {
		return decodeSSERPCResponse(data, expectedID)
	}
	if len(bytes.TrimSpace(data)) == 0 {
		return rpcResponse{}, nil
	}
	var response rpcResponse
	if err := json.Unmarshal(data, &response); err != nil {
		return rpcResponse{}, fmt.Errorf("decode MCP response: %w", err)
	}
	if expectedID != 0 && response.ID != expectedID {
		return rpcResponse{}, fmt.Errorf("MCP response id %d did not match request id %d", response.ID, expectedID)
	}
	return response, nil
}

func decodeSSERPCResponse(data []byte, expectedID int) (rpcResponse, error) {
	events := extractSSEEvents(data)
	for _, eventData := range events {
		var response rpcResponse
		if err := json.Unmarshal([]byte(eventData), &response); err != nil {
			return rpcResponse{}, fmt.Errorf("decode MCP SSE event: %w", err)
		}
		if expectedID == 0 || response.ID == expectedID {
			return response, nil
		}
	}
	if expectedID != 0 {
		return rpcResponse{}, fmt.Errorf("MCP SSE response missing request id %d", expectedID)
	}
	return rpcResponse{}, nil
}

func extractSSEEvents(data []byte) []string {
	var events []string
	var eventLines []string
	scanner := bufio.NewScanner(bytes.NewReader(data))
	for scanner.Scan() {
		line := strings.TrimSuffix(scanner.Text(), "\r")
		if line == "" {
			if len(eventLines) > 0 {
				events = append(events, strings.Join(eventLines, "\n"))
				eventLines = nil
			}
			continue
		}
		if strings.HasPrefix(line, "data:") {
			value := strings.TrimPrefix(line, "data:")
			value = strings.TrimPrefix(value, " ")
			eventLines = append(eventLines, value)
		}
	}
	if len(eventLines) > 0 {
		events = append(events, strings.Join(eventLines, "\n"))
	}
	return events
}

func toolErrorMessage(result ToolCallResult) string {
	for _, item := range result.Content {
		text, ok := item["text"].(string)
		if ok && text != "" {
			const prefix = "Error executing tool "
			if strings.HasPrefix(text, prefix) {
				if _, message, ok := strings.Cut(text, ": "); ok {
					return message
				}
			}
			return text
		}
	}
	return "tool call failed"
}
