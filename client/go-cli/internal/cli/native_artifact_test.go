package cli

import (
	"bytes"
	"context"
	"io"
	"net/http"
	"strings"
	"testing"
)

type artifactRoundTripFunc func(*http.Request) (*http.Response, error)

func (fn artifactRoundTripFunc) RoundTrip(req *http.Request) (*http.Response, error) {
	return fn(req)
}

func TestWriteArtifactToStdoutStreamsTextArtifactWithFakeHTTPClient(t *testing.T) {
	var requests []string
	previous := newArtifactHTTPClient
	newArtifactHTTPClient = func() *http.Client {
		return &http.Client{
			Transport: artifactRoundTripFunc(func(req *http.Request) (*http.Response, error) {
				requests = append(requests, req.Method+" "+req.URL.Path)
				switch req.Method {
				case http.MethodHead:
					return artifactResponse(200, "text/plain", 12, ""), nil
				case http.MethodGet:
					return artifactResponse(200, "text/plain", 12, "hello world\n"), nil
				default:
					t.Fatalf("unexpected method: %s", req.Method)
				}
				return nil, nil
			}),
		}
	}
	t.Cleanup(func() {
		newArtifactHTTPClient = previous
	})

	var stdout bytes.Buffer
	err := writeArtifactToStdout(
		context.Background(),
		"http://arbiter.test/api/v1/artifacts/art-1/content?nonce=nonce",
		1024,
		&stdout,
	)

	if err != nil {
		t.Fatal(err)
	}
	if stdout.String() != "hello world\n" {
		t.Fatalf("unexpected stdout: %q", stdout.String())
	}
	if strings.Join(requests, "\n") != ""+
		"HEAD /api/v1/artifacts/art-1/content\n"+
		"GET /api/v1/artifacts/art-1/content" {
		t.Fatalf("unexpected requests:\n%s", strings.Join(requests, "\n"))
	}
}

func artifactResponse(
	statusCode int,
	contentType string,
	contentLength int64,
	body string,
) *http.Response {
	return &http.Response{
		StatusCode:    statusCode,
		Header:        http.Header{"Content-Type": []string{contentType}},
		ContentLength: contentLength,
		Body:          io.NopCloser(bytes.NewBufferString(body)),
	}
}
