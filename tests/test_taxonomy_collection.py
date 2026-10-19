from tools.build_taxonomy_from_vacancies import _iter_active_vacancies


class FakeCursor:
    def __init__(self, rows):
        self.rows = list(rows)

    def sort(self, field, direction):
        assert field == "referenznummer"
        assert direction == 1
        self.rows.sort(key=lambda row: row.get(field, ""))
        return self

    def limit(self, count):
        self.rows = self.rows[:count]
        return self

    def __iter__(self):
        return iter(self.rows)


class FakeCollection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.find_queries = []

    def find(self, query):
        self.find_queries.append(query)
        if "is_active" in query:
            rows = [row for row in self.rows if row.get("is_active") is query["is_active"]]
        else:
            refs = query["referenznummer"]["$in"]
            rows = [row for row in self.rows if row.get("referenznummer") in refs]
        return FakeCursor(rows)


class FakeDatabase:
    def __init__(self, collections):
        self.collections = collections

    def __getitem__(self, name):
        return self.collections[name]


def test_active_vacancies_sort_and_bulk_fetch_details_with_missing_rows():
    refs = [f"R-{number:03d}" for number in reversed(range(205))]
    list_collection = FakeCollection(
        [{"referenznummer": ref, "is_active": True} for ref in refs]
    )
    # Details intentionally omit R-017 to confirm that missing rows keep the
    # previous empty-dictionary behavior.
    details_collection = FakeCollection(
        [
            {"referenznummer": ref, "title": f"Title {ref}"}
            for ref in refs
            if ref != "R-017"
        ]
    )
    db = FakeDatabase(
        {"stellenangebote": list_collection, "jobdetails": details_collection}
    )

    vacancies = _iter_active_vacancies(db, {}, max_vacancies=205)

    assert len(list_collection.find_queries) == 1
    assert len(details_collection.find_queries) == 2
    assert [meta["referenznummer"] for meta, _ in vacancies] == sorted(refs)
    assert vacancies[17] == ({"referenznummer": "R-017", "is_active": True}, {})
    assert all("$in" in query["referenznummer"] for query in details_collection.find_queries)
    assert [len(query["referenznummer"]["$in"]) for query in details_collection.find_queries] == [200, 5]
