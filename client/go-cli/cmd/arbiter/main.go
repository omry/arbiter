package main

import (
	"os"

	"github.com/omry/arbiter/client/go-cli/internal/cli"
)

func main() {
	os.Exit(cli.Main(os.Args[1:], os.Stdout, os.Stderr, os.LookupEnv, os.UserHomeDir))
}
