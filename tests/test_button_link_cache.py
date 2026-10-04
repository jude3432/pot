import unittest
from unittest.mock import patch

from database import repository


class ButtonLinkCacheTests(unittest.TestCase):
    def setUp(self):
        repository.invalidate_button_link_cache()

    def tearDown(self):
        repository.invalidate_button_link_cache()

    def test_cache_miss_uses_fallback_without_synchronous_database_read(self):
        with patch.object(
            repository.DatabaseManager,
            "execute_query_dict",
            side_effect=AssertionError("menu keyboard must not query the database"),
        ) as query:
            self.assertEqual(
                repository.get_button_link("website_url"),
                repository.get_button_link_fallback("website_url"),
            )
            self.assertEqual(repository.get_button_link_source("website_url"), "render")
        query.assert_not_called()

    def test_background_refresh_loads_all_links_with_one_query(self):
        rows = [
            {"payment_method": "website_url", "address": "https://custom.example.test"},
            {"payment_method": "games_url", "address": "https://games.example.test"},
        ]
        with patch.object(
            repository.DatabaseManager,
            "execute_query_dict",
            return_value=rows,
        ) as query:
            count = repository.refresh_button_link_cache()

        self.assertEqual(count, 2)
        self.assertEqual(repository.get_button_link("website_url"), "https://custom.example.test")
        self.assertEqual(repository.get_button_link_source("website_url"), "database")
        self.assertEqual(
            repository.get_button_link("app_download_url"),
            repository.get_button_link_fallback("app_download_url"),
        )
        query.assert_called_once()

    def test_setting_a_link_updates_cache_without_reading_it_back(self):
        with patch.object(repository.DatabaseManager, "execute_query") as execute:
            self.assertTrue(
                repository.set_button_link(
                    "website_url",
                    " https://new.example.test ",
                    updated_by="test",
                )
            )

        self.assertEqual(repository.get_button_link("website_url"), "https://new.example.test")
        self.assertEqual(repository.get_button_link_source("website_url"), "database")
        execute.assert_called_once()


if __name__ == "__main__":
    unittest.main()