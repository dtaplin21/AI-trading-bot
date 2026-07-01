.PHONY: test test-file pytest-version

PYTEST := .venv/bin/python -m pytest

test:
	$(PYTEST) $(PYTEST_ARGS)

test-file:
	@test -n "$(FILE)" || (echo "Usage: make test-file FILE=tests/unit/test_foo.py" && exit 1)
	$(PYTEST) $(FILE) -v

pytest-version:
	.venv/bin/python -m pytest --version
