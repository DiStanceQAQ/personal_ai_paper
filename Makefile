.PHONY: dev test typecheck install check frontend-install frontend-dev frontend-build tauri-dev build-sidecars tauri-build package-macos

install:
	pip install -e ".[dev]"

dev:
	uvicorn main:app --reload --host 127.0.0.1 --port 8000

test:
	pytest -v

typecheck:
	mypy main.py api_sidecar.py tests/

check: typecheck test
	@echo "All checks passed!"

frontend-install:
	npm --prefix frontend install

frontend-dev:
	npm --prefix frontend run dev

frontend-build:
	npm --prefix frontend run build

tauri-dev:
	npm run tauri dev

build-sidecars:
	python scripts/build_sidecars.py --target all

tauri-build: frontend-build build-sidecars
	npm run tauri build

package-macos: tauri-build
	@echo "DMG output is under src-tauri/target/release/bundle/dmg"
