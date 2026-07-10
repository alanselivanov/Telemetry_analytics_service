from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from .client import (
    CLICK_LOG_DEFAULT_COLUMNS,
    OmnicommClient,
    default_groups_for_columns,
    extract_click_log_rows,
)


class ClickLogHelpersTests(SimpleTestCase):
    def test_default_groups_include_lls_for_lls_columns(self):
        groups = default_groups_for_columns(["EVENT_DATE", "SPEED", "LLS_CODE"])

        self.assertEqual(groups, ["GENERAL", "LLS"])

    def test_default_groups_general_only_without_lls(self):
        groups = default_groups_for_columns(["EVENT_DATE", "SPEED"])

        self.assertEqual(groups, ["GENERAL"])

    def test_extract_click_log_rows_filters_invalid_entries(self):
        rows = extract_click_log_rows(
            {
                "columns": [
                    {"EVENT_DATE": 100, "LLS_CODE": [10, 20]},
                    "bad-row",
                    {"EVENT_DATE": 200, "LLS_CODE": [11, 21]},
                ]
            }
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["EVENT_DATE"], 100)

    def test_extract_click_log_rows_returns_empty_for_missing_columns(self):
        self.assertEqual(extract_click_log_rows({}), [])
        self.assertEqual(extract_click_log_rows({"columns": None}), [])


class OmnicommClientClickLogTests(SimpleTestCase):
    def setUp(self):
        self.client = OmnicommClient(timeout=5)
        self.client.jwt = "header.payload.signature"
        self.client.server_name = "test-server"

    @patch("omnicomm.client._load_omnicomm_config")
    def test_fetch_click_log_auto_groups_and_columns(self, load_config_mock: MagicMock) -> None:
        load_config_mock.return_value = {
            "login_endpoint": "https://example.test/login",
            "vehicle_tree_endpoint": "https://example.test/tree",
            "click_log_endpoint": "https://example.test/click/log",
            "timeout": 5,
        }
        client = OmnicommClient(timeout=5)
        client.jwt = "header.payload.signature"
        client.server_name = "test-server"

        response_mock = MagicMock()
        response_mock.status_code = 200
        response_mock.json.return_value = {"columns": [{"EVENT_DATE": 1, "LLS_CODE": [1, 2]}]}

        with patch.object(client._http, "post", return_value=response_mock) as post_mock:
            result = client.fetch_click_log(
                terminal_id=303020190,
                date_from=1_770_000_000,
                date_to=1_770_086_400,
            )

        post_mock.assert_called_once()
        payload = post_mock.call_args.kwargs["json"]
        self.assertEqual(payload["groups"], ["GENERAL", "LLS"])
        self.assertEqual(payload["columns"], CLICK_LOG_DEFAULT_COLUMNS)
        self.assertEqual(len(result["columns"]), 1)

    @patch("omnicomm.client._load_omnicomm_config")
    def test_fetch_click_log_respects_explicit_empty_groups(self, load_config_mock: MagicMock) -> None:
        load_config_mock.return_value = {
            "login_endpoint": "https://example.test/login",
            "vehicle_tree_endpoint": "https://example.test/tree",
            "click_log_endpoint": "https://example.test/click/log",
            "timeout": 5,
        }
        client = OmnicommClient(timeout=5)
        client.jwt = "header.payload.signature"
        client.server_name = "test-server"

        response_mock = MagicMock()
        response_mock.status_code = 200
        response_mock.json.return_value = {"columns": []}

        with patch.object(client._http, "post", return_value=response_mock) as post_mock:
            client.fetch_click_log(
                terminal_id=303020190,
                date_from=1_770_000_000,
                date_to=1_770_086_400,
                groups=[],
            )

        payload = post_mock.call_args.kwargs["json"]
        self.assertEqual(payload["groups"], [])
