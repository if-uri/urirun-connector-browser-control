.PHONY: help
help:
	@grep -E "^[a-zA-Z_-]+:.*?## .*$$" $(MAKEFILE_LIST) | awk "BEGIN{FS=\":.*?## \"}{printf \"  %-12s %s\\n\",\$$1,\$$2}"

.PHONY: test
test: ## Run connector tests
	python3 -m pytest -q

.PHONY: smoke
smoke: ## Run CLI smoke without opening a local browser
	urirun-browser-control open https://example.com/

.PHONY: manifest
manifest: ## Print connector manifest
	urirun-browser-control manifest

.PHONY: bindings
bindings: ## Print urirun v2 bindings
	urirun-browser-control bindings

.PHONY: docker-test
docker-test: ## Run connector in Docker against a fake browser endpoint plus MCP/A2A projection
	docker compose up --build --abort-on-container-exit --exit-code-from tester
	docker compose down -v --remove-orphans
