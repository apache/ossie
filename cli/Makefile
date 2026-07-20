BINARY_NAME := ossie
BUILD_DIR   := dist

.PHONY: build install test lint format release-dry-run clean

build:
	go build -o $(BUILD_DIR)/$(BINARY_NAME) .

install:
	go build -o $(shell go env GOPATH)/bin/$(BINARY_NAME) .

test:
	go test ./...

lint:
	go vet ./...

format:
	gofmt -w .

release-dry-run:
	goreleaser release --snapshot --clean --config .goreleaser.yaml

clean:
	rm -rf $(BUILD_DIR)
