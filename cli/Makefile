#  Licensed to the Apache Software Foundation (ASF) under one
#  or more contributor license agreements.  See the NOTICE file
#  distributed with this work for additional information
#  regarding copyright ownership.  The ASF licenses this file
#  to you under the Apache License, Version 2.0 (the
#  "License"); you may not use this file except in compliance
#  with the License.  You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing,
#  software distributed under the License is distributed on an
#  "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
#  KIND, either express or implied.  See the License for the
#  specific language governing permissions and limitations
#  under the License.

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
