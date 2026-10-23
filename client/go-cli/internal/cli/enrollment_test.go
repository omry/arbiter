package cli

import (
	"bufio"
	"bytes"
	"encoding/json"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

// The control socket is a trusted synthetic operator, never a product endpoint.
func TestPythonEnrollmentCLI(t *testing.T) {
	address := os.Getenv("ARBITER_TEST_ENROLLMENT_CONTROL")
	if address == "" {
		t.Skip("Python test peer only")
	}
	control, err := net.DialTimeout("tcp", address, 5*time.Second)
	if err != nil {
		t.Fatal(err)
	}
	defer control.Close()
	control.SetDeadline(time.Now().Add(20 * time.Second))
	var start struct{ URL, Directory, Mode string }
	decoder := json.NewDecoder(control)
	if err := decoder.Decode(&start); err != nil {
		t.Fatal(err)
	}
	in, feed, _ := os.Pipe()
	output, out, _ := os.Pipe()
	defer in.Close()
	defer feed.Close()
	defer output.Close()
	defer out.Close()
	previous := openEnrollmentTerminal
	defer func() { openEnrollmentTerminal = previous }()
	openEnrollmentTerminal = func() (*os.File, *os.File, error) { return in, out, nil }
	finished := make(chan string, 1)
	go func() {
		reader := bufio.NewReader(output)
		selector, secret := "", ""
		for i := 0; i < 3; i++ {
			line, err := reader.ReadString('\n')
			if err != nil {
				finished <- ""
				return
			}
			if strings.HasPrefix(line, "Request: ") {
				selector = strings.TrimSpace(strings.TrimPrefix(line, "Request: "))
			}
			if strings.HasPrefix(line, "Secret ") {
				_, secret, _ = strings.Cut(line, "): ")
				secret = strings.TrimSpace(secret)
			}
		}
		json.NewEncoder(control).Encode(map[string]string{"selector": selector, "secret": secret})
		var approved bool
		if decoder.Decode(&approved) == nil && approved {
			fmt.Fprintln(feed)
		}
		finished <- secret
	}()
	var stdout, stderr bytes.Buffer
	code := Main([]string{"--config-dir", start.Directory, "enrol", "url=" + start.URL, "name=Fresh CLI"}, &stdout, &stderr,
		func(string) (string, bool) { return "", false }, func() (string, error) { return start.Directory, nil })
	// Closing the command's terminal output also releases the synthetic reader
	// when closed enrollment fails before a handoff.
	secret := <-finished
	if secret != "" && (strings.Contains(stdout.String(), secret) || strings.Contains(stderr.String(), secret)) {
		t.Fatal("secret reached output")
	}
	if start.Mode == "ok" {
		if code != 0 || !strings.Contains(stdout.String(), "Enrollment is not active") {
			t.Fatalf("code=%d stderr=%s", code, stderr.String())
		}
	} else if code != 1 {
		t.Fatalf("failure accepted: %d", code)
	}
	if start.Mode == "closed" && !strings.Contains(stderr.String(), "enrollment open") {
		t.Fatal("missing closed enrollment guidance")
	}
	entries, err := os.ReadDir(start.Directory + "/provisional")
	if err != nil {
		t.Fatal(err)
	}
	if len(entries) != 1 || entries[0].Name() != ".enrollment.lock" {
		t.Fatal("provisional material leaked")
	}
}

func TestEnrollmentRequiresTerminalAndRejectsSecretArguments(t *testing.T) {
	previous := openEnrollmentTerminal
	defer func() { openEnrollmentTerminal = previous }()
	openEnrollmentTerminal = func() (*os.File, *os.File, error) { return nil, nil, fmt.Errorf("no terminal") }
	for _, args := range [][]string{{"enrol", "url=https://127.0.0.1", "name=test"}, {"enrol", "psk=do-not-echo"}} {
		var out, err bytes.Buffer
		if Main(args, &out, &err, func(string) (string, bool) { return "", false }, func() (string, error) { return t.TempDir(), nil }) == 0 {
			t.Fatal("unsafe input accepted")
		}
		if strings.Contains(out.String()+err.String(), "do-not-echo") {
			t.Fatal("argument echoed")
		}
	}
}

func TestEnrollmentInvalidCustodyDoesNotAdvertise(t *testing.T) {
	var requests atomic.Int32
	server := httptest.NewTLSServer(http.HandlerFunc(func(http.ResponseWriter, *http.Request) {
		requests.Add(1)
	}))
	defer server.Close()
	directory := t.TempDir()
	if err := os.WriteFile(filepath.Join(directory, "provisional"), []byte("invalid custody root"), 0600); err != nil {
		t.Fatal(err)
	}
	previous := openEnrollmentTerminal
	defer func() { openEnrollmentTerminal = previous }()
	openEnrollmentTerminal = func() (*os.File, *os.File, error) {
		in, err := os.CreateTemp(directory, "input")
		if err != nil {
			return nil, nil, err
		}
		out, err := os.CreateTemp(directory, "output")
		if err != nil {
			in.Close()
		}
		return in, out, err
	}
	var stdout, stderr bytes.Buffer
	code := Main([]string{"--config-dir", directory, "enrol", "url=" + server.URL, "name=test"}, &stdout, &stderr,
		func(string) (string, bool) { return "", false }, func() (string, error) { return directory, nil })
	if code != 1 || requests.Load() != 0 || stdout.Len() != 0 {
		t.Fatalf("invalid custody advertised: code=%d requests=%d", code, requests.Load())
	}
}
