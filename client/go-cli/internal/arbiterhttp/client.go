package arbiterhttp

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
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
	Fields     map[string]any
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

func NewClientInsecureTLS(baseURL string) *Client {
	return NewClientWithHTTP(baseURL, NewHTTPClientInsecureTLS())
}

func NewHTTPClientInsecureTLS() *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true} //nolint:gosec
	client := &http.Client{
		Transport: transport,
	}
	ConfigureTimeouts(client, DefaultConnectTimeout, DefaultTimeout, DefaultDownloadIdleTimeout)
	return client
}

func NewClientWithTLSCAFile(baseURL string, caFile string) (*Client, error) {
	httpClient, err := NewHTTPClientWithTLSCAFile(caFile)
	if err != nil {
		return nil, err
	}
	return NewClientWithHTTP(baseURL, httpClient), nil
}

func NewHTTPClientWithTLSCAFile(caFile string) (*http.Client, error) {
	caBytes, err := os.ReadFile(caFile)
	if err != nil {
		return nil, fmt.Errorf("read Arbiter TLS CA file: %w", err)
	}
	roots, err := x509.SystemCertPool()
	if err != nil {
		roots = x509.NewCertPool()
	}
	if !roots.AppendCertsFromPEM(caBytes) {
		return nil, fmt.Errorf("Arbiter TLS CA file contains no PEM certificates: %s", caFile)
	}
	transport := http.DefaultTransport.(*http.Transport).Clone()
	transport.TLSClientConfig = &tls.Config{RootCAs: roots}
	client := &http.Client{
		Transport: transport,
	}
	ConfigureTimeouts(client, DefaultConnectTimeout, DefaultTimeout, DefaultDownloadIdleTimeout)
	return client, nil
}

func NewClientWithHTTP(baseURL string, httpClient *http.Client) *Client {
	if httpClient == nil {
		httpClient = &http.Client{}
		ConfigureTimeouts(httpClient, DefaultConnectTimeout, DefaultTimeout, DefaultDownloadIdleTimeout)
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

func (c *Client) PluginDetails(ctx context.Context, plugin string) (map[string]any, error) {
	return c.get(ctx, apiPrefix+"/plugins/"+url.PathEscape(plugin))
}

func (c *Client) PluginAccounts(ctx context.Context, plugin string) (map[string]any, error) {
	return c.get(ctx, apiPrefix+"/plugins/"+url.PathEscape(plugin)+"/accounts")
}

func (c *Client) PluginAccount(ctx context.Context, plugin string, account string) (map[string]any, error) {
	return c.get(ctx, apiPrefix+"/plugins/"+url.PathEscape(plugin)+"/accounts/"+url.PathEscape(account))
}

func (c *Client) PluginPolicy(ctx context.Context, plugin string, policy string) (map[string]any, error) {
	return c.get(ctx, apiPrefix+"/plugins/"+url.PathEscape(plugin)+"/policies/"+url.PathEscape(policy))
}

func (c *Client) PluginOperations(ctx context.Context, plugin string) (map[string]any, error) {
	return c.get(ctx, apiPrefix+"/plugins/"+url.PathEscape(plugin)+"/operations")
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
	requestCtx, requestState := newAPIRequestContext(req.Context())
	defer requestState.close()
	resp, err := c.http.Do(req.WithContext(requestCtx))
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
		Error map[string]any `json:"error"`
	}
	decoder := json.NewDecoder(bytes.NewReader(data))
	decoder.UseNumber()
	if err := decoder.Decode(&payload); err == nil {
		var trailing any
		if decoder.Decode(&trailing) != io.EOF {
			return APIError{StatusCode: statusCode, Code: "invalid_response", Message: "Arbiter returned an invalid error response."}
		}
		message, hasMessage := payload.Error["message"].(string)
		code, _ := payload.Error["code"].(string)
		if hasMessage && code != "" {
			return APIError{
				StatusCode: statusCode,
				Code:       code,
				Message:    message,
				Fields:     payload.Error,
			}
		}
	}
	return APIError{StatusCode: statusCode, Code: "invalid_response", Message: "Arbiter returned an invalid error response."}
}

// ErrorResponse retains public server error fields with a bounded read. HEAD
// failures have no body, so retain their status without claiming a domain code.
func ErrorResponse(resp *http.Response) error {
	data, err := io.ReadAll(io.LimitReader(resp.Body, 64*1024+1))
	if err != nil || len(data) > 64*1024 {
		return APIError{StatusCode: resp.StatusCode, Code: "invalid_response", Message: "Arbiter returned an invalid error response."}
	}
	if len(data) == 0 {
		return APIError{StatusCode: resp.StatusCode, Code: "http_error", Message: "Arbiter rejected the artifact request."}
	}
	return decodeError(resp.StatusCode, data)
}
