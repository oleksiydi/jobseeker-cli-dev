from __future__ import annotations

from unittest.mock import patch

import app.job_details_collector as job_details_collector


class FakeLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class FakeListCollection:
    def __init__(self):
        self.find_query = None
        self.find_projection = None

    def find(self, query=None, projection=None):
        self.find_query = query
        self.find_projection = projection
        return []


class FakeCollection:
    def create_index(self, *args, **kwargs):
        return None


class FakeDB:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, key):
        return self.collections[key]


def test_main_reads_only_active_list_records():
    list_col = FakeListCollection()
    db = FakeDB(
        {
            "stellenangebote": list_col,
            "jobdetails": FakeCollection(),
            "jobdetails_history": FakeCollection(),
        }
    )
    cfg = {
        "collections": {"list": "stellenangebote", "details": "jobdetails"},
        "history_collections": {"details": "jobdetails_history"},
        "jobcenter": {"details_api": {"url_template": "https://example/{ref_b64}", "headers": {}}},
    }

    with (
        patch.object(job_details_collector, "load_app_config", return_value=cfg),
        patch.object(job_details_collector, "get_database", return_value=db),
        patch.object(job_details_collector, "get_logger", return_value=FakeLogger()),
    ):
        job_details_collector.main()

    assert list_col.find_query == {"is_active": True}
