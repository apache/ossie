BINARY_NAME := ossie
BUILD_DIR   := dist

.PHONY: build test lint format release-dry-run clean

build:
	go build -o $(BUILD_DIR)/$(BINARY_NAME) .

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
