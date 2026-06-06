package cli

//go:generate go run ../../cmd/versiongen ../../../../server/pyproject.toml version_generated.go

const Version = serverVersion
