"""Which URLs the store calls for SPARQL reads and writes.

With RDF_QUERY_URL=http://oxigraph:7878/query (every compose file) the first
read went to .../query/query, a 404, so every SELECT cost two requests; and a
timed-out query was run again on each fallback URL.

Run:
    cd heritage_graph
    DJANGO_ENV=development python manage.py test apps.graph.test_store_endpoints -v2
"""

from unittest import mock

import requests
from apps.graph.kg_engine.store import KnowledgeGraphStore, _endpoint_candidates
from django.test import SimpleTestCase

OXI = "http://oxigraph:7878"


def _ok(bindings=()):
    resp = mock.Mock(status_code=200)
    resp.raise_for_status.return_value = None
    resp.json.return_value = {"results": {"bindings": list(bindings)}}
    return resp


class EndpointCandidatesTest(SimpleTestCase):
    def test_query_endpoint_is_tried_first(self):
        self.assertEqual(
            _endpoint_candidates(f"{OXI}/query", "query"),
            [f"{OXI}/query", f"{OXI}/sparql"],
        )

    def test_update_endpoint_used_for_reads_keeps_the_old_order(self):
        self.assertEqual(
            _endpoint_candidates(f"{OXI}/update", "query"),
            [f"{OXI}/query", f"{OXI}/update", f"{OXI}/sparql"],
        )

    def test_update_is_not_sent_to_the_same_url_twice(self):
        self.assertEqual(
            _endpoint_candidates(f"{OXI}/update", "update"),
            [f"{OXI}/update", f"{OXI}/sparql"],
        )


class RemoteSelectTest(SimpleTestCase):
    def setUp(self):
        self.store = KnowledgeGraphStore()

    def test_one_request_when_the_query_url_is_configured(self):
        row = {"s": {"type": "uri", "value": "urn:x"}}
        with mock.patch("requests.get", return_value=_ok([row])) as get:
            rows = self.store._remote_select(
                f"{OXI}/query", "SELECT ?s {}", timeout_s=5
            )
        self.assertEqual(rows, [{"s": "urn:x"}])
        self.assertEqual(get.call_count, 1)
        self.assertEqual(get.call_args.args[0], f"{OXI}/query")

    def test_a_timeout_is_not_retried_on_the_other_urls(self):
        with mock.patch("requests.get", side_effect=requests.Timeout()) as get:
            rows = self.store._remote_select(
                f"{OXI}/query", "SELECT ?s {}", timeout_s=5
            )
        self.assertEqual(rows, [])
        self.assertEqual(get.call_count, 1)

    def test_a_wrong_url_still_falls_back(self):
        missing = mock.Mock(status_code=404)
        missing.raise_for_status.side_effect = requests.HTTPError("404")
        with mock.patch("requests.get", side_effect=[missing, _ok()]) as get:
            self.store._remote_select(f"{OXI}/query", "SELECT ?s {}", timeout_s=5)
        self.assertEqual(
            [c.args[0] for c in get.call_args_list], [f"{OXI}/query", f"{OXI}/sparql"]
        )


class RemoteUpdateTest(SimpleTestCase):
    def test_a_timed_out_update_is_not_resent(self):
        store = KnowledgeGraphStore()
        with mock.patch("requests.post", side_effect=requests.Timeout()) as post:
            ok = store._remote_update(f"{OXI}/update", "INSERT DATA {}", timeout_s=5)
        self.assertFalse(ok)
        self.assertEqual(post.call_count, 1)
