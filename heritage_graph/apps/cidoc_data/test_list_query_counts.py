"""List endpoints must not run queries per row.

`cultural_entity_id` ran one query per row, plus a scan of every revision's JSON
for rows without the FK link; structures fetched their location per row; the
assertion list fetched `supporting_sources` and `object_content_type` per row;
and the graph's provenance map fetched two content types per assertion.

Run:
    cd heritage_graph
    DJANGO_ENV=development python manage.py test \
        apps.cidoc_data.test_list_query_counts -v2
"""

from __future__ import annotations

from apps.cidoc_data.models import (
    ArchitecturalStructure,
    DataSource,
    HeritageAssertion,
    Location,
)
from apps.cidoc_data.serializers import _get_cultural_entity_id
from apps.graph.views import _assertion_provenance_map
from apps.heritage_data.models import CulturalEntity, Revision
from django.contrib.auth import get_user_model
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient


def _count_queries(fn) -> int:
    with CaptureQueriesContext(connection) as ctx:
        fn()
    return len(ctx)


@override_settings(RDF_SYNC_ENABLED=False)
class CidocListQueryCountTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        user = get_user_model().objects.create_user(
            username="contrib", email="contrib@example.com", password="x"
        )
        ct = ContentType.objects.get_for_model(Location)
        self.locations = []
        for i in range(12):
            loc = Location.objects.create(
                name=f"Shrine {i}",
                type="temple",
                current_status="preserved",
                status="accepted",
            )
            self.locations.append(loc)
            entity = CulturalEntity.objects.create(
                name=loc.name, category="other", status="accepted", contributor=user
            )
            if i % 3 == 0:
                # Current rows: the FK written by ContributionFlowMixin.
                entity.cidoc_content_type = ct
                entity.cidoc_object_id = loc.pk
                entity.save(update_fields=["cidoc_content_type", "cidoc_object_id"])
            elif i % 3 == 1:
                # Older rows: only the revision JSON points back.
                Revision.objects.create(
                    entity=entity,
                    data={
                        "name": loc.name,
                        "_cidoc_model": "Location",
                        "_cidoc_id": loc.pk,
                    },
                    revision_number=1,
                    created_by=user,
                )
            # i % 3 == 2: no wrapper at all.

    def _list(self, path, limit):
        resp = self.client.get(f"{path}?limit={limit}")
        self.assertEqual(resp.status_code, 200, resp.content[:300])
        return resp.json()["results"]

    def test_cultural_entity_id_matches_the_per_row_lookup(self):
        rows = self._list("/cidoc/locations/", 12)
        self.assertEqual(len(rows), 12)
        by_id = {loc.pk: loc for loc in self.locations}
        for row in rows:
            self.assertEqual(
                row["cultural_entity_id"], _get_cultural_entity_id(by_id[row["id"]])
            )
        self.assertEqual(sum(1 for r in rows if r["cultural_entity_id"]), 8)

    def test_location_list_query_count_does_not_grow_with_page_size(self):
        small = _count_queries(lambda: self._list("/cidoc/locations/", 3))
        large = _count_queries(lambda: self._list("/cidoc/locations/", 12))
        self.assertEqual(small, large)

    def test_structure_list_does_not_fetch_each_location(self):
        for i, loc in enumerate(self.locations):
            ArchitecturalStructure.objects.create(
                name=f"Temple {i}",
                structure_type="Temple",
                status="accepted",
                has_current_location=loc,
            )
        small = _count_queries(lambda: self._list("/cidoc/structures/", 3))
        rows = []
        large = _count_queries(
            lambda: rows.extend(self._list("/cidoc/structures/", 12))
        )
        self.assertEqual(small, large)
        self.assertEqual(
            {r["has_current_location"]["name"] for r in rows},
            {loc.name for loc in self.locations},
        )


@override_settings(RDF_SYNC_ENABLED=False)
class AssertionQueryCountTest(TestCase):
    def setUp(self):
        self.client = APIClient()
        place_ct = ContentType.objects.get_for_model(Location)
        temple_ct = ContentType.objects.get_for_model(ArchitecturalStructure)
        source = DataSource.objects.create(name="Wikidata", source_type="web")
        extra = DataSource.objects.create(name="DANAM", source_type="web")
        for i in range(8):
            place = Location.objects.create(name=f"Place {i}")
            temple = ArchitecturalStructure.objects.create(
                name=f"Temple {i}", structure_type="Temple"
            )
            assertion = HeritageAssertion.objects.create(
                content_type=temple_ct,
                object_id=temple.pk,
                object_content_type=place_ct,
                object_object_id=place.pk,
                asserted_property="relationship.has_current_location",
                source=source,
                reconciliation_status="accepted",
                contributed_by="test",
            )
            assertion.supporting_sources.add(extra)

    def test_assertion_list_query_count_does_not_grow_with_page_size(self):
        def page(limit):
            resp = self.client.get(f"/cidoc/assertions/?limit={limit}")
            self.assertEqual(resp.status_code, 200, resp.content[:300])
            return resp.json()["results"]

        small = _count_queries(lambda: page(2))
        rows = []
        large = _count_queries(lambda: rows.extend(page(8)))
        self.assertEqual(small, large)
        self.assertEqual(len(rows), 8)
        self.assertTrue(all(len(r["supporting_sources"]) == 1 for r in rows))
        self.assertTrue(all(r["object_entity_type"] == "location" for r in rows))

    def test_provenance_map_runs_one_query(self):
        with self.assertNumQueries(1):
            prov = _assertion_provenance_map()
        self.assertEqual(len(prov), 8)
