package cli

import (
	"bufio"
	"bytes"
	"context"
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

	"github.com/omry/arbiter/client/go-cli/internal/identity"
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
	if start.Mode == "resume" {
		entries, err := os.ReadDir(filepath.Join(start.Directory, "prepared"))
		if err != nil {
			t.Fatal(err)
		}
		for _, entry := range entries {
			if !strings.HasSuffix(entry.Name(), ".json") {
				continue
			}
			selector := strings.TrimSuffix(entry.Name(), ".json")
			var out, diagnostics bytes.Buffer
			if Main([]string{"--config-dir", start.Directory, "arbiter.connect_timeout=15s", "enrol", "resume=" + selector}, &out, &diagnostics,
				func(string) (string, bool) { return "", false }, func() (string, error) { return start.Directory, nil }) != 0 {
				t.Fatal(diagnostics.String())
			}
			if _, err := os.Stat(filepath.Join(start.Directory, "enrolled", entry.Name())); err != nil {
				t.Fatal(err)
			}
			return
		}
		t.Fatal("missing recoverable prepared state")
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
	args := []string{"--config-dir", start.Directory}
	if start.Mode == "ok" {
		args = append(args, "arbiter.connect_timeout=15s")
	}
	if start.Mode == "lost-reply" || start.Mode == "delayed-reply" {
		args = append(args, "arbiter.connect_timeout=10s")
	}
	args = append(args, "enrol", "url="+start.URL, "name=Fresh CLI")
	code := Main(args, &stdout, &stderr,
		func(string) (string, bool) { return "", false }, func() (string, error) { return start.Directory, nil })
	// Closing the command's terminal output also releases the synthetic reader
	// when closed enrollment fails before a handoff.
	secret := <-finished
	if secret != "" && (strings.Contains(stdout.String(), secret) || strings.Contains(stderr.String(), secret)) {
		t.Fatal("secret reached output")
	}
	if start.Mode == "ok" || start.Mode == "lost-reply" || start.Mode == "delayed-reply" {
		if code != 0 || !strings.Contains(stdout.String(), "Enrollment activated and saved") {
			t.Fatalf("code=%d stderr=%s", code, stderr.String())
		}
		entries, err := os.ReadDir(filepath.Join(start.Directory, "enrolled"))
		if err != nil {
			t.Fatal(err)
		}
		count := 0
		for _, entry := range entries {
			if !strings.HasSuffix(entry.Name(), ".json") {
				continue
			}
			selector := strings.TrimSuffix(entry.Name(), ".json")
			if start.Mode == "lost-reply" || start.Mode == "delayed-reply" {
				receipt, reconcileErr := identity.ReconcileEnrollment(context.Background(), filepath.Join(start.Directory, "enrolled"), selector, 10*time.Second)
				if reconcileErr == nil {
					reconcileErr = receipt.Promote(context.Background(), filepath.Join(start.Directory, "enrolled"))
				}
				if reconcileErr != nil {
					t.Fatal("expired enrolled receipt did not survive reload", reconcileErr)
				}
			} else {
				material, prepared, err := identity.LoadPrepared(filepath.Join(start.Directory, "enrolled"), selector, time.Now().UTC())
				if err != nil || material == nil || prepared == nil {
					t.Fatal("enrolled private result did not survive reload", err)
				}
			}
			path := filepath.Join(start.Directory, "enrolled", entry.Name())
			original, err := os.ReadFile(path)
			if err != nil {
				t.Fatal(err)
			}
			for _, changed := range [][]byte{
				append(append([]byte{}, original[:len(original)-1]...), []byte(`,"version":1}`)...),
				append(append([]byte{}, original[:len(original)-1]...), []byte(`,"unexpected":true}`)...),
				[]byte(strings.Replace(string(original), `"NEVER SHARE THIS:`, `"MODIFIED:`, 1)),
				append(append([]byte{}, original...), []byte(`{}`)...),
			} {
				if err := os.WriteFile(path, changed, 0600); err != nil {
					t.Fatal(err)
				}
				var loadErr error
				if start.Mode == "lost-reply" || start.Mode == "delayed-reply" {
					_, loadErr = identity.ReconcileEnrollment(context.Background(), filepath.Join(start.Directory, "enrolled"), selector, 10*time.Second)
				} else {
					_, _, loadErr = identity.LoadPrepared(filepath.Join(start.Directory, "enrolled"), selector, time.Now().UTC())
				}
				if loadErr == nil {
					t.Fatal("corrupted prepared record accepted")
				}
			}
			if err := os.WriteFile(path, original, 0600); err != nil {
				t.Fatal(err)
			}
			count++
			// Explicit recovery after cleanup authenticates against enrolled
			// custody and never creates another activation or handoff.
			var resumed, resumeErr bytes.Buffer
			if Main([]string{"--config-dir", start.Directory, "arbiter.connect_timeout=15s", "enrol", "resume=" + selector}, &resumed, &resumeErr,
				func(string) (string, bool) { return "", false }, func() (string, error) { return start.Directory, nil }) != 0 {
				t.Fatal("resume after promotion", resumeErr.String())
			}
		}
		if count != 1 {
			t.Fatal("expected one prepared result", count)
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
