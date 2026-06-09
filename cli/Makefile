BINARY_NAME := osi
BUILD_DIR   := dist

.PHONY: build test lint release-dry-run clean

build:
	go build -o $(BUILD_DIR)/$(BINARY_NAME) .

test:
	go test ./...

lint:
	go vet ./...

release-dry-run:
	goreleaser release --snapshot --clean --config .goreleaser.yaml

clean:
	rm -rf $(BUILD_DIR)
