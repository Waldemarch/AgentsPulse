"""Tests for the secondary provider registry."""
from __future__ import annotations

import unittest

from agentpulse.codex_cli import codex_version
from agentpulse.kimi_cli import kimi_version
from agentpulse.providers import SECONDARY_PROVIDERS, SECONDARY_PROVIDERS_BY_NAME, SecondaryProviderSpec


class TestSecondaryProviders(unittest.TestCase):
    """Tests for the SECONDARY_PROVIDERS registry contents."""

    def test_codex_and_kimi_are_registered(self):
        self.assertEqual({spec.name for spec in SECONDARY_PROVIDERS}, {'codex', 'kimi'})

    def test_by_name_indexes_match_the_tuple(self):
        self.assertEqual(set(SECONDARY_PROVIDERS_BY_NAME), {spec.name for spec in SECONDARY_PROVIDERS})
        for spec in SECONDARY_PROVIDERS:
            self.assertIs(SECONDARY_PROVIDERS_BY_NAME[spec.name], spec)

    def test_codex_spec_wires_the_real_version_lookup(self):
        self.assertIs(SECONDARY_PROVIDERS_BY_NAME['codex'].cli_version, codex_version)

    def test_kimi_spec_wires_the_real_version_lookup(self):
        self.assertIs(SECONDARY_PROVIDERS_BY_NAME['kimi'].cli_version, kimi_version)

    def test_install_title_fallbacks_are_human_readable(self):
        self.assertEqual(SECONDARY_PROVIDERS_BY_NAME['codex'].install_title_fallback, 'CODEX CLI')
        self.assertEqual(SECONDARY_PROVIDERS_BY_NAME['kimi'].install_title_fallback, 'KIMI CLI')

    def test_spec_is_immutable(self):
        spec = SECONDARY_PROVIDERS_BY_NAME['codex']
        with self.assertRaises(Exception):
            spec.name = 'renamed'  # type: ignore[misc]

    def test_spec_fields_are_all_populated(self):
        for spec in SECONDARY_PROVIDERS:
            self.assertIsInstance(spec, SecondaryProviderSpec)
            self.assertTrue(spec.name)
            self.assertTrue(callable(spec.cli_version))
            self.assertTrue(spec.install_title_key)
            self.assertTrue(spec.install_title_fallback)


if __name__ == '__main__':
    unittest.main()
