.DEFAULT_GOAL := help
.PHONY: help setup demo demo-build test lint typecheck check anon config profile app backfill status build \
        sync dq truth export refresh schedule-install schedule-uninstall schedule-status \
        scrape pdfs extract collect evals evals-replay label kaggle-export kaggle-import \
        docker docker-up vllm-up smoke-mistral smoke-local

PROFILE ?= $(or $(LICITA_PROFILE),example)
BUYER ?=
HOUR ?= 6                                  # local hour of the daily `licita sync` (schedule-install)
ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
UV := $(shell command -v uv)
AGENT := $(HOME)/Library/LaunchAgents/com.licita.sync.plist
MODELS ?= mistral-small-latest,mistral-medium-latest,mistral-large-latest
export LICITA_PROFILE := $(PROFILE)

help:             ## List targets (every target is a thin alias for a `licita` command)
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'
	@echo "\n  PROFILE=$(PROFILE)  (override: make app PROFILE=mine BUYER=KEY)"

# ------------------------------------------------------------------ first run
setup:            ## Install dependencies and git hooks (uv required)
	uv sync --all-extras
	git config core.hooksPath .githooks

demo:             ## Synthetic data through the real pipeline, then the app. No key, no download
	uv run licita demo app

demo-build:       ## Rebuild the synthetic demo data only
	LICITA_DEMO= uv run licita demo build

config:           ## Effective profile, provider, models, paths (never prints keys)
	uv run licita config

profile:          ## Show the active deployment profile
	uv run licita profile

app:              ## App for one buyer of the profile, e.g. make app PROFILE=mine BUYER=KEY
	uv run licita app $(BUYER)

# ------------------------------------------------------------------ quality
test:             ## Offline tests: no network, no credentials (<10 s)
	uv run pytest

lint:             ## Ruff
	uv run ruff check src tests kaggle

typecheck:        ## Pyright on the typed core (llm, profile, evals, errors)
	uv run pyright

anon:             ## Fail if the tree names any buyer, region or tribunal
	uv run licita anon check

evals-replay:     ## Offline evaluation on cached synthetic responses (what CI runs)
	uv run licita evals replay tests/fixtures/evals

check: lint typecheck anon test evals-replay   ## Everything CI runs

# ------------------------------------------------------------------ data pipeline
backfill:         ## Download + parse PLACSP feeds 643 and 1044, 2017-present (~10 GB, resumable)
	uv run licita ingest

status:           ## Backfill progress per archive
	uv run licita status

build:            ## Build a new versioned spine and swap it in atomically
	uv run licita build

sync:             ## Fetch changed PLACSP archives (closed months), rebuild if needed, refresh fact sheets
	uv run licita sync --facts

dq:               ## Data-quality report on the live spine
	uv run licita dq

truth:            ## Validate the team's ground truth (data/truth/*.csv) and show what it changes on the radar
	uv run licita truth validate && uv run licita truth import

export:           ## OCDS 1.1 release of the health slice since 2022 + datasheet (out/ocds/)
	uv run licita export --since 2022-01-01 --all

refresh:          ## Bring everything to today: sync (+ current month) -> rulings -> fact sheets -> documents
	uv run licita sync --current-month --facts
	uv run licita scrape --years $$(date +%Y) $$(date +%Y)
	uv run licita pdfs
	for b in $$(uv run licita profile buyers); do uv run licita documents --buyer $$b; done

schedule-install: ## Run `licita sync --facts` daily at HOUR:30 via launchd (macOS); PROFILE and HOUR apply
	@test -n "$(UV)" || { echo "uv not found on PATH"; exit 1; }
	mkdir -p $(ROOT)/runs/sync $(dir $(AGENT))
	sed -e 's|@ROOT@|$(ROOT)|g' -e 's|@UV@|$(UV)|g' -e 's|@PROFILE@|$(PROFILE)|g' -e 's|@HOUR@|$(HOUR)|g' \
	    -e 's|@HOME@|$(HOME)|g' ops/launchd/com.licita.sync.plist.in > $(AGENT)
	-launchctl bootout gui/$$(id -u)/com.licita.sync 2>/dev/null
	launchctl bootstrap gui/$$(id -u) $(AGENT)
	@echo "installed $(AGENT): daily at $(HOUR):30, profile $(PROFILE); logs in runs/sync/"

schedule-uninstall: ## Remove the launchd job
	-launchctl bootout gui/$$(id -u)/com.licita.sync
	rm -f $(AGENT)

schedule-status:  ## Is the launchd job loaded, and when did sync last run
	@launchctl print gui/$$(id -u)/com.licita.sync 2>/dev/null | grep -E 'state|last exit|program' || echo "not installed"
	@tail -n 3 data/state/sync_log.jsonl 2>/dev/null || echo "no sync has run yet"

# ------------------------------------------------------------------ rulings and Mistral
scrape:           ## Scrape the profile's tribunal rulings
	uv run licita scrape

pdfs:             ## Ruling PDFs -> text (add OCR=1 to OCR scanned PDFs with Mistral)
	uv run licita pdfs $(if $(OCR),--ocr,)

extract:          ## Submit ruling extraction to the Mistral Batch API (needs MISTRAL_API_KEY)
	uv run licita extract submit

collect:          ## Collect finished Mistral batches (resumable)
	uv run licita extract collect

label:            ## Sample 40 rulings for hand labelling -> private/golden/to_label.jsonl
	uv run licita label sample

evals:            ## Live evaluation on the private golden set (costs money), e.g. make evals MODELS=a,b
	uv run licita evals live --models $(MODELS)

kaggle-export:    ## Open-model baseline: dataset + notebook for Kaggle GPUs
	uv run licita baseline export

kaggle-import:    ## Load Kaggle results, e.g. make kaggle-import RESULTS=~/Downloads/results.jsonl
	uv run licita baseline import $(RESULTS)

# ------------------------------------------------------------------ containers
docker:           ## Build the image
	docker build -t licita:local .

docker-up:        ## App on http://localhost:8501 with the demo data (hosted Mistral if MISTRAL_API_KEY is set)
	docker compose up app

vllm-up:          ## Self-hosted Mistral Small on a local GPU (OpenAI-compatible, port 8000)
	docker compose --profile vllm up vllm

smoke-mistral:    ## One live extraction call to hosted Mistral (do this first after setting the key)
	uv run licita extract submit --limit 1 --sync && uv run licita extract status

smoke-local:      ## One extraction call against the local server (LICITA_LLM_BASE_URL)
	LICITA_LLM_PROVIDER=local uv run licita extract submit --limit 1 --sync
