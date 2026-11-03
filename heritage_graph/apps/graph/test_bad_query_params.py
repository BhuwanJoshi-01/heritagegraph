"""Malformed input on public endpoints is a 400, not a 500.

Each of these raised (ValueError from int(), Django's ValidationError from a
UUID filter, KeyError, AttributeError on a non-object body) and returned 500.

Run:
    cd heritage_graph
    DJANGO_ENV=development python manage.py test apps.graph.test_bad_query_params -v2
"""

from apps.cidoc_data.models import ArchitecturalStructure, HeritageAssertion
from django.contrib.contenttypes.models import ContentType
from django.test import TestCase, override_settings
from rest_framework.test import APIClient

RESOURCE = "https://cair-nepal.org/heritagegraph/resource/person/1"


@override_settings(RDF_SYNC_ENABLED=False)
class BadQueryParamsTest(TestCase):
    def setUp(self):
        self.client = APIClient()

    def assert400(self, resp):
        self.assertEqual(resp.status_code, 400, resp.content[:300])

    def test_graph_limits_must_be_integers(self):
        self.assert400(self.client.get("/cidoc/kg/graph/?node_limit=abc"))
        self.assert400(self.client.get("/cidoc/kg/graph/?edge_limit=1e3"))

    def test_neighborhood_limit_must_be_an_integer(self):
        self.assert400(self.client.get(f"/cidoc/kg/neighborhood/?uri={RESOURCE}&limit=x"))

    def test_query_body_must_be_an_object(self):
        resp = self.client.post("/cidoc/kg/query/", [1, 2], format="json")
        self.assert400(resp)

    def test_assertion_filters_validate_their_ids(self):
        self.assert400(self.client.get("/cidoc/assertions/?entity_id=abc"))
        self.assert400(self.client.get("/cidoc/assertions/?entity_cluster=nope"))

    def test_logout_without_refresh_token(self):
        self.assert400(self.client.post("/auth/logout/", {}, format="json"))

    def test_uuid_filters_on_data_endpoints(self):
        self.assert400(self.client.get("/data/revisions/?entity=nope"))
        self.assert400(self.client.get("/data/review-decisions-profile/?reviewer=nope"))
        self.assert400(self.client.get("/data/shares/count/?entity_id=nope"))


@override_settings(RDF_SYNC_ENABLED=False)
class AssertionStatusFilterTest(TestCase):
    """The UI asks for ?reconciliation_status=accepted (instance-graph.ts); the
    view only read ?status=, so every pending assertion came back as an edge."""

    def test_reconciliation_status_filters_like_status(self):
        ct = ContentType.objects.get_for_model(ArchitecturalStructure)
        temple = ArchitecturalStructure.objects.create(
            name="T", structure_type="Temple"
        )
        for state in ("accepted", "pending"):
            HeritageAssertion.objects.create(
                content_type=ct,
                object_id=temple.pk,
                asserted_property="material",
                asserted_value=state,
                reconciliation_status=state,
                contributed_by="test",
            )
        client = APIClient()
        for param in ("status", "reconciliation_status"):
            resp = client.get(f"/cidoc/assertions/?{param}=accepted")
            self.assertEqual(resp.status_code, 200)
            values = [r["asserted_value"] for r in resp.json()["results"]]
            self.assertEqual(values, ["accepted"], param)
