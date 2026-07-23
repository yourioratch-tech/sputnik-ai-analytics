import unittest
from dataclasses import replace

from sputnik.config import AppConfig


class ConfigTests(unittest.TestCase):
    def test_default_config_is_valid(self):
        AppConfig().validate()

    def test_invalid_ema_order_fails(self):
        config = AppConfig(strategy=replace(AppConfig().strategy, ema_fast=50, ema_slow=20))
        with self.assertRaisesRegex(ValueError, "ema"):
            config.validate()


if __name__ == "__main__":
    unittest.main()
