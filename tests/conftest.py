"""Shared test setup: messages in Ukrainian unless a test asks otherwise."""

import pytest


@pytest.fixture(autouse=True)
def ukrainian(monkeypatch):
    monkeypatch.delenv("LC_ALL", raising=False)
    monkeypatch.delenv("LC_MESSAGES", raising=False)
    monkeypatch.setenv("LANG", "uk_UA.UTF-8")
