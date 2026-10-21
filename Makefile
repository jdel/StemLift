DOCKER_IMAGE ?= git.fkn.ninja/jdel/stemlift:$(shell date +%Y%m%d-%H%M)
HOST_TARGET := $(shell rustc --print host-tuple)
NODE_PLATFORM := $(shell node -p "process.platform + '-' + process.arch")
NODE_MODULES_STAMP := node_modules/.stemlift-$(NODE_PLATFORM)
SIDECAR_SUFFIX := $(if $(findstring windows,$(HOST_TARGET)),.exe,)
CLI := dist/stemlift$(SIDECAR_SUFFIX)
SIDECAR := tauri/binaries/stemlift-worker-$(HOST_TARGET)$(SIDECAR_SUFFIX)
FRONTEND := tauri/frontend-dist/index.html
FRONTEND_SOURCES := $(wildcard tauri/frontend/*) \
	package.json package-lock.json vite.config.js scripts/build-frontend.mjs
NATIVE_SOURCES := $(wildcard src/stemlift/*.py) \
	.python-version pyproject.toml uv.lock stemlift.spec \
	scripts/build-sidecar.mjs

.PHONY: build-linux build-macos build-windows clean cli-build dependencies docker-build frontend-build help sidecar-build test

dependencies: $(NODE_MODULES_STAMP)

frontend-build: $(FRONTEND)

sidecar-build: $(SIDECAR)

cli-build: $(CLI)

build-linux: $(NODE_MODULES_STAMP) $(FRONTEND) $(SIDECAR)
	npm run tauri -- build --bundles appimage,deb,rpm

build-macos: $(NODE_MODULES_STAMP) $(FRONTEND) $(SIDECAR)
	npm run tauri -- build --bundles app

build-windows: $(NODE_MODULES_STAMP) $(FRONTEND) $(SIDECAR)
	npm run tauri -- build --bundles nsis

$(NODE_MODULES_STAMP): package.json package-lock.json
	npm ci --include=optional
	node -e "require('node:fs').writeFileSync('$@', '')"

$(FRONTEND): $(FRONTEND_SOURCES) | $(NODE_MODULES_STAMP)
	npm run frontend:build

$(CLI): $(NATIVE_SOURCES)
	npm run sidecar:build

$(SIDECAR): $(CLI)
	node -e "const fs=require('node:fs'); fs.mkdirSync('tauri/binaries',{recursive:true}); fs.copyFileSync('$(CLI)','$(SIDECAR)'); if(process.platform!=='win32')fs.chmodSync('$(SIDECAR)',0o755)"

docker-build:
	docker build \
		--platform linux/amd64,linux/arm64 \
		--tag $(DOCKER_IMAGE) \
		.

test: $(NODE_MODULES_STAMP)
	uvx --from uv==0.13.0 uv run --locked --group dev ruff check .
	uvx --from uv==0.13.0 uv run --locked --group dev ruff format --check .
	uvx --from uv==0.13.0 uv run --locked python -m unittest discover -s tests
	npm audit --audit-level=high
	npm run frontend:test
	npm run frontend:build
	npm run sidecar:dev
	cargo fmt --manifest-path tauri/Cargo.toml -- --check
	cargo clippy --locked --manifest-path tauri/Cargo.toml -- -D warnings

clean:
	rm -rf build dist tauri/frontend-dist tauri/target
	rm -f node_modules/.stemlift-* tauri/binaries/stemlift-worker-*

help:
	@printf '%s\n' \
		'dependencies   Install locked npm dependencies when required' \
		'frontend-build Build the frontend when its inputs change' \
		'cli-build      Build the standalone CLI when its inputs change' \
		'sidecar-build  Freeze the Python worker when its inputs change' \
		'build-macos    Build the macOS app' \
		'build-windows  Build the Windows NSIS installer' \
		'build-linux    Build Linux AppImage, Debian, and RPM packages' \
		'docker-build   Build the AMD64/ARM64 Docker image' \
		'test           Run all static checks and tests' \
		'clean          Remove generated build artifacts'
