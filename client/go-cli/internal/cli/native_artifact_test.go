package cli

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"io"
	"math/big"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
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
		newArtifactHTTPClient(),
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

func TestArtifactHTTPClientUsesConfiguredTLSCAFile(t *testing.T) {
	home := t.TempDir()
	caPath := filepath.Join(home, "arbiter.crt")
	writeTestCertificatePEM(t, caPath)
	configDir := filepath.Join(home, DefaultConfigDir)
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatal(err)
	}
	configPath := filepath.Join(configDir, DefaultClientConfigName)
	if err := os.WriteFile(
		configPath,
		[]byte("arbiter:\n  tls_ca_file: "+caPath+"\n"),
		0o644,
	); err != nil {
		t.Fatal(err)
	}

	httpClient, err := artifactHTTPClient(
		defaultGlobalOptions(),
		func(string) (string, bool) { return "", false },
		func() (string, error) { return home, nil },
		"https://example.com/artifact",
	)

	if err != nil {
		t.Fatal(err)
	}
	transport, ok := httpClient.Transport.(*http.Transport)
	if !ok {
		t.Fatalf("expected HTTP transport, got %T", httpClient.Transport)
	}
	if transport.TLSClientConfig == nil {
		t.Fatal("expected TLS client config")
	}
	if transport.TLSClientConfig.InsecureSkipVerify {
		t.Fatal("expected configured CA file to keep certificate verification enabled")
	}
	if transport.TLSClientConfig.RootCAs == nil {
		t.Fatal("expected configured CA file to populate RootCAs")
	}
}

func TestAllowLocalSelfSignedTLSOnlyForLoopbackHTTPS(t *testing.T) {
	tests := []struct {
		name     string
		rawURL   string
		expected bool
	}{
		{name: "localhost", rawURL: "https://localhost:8075", expected: true},
		{name: "localhost with dot", rawURL: "https://localhost.:8075", expected: true},
		{name: "ipv4 loopback", rawURL: "https://127.0.0.1:8075", expected: true},
		{name: "ipv6 loopback", rawURL: "https://[::1]:8075", expected: true},
		{name: "remote https", rawURL: "https://arbiter.example.test", expected: false},
		{name: "local http", rawURL: "http://127.0.0.1:8075", expected: false},
		{name: "invalid", rawURL: "://not a url", expected: false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			if got := allowLocalSelfSignedTLS(tt.rawURL); got != tt.expected {
				t.Fatalf("expected %v for %q, got %v", tt.expected, tt.rawURL, got)
			}
		})
	}
}

func TestArtifactHTTPClientVerifiesRemoteHTTPSByDefault(t *testing.T) {
	httpClient, err := artifactHTTPClient(
		defaultGlobalOptions(),
		func(string) (string, bool) { return "", false },
		func() (string, error) { return t.TempDir(), nil },
		"https://arbiter.example.test/artifact",
	)

	if err != nil {
		t.Fatal(err)
	}
	if httpClient.Transport != nil {
		transport, ok := httpClient.Transport.(*http.Transport)
		if !ok {
			t.Fatalf("expected HTTP transport, got %T", httpClient.Transport)
		}
		if transport.TLSClientConfig != nil && transport.TLSClientConfig.InsecureSkipVerify {
			t.Fatal("remote HTTPS artifact client must not disable certificate verification")
		}
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

func writeTestCertificatePEM(t *testing.T, path string) {
	t.Helper()
	key, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	template := x509.Certificate{
		SerialNumber: big.NewInt(1),
		Subject: pkix.Name{
			CommonName: "arbiter.test",
		},
		NotBefore: time.Now().Add(-time.Hour),
		NotAfter:  time.Now().Add(time.Hour),
	}
	certDER, err := x509.CreateCertificate(
		rand.Reader,
		&template,
		&template,
		&key.PublicKey,
		key,
	)
	if err != nil {
		t.Fatal(err)
	}
	certPEM := pem.EncodeToMemory(&pem.Block{
		Type:  "CERTIFICATE",
		Bytes: certDER,
	})
	if err := os.WriteFile(path, certPEM, 0o644); err != nil {
		t.Fatal(err)
	}
}
