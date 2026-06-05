package cli

//go:generate go run ../../cmd/versiongen ../../../core/pyproject.toml version_generated.go

const Version = coreVersion
