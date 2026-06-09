import json
import os
import tempfile
import unittest
from unittest.mock import patch

import urllib.error

from qui_workflows import QuiWorkflowError, resolve_media_scan_path, trigger_dir_scan, trigger_qui_dir_scan


class QuiWorkflowTests(unittest.TestCase):
    def test_resolve_media_scan_path_joins_relative_paths_under_media_root(self):
        with tempfile.TemporaryDirectory() as media_root:
            target_dir = os.path.join(media_root, "Movies", "Example (2024)")
            os.makedirs(target_dir)
            media_file = os.path.join(target_dir, "Example.mkv")
            with open(media_file, "wb") as fh:
                fh.write(b"movie")

            resolved = resolve_media_scan_path(
                {"MEDIA_PATH": media_root},
                os.path.join("Movies", "Example (2024)", "Example.mkv"),
            )

        self.assertEqual(resolved, os.path.abspath(media_file))

    def test_resolve_media_scan_path_rejects_paths_outside_media_root(self):
        with tempfile.TemporaryDirectory() as media_root, tempfile.TemporaryDirectory() as outside:
            outside_file = os.path.join(outside, "escape.mkv")
            with open(outside_file, "wb") as fh:
                fh.write(b"movie")

            with self.assertRaisesRegex(QuiWorkflowError, "outside MEDIA_PATH"):
                resolve_media_scan_path({"MEDIA_PATH": media_root}, outside_file)

            with self.assertRaisesRegex(QuiWorkflowError, "outside MEDIA_PATH"):
                resolve_media_scan_path({"MEDIA_PATH": media_root}, os.path.join("..", "escape.mkv"))

    def test_trigger_dir_scan_requires_qui_source_and_credentials(self):
        with self.assertRaisesRegex(QuiWorkflowError, "Torrent source must be set to qui"):
            trigger_dir_scan({"TORRENT_SOURCE": "qbit", "QUI_HOST": "http://qui:7476", "QUI_API_KEY": "key"}, "/media/file.mkv")

        with self.assertRaisesRegex(QuiWorkflowError, "QUI_HOST is required"):
            trigger_dir_scan({"TORRENT_SOURCE": "qui", "QUI_API_KEY": "key"}, "/media/file.mkv")

        with self.assertRaisesRegex(QuiWorkflowError, "QUI_API_KEY is required"):
            trigger_dir_scan({"TORRENT_SOURCE": "qui", "QUI_HOST": "http://qui:7476"}, "/media/file.mkv")

    @patch("qui_workflows.urllib.request.urlopen")
    def test_trigger_dir_scan_posts_path_to_qui_webhook(self, mock_urlopen):
        class FakeResponse:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return json.dumps({
                    "runId": 42,
                    "directoryId": 7,
                    "directoryPath": "/data/media",
                    "scanRoot": "/data/media/Movie",
                }).encode("utf-8")

        mock_urlopen.return_value = FakeResponse()

        result = trigger_dir_scan({
            "TORRENT_SOURCE": "qui",
            "QUI_HOST": "http://qui.local:7476/",
            "QUI_API_KEY": "secret",
        }, "/data/media/Movie/Movie.mkv")

        self.assertEqual(result["status_code"], 202)
        self.assertEqual(result["response"]["runId"], 42)
        request = mock_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://qui.local:7476/api/dir-scan/webhook/scan")
        self.assertEqual(request.method, "POST")
        self.assertEqual(request.headers["X-api-key"], "secret")
        self.assertEqual(request.headers["Content-type"], "application/json")
        self.assertEqual(json.loads(request.data.decode("utf-8")), {"path": "/data/media/Movie/Movie.mkv"})

    @patch("qui_workflows.urllib.request.urlopen")
    def test_trigger_dir_scan_posts_arr_payload_when_download_client_is_known(self, mock_urlopen):
        class FakeResponse:
            status = 202

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return json.dumps({"runId": 42}).encode("utf-8")

        mock_urlopen.return_value = FakeResponse()

        trigger_dir_scan({
            "TORRENT_SOURCE": "qui",
            "QUI_HOST": "http://qui.local:7476/",
            "QUI_API_KEY": "secret",
        }, "/data/media/Movies4K/Movie/Movie.mkv", service="radarr", download_client="Movies 4K")

        request = mock_urlopen.call_args.args[0]
        self.assertEqual(json.loads(request.data.decode("utf-8")), {
            "downloadClient": "Movies 4K",
            "movie": {"folderPath": "/data/media/Movies4K/Movie/Movie.mkv"},
        })

    @patch("qui_workflows.urllib.request.urlopen")
    def test_trigger_dir_scan_surfaces_qui_http_errors(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "http://qui/api/dir-scan/webhook/scan",
            404,
            "Not Found",
            hdrs=None,
            fp=None,
        )

        with self.assertRaisesRegex(QuiWorkflowError, "HTTP 404"):
            trigger_dir_scan({
                "TORRENT_SOURCE": "qui",
                "QUI_HOST": "http://qui",
                "QUI_API_KEY": "secret",
            }, "/data/media/Movie/Movie.mkv")

    @patch("qui_workflows.trigger_dir_scan")
    def test_trigger_qui_dir_scan_resolves_candidate_path_before_posting(self, mock_trigger):
        mock_trigger.return_value = {"status_code": 202, "response": {"runId": 42}}
        with tempfile.TemporaryDirectory() as media_root:
            result = trigger_qui_dir_scan({
                "TORRENT_SOURCE": "qui",
                "QUI_HOST": "http://qui",
                "QUI_API_KEY": "secret",
                "MEDIA_PATH": media_root,
            }, os.path.join("Movie", "Movie.mkv"))

        self.assertEqual(result["response"]["runId"], 42)
        posted_path = mock_trigger.call_args.args[1]
        self.assertEqual(posted_path, os.path.abspath(os.path.join(media_root, "Movie", "Movie.mkv")))

    @patch("qui_workflows.trigger_dir_scan")
    def test_trigger_qui_dir_scan_uses_arr_connection_download_client(self, mock_trigger):
        mock_trigger.return_value = {"status_code": 202, "response": {"runId": 42}}
        with tempfile.TemporaryDirectory() as media_root:
            trigger_qui_dir_scan({
                "TORRENT_SOURCE": "qui",
                "QUI_HOST": "http://qui",
                "QUI_API_KEY": "secret",
                "MEDIA_PATH": media_root,
                "ARR_CONNECTIONS": [{
                    "id": "radarr-4k",
                    "service": "radarr",
                    "name": "4K Radarr",
                    "base_url": "http://radarr:7878",
                    "api_key": "radarr-key",
                    "qui_download_client": "Movies 4K",
                }],
            }, os.path.join("Movie", "Movie.mkv"), service="radarr", connection_id="radarr-4k")

        self.assertEqual(mock_trigger.call_args.kwargs["service"], "radarr")
        self.assertEqual(mock_trigger.call_args.kwargs["download_client"], "Movies 4K")


class QuiWorkflowRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._data_dir = tempfile.TemporaryDirectory()
        os.environ["DATA_DIR"] = cls._data_dir.name
        import db

        cls._old_data_dir = db.DATA_DIR
        cls._old_db_file = db.DB_FILE
        db.DATA_DIR = cls._data_dir.name
        db.DB_FILE = os.path.join(cls._data_dir.name, "auditorr.db")

    @classmethod
    def tearDownClass(cls):
        import db

        db.DATA_DIR = cls._old_data_dir
        db.DB_FILE = cls._old_db_file
        cls._data_dir.cleanup()

    @patch("app.trigger_qui_dir_scan")
    @patch("app.db_load_config")
    def test_route_triggers_qui_dir_scan_for_candidate_path(self, mock_config, mock_trigger):
        mock_config.return_value = {
            "TORRENT_SOURCE": "qui",
            "QUI_HOST": "http://qui.local:7476",
            "QUI_API_KEY": "secret",
            "MEDIA_PATH": "/data/media",
        }
        mock_trigger.return_value = {
            "path": "/data/media/Movie/Movie.mkv",
            "status_code": 202,
            "response": {"runId": 42},
        }

        from app import app

        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.post("/api/workflows/qui_dir_scan", json={
                "path": "Movie/Movie.mkv",
                "service": "radarr",
                "connection_id": "radarr-4k",
            })

        self.assertEqual(response.status_code, 200)
        data = response.get_json()
        self.assertEqual(data["status"], "success")
        self.assertEqual(data["qui"]["response"]["runId"], 42)
        mock_trigger.assert_called_once_with(
            mock_config.return_value,
            "Movie/Movie.mkv",
            service="radarr",
            connection_id="radarr-4k",
        )

    @patch("app.trigger_qui_dir_scan", side_effect=QuiWorkflowError("QUI_HOST is required"))
    @patch("app.db_load_config", return_value={"TORRENT_SOURCE": "qui", "MEDIA_PATH": "/data/media"})
    def test_route_returns_bad_request_for_qui_config_errors(self, _mock_config, _mock_trigger):
        from app import app

        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.post("/api/workflows/qui_dir_scan", json={"path": "Movie/Movie.mkv"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["message"], "QUI_HOST is required")


if __name__ == "__main__":
    unittest.main()
