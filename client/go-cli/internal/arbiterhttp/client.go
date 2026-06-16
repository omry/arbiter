package arbiterhttp

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"time"
)

const apiPrefix = "/api/v1"

type Client struct {
	baseURL string
	http    *http.Client
}

type APIError struct {
	StatusCode int
	Code       string
	Message    string
}

func (err APIError) Error() string {
	if err.Code == "" {
		return fmt.Sprintf("HTTP %d: %s", err.StatusCode, err.Message)
	}
	return fmt.Sprintf("HTTP %d %s: %s", err.StatusCode, err.Code, err.Message)
}

func NewClient(baseURL string) *Client {
	return NewClientWithHTTP(baseURL, nil)
}

func NewClientWithHTTP(baseURL string, httpClient *http.Client) *Client {
	if httpClient == nil {
		httpClient = &http.Client{Timeout: 30 * time.Second}
	}
	return &Client{
		baseURL: strings.TrimRight(baseURL, "/"),
		http:    httpClient,
	}
}

func (c *Client) Info(ctx context.Context) (map[string]any, error) {
	return c.get(ctx, apiPrefix+"/info")
}

func (c *Client) Plugins(ctx context.Context) (map[string]any, error) {
	return c.get(ctx, apiPrefix+"/plugins")
}

func (c *Client) PluginOperations(ctx context.Context, plugin string) (map[string]any, error) {
	return c.get(ctx, apiPrefix+"/plugins/"+plugin+"/operations")
}

func (c *Client) OperationDetails(ctx context.Context, operationID string) (map[string]any, error) {
	return c.get(ctx, apiPrefix+"/operations/"+operationID)
}

func (c *Client) RunOperation(ctx context.Context, operationID string, args map[string]any) (map[string]any, error) {
	return c.post(ctx, apiPrefix+"/operations/"+operationID, map[string]any{"args": args})
}

func (c *Client) get(ctx context.Context, path string) (map[string]any, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, c.baseURL+path, nil)
	if err != nil {
		return nil, err
	}
	return c.do(req)
}

func (c *Client) post(ctx context.Context, path string, payload any) (map[string]any, error) {
	body, err := json.Marshal(payload)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.baseURL+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	return c.do(req)
}

func (c *Client) do(req *http.Request) (map[string]any, error) {
	req.Header.Set("Accept", "application/json")
	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	data, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, decodeError(resp.StatusCode, data)
	}
	var payload map[string]any
	if err := json.Unmarshal(data, &payload); err != nil {
		return nil, fmt.Errorf("decode Arbiter response: %w", err)
	}
	return payload, nil
}

func decodeError(statusCode int, data []byte) error {
	var payload struct {
		Error struct {
			Code    string `json:"code"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if err := json.Unmarshal(data, &payload); err == nil && payload.Error.Message != "" {
		return APIError{
			StatusCode: statusCode,
			Code:       payload.Error.Code,
			Message:    payload.Error.Message,
		}
	}
	message := strings.TrimSpace(string(data))
	if message == "" {
		message = http.StatusText(statusCode)
	}
	return APIError{StatusCode: statusCode, Message: message}
}
