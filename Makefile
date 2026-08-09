# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 Polygraph contributors
#
# Makefile -- convenience entry points for this repo's judge-facing demo.
# Everything here is a thin wrapper around a script already committed to
# the repo; the Makefile exists so the whole demo is one command, on a
# fresh clone, with no setup. See docs/QUICKSTART.md for the full
# walkthrough and what to do if a step fails.

.DEFAULT_GOAL := help

.PHONY: help demo demo-clean test readiness-help

help:
	@echo "make demo        2-minute catch-a-liar demo -- no Arm hardware, no model download."
	@echo "                 Compiles examples/catch-a-liar/liar.c both ways and runs"
	@echo "                 tools/polygraph check against each. See docs/QUICKSTART.md."
	@echo "make demo-clean  remove examples/catch-a-liar/build/ (compiled demo binaries)."
	@echo "make test        run the stdlib-only unit suite and numeric-claims gate."
	@echo "make readiness-help  show the Arm server-readiness evidence harness options."

demo:
	./examples/catch-a-liar/demo.sh

demo-clean:
	rm -rf examples/catch-a-liar/build

test:
	python3 -m unittest discover -s tests -p 'test_*.py' -v
	python3 tools/check_claims.py

readiness-help:
	python3 tools/server_readiness.py --help
