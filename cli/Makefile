BINARY_NAME := ossie
BUILD_DIR   := dist

.PHONY: prepare build install test lint release-dry-run clean

# Copy the canonical schema from the repo root before any Go step that needs it.
prepare:
	cp ../core-spec/osi-schema.json internal/schema/osi-schema.json

build: prepare
	go build -o $(BUILD_DIR)/$(BINARY_NAME) .

install: prepare
	go build -o $(shell go env GOPATH)/bin/$(BINARY_NAME) .

test: prepare
	go test ./...

lint: prepare
	go vet ./...

release-dry-run:
	goreleaser release --snapshot --clean --config .goreleaser.yaml

clean:
	rm -rf $(BUILD_DIR)
