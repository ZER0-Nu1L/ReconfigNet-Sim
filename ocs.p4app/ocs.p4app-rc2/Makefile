AGENT_IMAGE ?= reconfig-net/ocs-p4app-agent:rc2
TEST_IMAGE ?= reconfig-net/ocs-p4app-agent-test:rc2
PY311_BENCH_IMAGE ?= reconfig-net/ocs-benchmark-python:3.11
PROJECT_DIR := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
REPOSITORY_DIR := $(abspath $(PROJECT_DIR)/../..)
P4APP_RUNNER := $(abspath $(PROJECT_DIR)/../p4app-rc2/p4app)

.PHONY: run image test-image compile test test-container test-models \
	test-go test-go-race benchmark benchmark-protocols benchmark-gnmi-connection \
	benchmark-matrix benchmark-matrix-collect benchmark-go \
	benchmark-python311-image

run: image
	P4APP_IMAGE=$(AGENT_IMAGE) $(P4APP_RUNNER) run $(PROJECT_DIR)

image:
	docker build -f $(PROJECT_DIR)/Dockerfile.agent \
		-t $(AGENT_IMAGE) $(REPOSITORY_DIR)

test-image: image
	docker build --build-arg AGENT_IMAGE=$(AGENT_IMAGE) \
		-f $(PROJECT_DIR)/Dockerfile.test \
		-t $(TEST_IMAGE) $(REPOSITORY_DIR)

benchmark-python311-image:
	docker build -f $(PROJECT_DIR)/Dockerfile.benchmark-python311 \
		-t $(PY311_BENCH_IMAGE) $(REPOSITORY_DIR)

compile:
	$(P4APP_RUNNER) run $(PROJECT_DIR) compile

test: test-models
	python3 -c "import grpc, google.protobuf, yaml"
	PYTHONPATH="$(REPOSITORY_DIR)/ocs.agent:$(PROJECT_DIR)" \
		PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
		-s "$(REPOSITORY_DIR)/ocs.agent/tests" -v

test-go:
	docker run --rm \
		-v "$(REPOSITORY_DIR)/ocs.agent:/project" \
		-w /project/go-agent golang:1.25 \
		sh -c 'test -z "$$(gofmt -l cmd internal)" && go test ./...'

test-go-race:
	docker run --rm \
		-v "$(REPOSITORY_DIR)/ocs.agent:/project" \
		-w /project/go-agent golang:1.25 \
		go test -race ./...

test-container: test-image
	docker run --rm --entrypoint /bin/sh \
		-v "$(REPOSITORY_DIR):/workspace/ReconfigNet-Sim:ro" \
		-w /opt/ocs-agent \
		$(TEST_IMAGE) -c \
		'PYTHONPATH=/opt/ocs-agent:/workspace/ReconfigNet-Sim/ocs.p4app/ocs.p4app-rc2 PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s /opt/ocs-agent/tests -v'

test-models:
	pyang -p "$(REPOSITORY_DIR)/ocs.agent/models" \
		"$(REPOSITORY_DIR)/ocs.agent/models/openconfig-platform.yang" \
		"$(REPOSITORY_DIR)/ocs.agent/models/oc-optical-switch.yang" \
		"$(REPOSITORY_DIR)/ocs.agent/models/oc-optical-switch-connections.yang"

benchmark:
	python3 $(REPOSITORY_DIR)/ocs.agent/benchmarks/benchmark_batch.py \
		--target 127.0.0.1:9339

benchmark-protocols:
	python3 $(REPOSITORY_DIR)/ocs.agent/benchmarks/benchmark_protocols.py \
		--grpc-target 127.0.0.1:9339 \
		--http-target 127.0.0.1:5000

benchmark-gnmi-connection:
	python3 $(REPOSITORY_DIR)/ocs.agent/benchmarks/benchmark_gnmi_connection.py \
		--target 127.0.0.1:9339

benchmark-matrix:
	python3 $(REPOSITORY_DIR)/ocs.agent/benchmarks/benchmark_matrix.py \
		report $(ARGS)

benchmark-go:
	$(P4APP_RUNNER) exec /usr/local/bin/ocs-benchmark \
		--grpc-target 127.0.0.1:9339 --http-target 127.0.0.1:5000

benchmark-matrix-collect:
	$(P4APP_RUNNER) exec python3 \
		/opt/ocs-agent/benchmarks/benchmark_matrix.py collect \
		--profile $(PROFILE) --output /p4app/$(OUTPUT)
