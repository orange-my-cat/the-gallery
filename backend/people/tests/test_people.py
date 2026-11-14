"""People end to end: finding faces, grouping, and every owner action with undo.

The face models are replaced by a stand-in that returns chosen faces for each
photo (by its bytes), with descriptions drawn around a few made-up people.
"""

from __future__ import annotations

import hashlib

import numpy as np
import pytest
from django.contrib.auth import get_user_model
from django.test import Client

from fileops.services import ops as fileops
from library.models import MediaFile
from library.services.scan import start_scan
from library.tests.conftest import drain, jpeg_bytes, library, tags, write  # noqa: F401 - fixtures
from people.models import Face, FaceScan, Person
from people.services import cluster, faces, ops

pytestmark = pytest.mark.django_db

RNG = np.random.default_rng(7)
IDENTITY = {name: RNG.normal(size=128) for name in ("ana", "ben", "cy")}


def look(name: str, jitter: float = 0.15) -> np.ndarray:
    v = IDENTITY[name] / np.linalg.norm(IDENTITY[name]) + RNG.normal(scale=jitter / np.sqrt(128), size=128)
    return (v / np.linalg.norm(v)).astype(np.float32)


@pytest.fixture
def fake_faces(monkeypatch, tags):
    """`fake_faces[sha] = ["ana", "ben"]` - who is in the photo with those bytes."""
    table: dict[str, list[str]] = {}

    def detect(path):
        sha = path.name.split("_")[0]
        return [
            faces.Found(box=(0.1 * i, 0.2, 0.1, 0.1), score=0.9 - 0.01 * i, embedding=look(who), crop=b"RIFFcrop")
            for i, who in enumerate(table.get(sha, []))
        ]

    monkeypatch.setattr(faces, "detect", detect)
    monkeypatch.setattr(faces, "present", lambda folder: True)
    return table


def photo(library, fake_faces, name: str, *people: str, colour: int = 0) -> str:
    data = jpeg_bytes(color=(colour, 40, 90))
    write(library / name, data)
    fake_faces[hashlib.sha256(data).hexdigest()] = list(people)
    return name


def scan_and_find() -> None:
    start_scan()
    drain()
    while faces.schedule_due():
        drain()


def person_of(name: str) -> Person | None:
    face = Face.objects.filter(file__rel_path=name).first()
    return face.person if face else None


@pytest.fixture
def album(library, fake_faces):
    """Ana in 5 photos, Ben in 4, Cy once; one photo of Ana with Ben."""
    for i in range(5):
        photo(library, fake_faces, f"ana{i}.jpg", "ana", colour=i)
    for i in range(4):
        photo(library, fake_faces, f"ben{i}.jpg", "ben", colour=50 + i)
    photo(library, fake_faces, "cy.jpg", "cy", colour=99)
    photo(library, fake_faces, "both.jpg", "ana", "ben", colour=120)
    scan_and_find()
    return library


# ---------------------------------------------------------------------------
# Finding and grouping
# ---------------------------------------------------------------------------


def test_faces_are_found_once_and_grouped_into_people(album):
    assert Face.objects.count() == 12
    assert FaceScan.objects.count() == 11
    groups = {p.pk: sorted(f.file.rel_path for f in p.faces.all()) for p in Person.objects.all()}
    assert len(groups) == 2, "Cy, seen once, is not a group"
    ana = person_of("ana0.jpg")
    assert sorted(f.file.rel_path for f in ana.faces.all()) == ["ana0.jpg", "ana1.jpg", "ana2.jpg", "ana3.jpg", "ana4.jpg", "both.jpg"]
    assert not ana.confirmed and ana.cover_id is not None
    assert person_of("cy.jpg") is None

    # Nothing new: nothing searched again.
    assert faces.schedule_due() == 0


def test_a_metadata_edit_does_not_search_again_but_new_pixels_do(album, fake_faces):
    media = MediaFile.objects.get(rel_path="ana0.jpg")
    FaceScan.objects.filter(file=media).update(pixel_hash=media.pixel_hash)
    assert faces.schedule_due() == 0
    MediaFile.objects.filter(pk=media.pk).update(pixel_hash="0" * 64)
    assert faces.schedule_due() == 1


def test_a_lone_group_left_to_find_is_still_a_group(album):
    ana, ben = person_of("ana0.jpg"), person_of("ben0.jpg")
    ops.rename(ben.pk, "Ben")  # confirmed: out of the clustering pool
    cluster.recluster()
    assert person_of("ana0.jpg") == ana, "Ana alone in the pool is not dissolved as noise"


def test_a_pool_of_strangers_is_never_taken_for_one_person():
    strangers = np.stack([(lambda v: v / np.linalg.norm(v))(RNG.normal(size=128)) for _ in range(8)]).astype(np.float32)
    assert (cluster.lone_group(strangers) < 0).all()
    one_person = np.stack([look("ana") for _ in range(6)])
    assert (cluster.lone_group(one_person) >= 0).sum() >= 3


def test_groups_keep_their_ids_when_clustering_runs_again(album):
    before = {p.pk for p in Person.objects.all()}
    cluster.recluster()
    assert {p.pk for p in Person.objects.all()} == before


def test_new_faces_join_a_named_person(album, fake_faces):
    ana = person_of("ana0.jpg")
    ops.rename(ana.pk, "Ana")
    photo(album, fake_faces, "later.jpg", "ana", colour=200)
    scan_and_find()
    cluster.recluster()
    assert person_of("later.jpg") == Person.objects.get(name="Ana")
    assert Face.objects.get(file__rel_path="later.jpg").assigned_by == "match"


# ---------------------------------------------------------------------------
# The owner's actions, each undoable
# ---------------------------------------------------------------------------


def snapshot() -> dict:
    return {
        "faces": sorted(Face.objects.values_list("pk", "person_id", "ignored", "assigned_by")),
        "people": sorted(Person.objects.values_list("pk", "name", "confirmed")),
    }


def test_naming_confirms_and_undo_puts_it_back(album):
    ana = person_of("ana0.jpg")
    before = snapshot()
    op = ops.rename(ana.pk, "  Ana   Lim ")
    ana.refresh_from_db()
    assert (ana.name, ana.confirmed) == ("Ana Lim", True)
    fileops.undo(op.pk)
    assert snapshot() == before


def test_merging_people_and_undo(album):
    ana, ben = person_of("ana0.jpg"), person_of("ben0.jpg")
    ops.rename(ben.pk, "Ben")
    before = snapshot()
    op = ops.merge([ana.pk, ben.pk])
    assert not Person.objects.filter(pk=ana.pk).exists(), "the named one is kept"
    assert Person.objects.get(pk=ben.pk).faces.count() == 11  # everyone but Cy
    fileops.undo(op.pk)
    assert snapshot() == before


def test_removing_a_face_and_it_is_never_suggested_back(album):
    ana = person_of("ana0.jpg")
    face = Face.objects.get(file__rel_path="ana4.jpg")
    op = ops.remove_faces([face.pk])
    face.refresh_from_db()
    assert face.person is None and ana.pk in face.rejected
    assert Person.objects.get(pk=ana.pk).confirmed
    cluster.recluster()
    face.refresh_from_db()
    assert face.person_id != ana.pk, "matching never puts it back with Ana"
    from people.services import browse

    assert str(face.pk) not in {s["id"] for s in browse.suggestions(ana.pk)}
    fileops.undo(op.pk)


def test_adding_faces_to_a_person_and_starting_a_new_one(album):
    ana = person_of("ana0.jpg")
    cy = Face.objects.get(file__rel_path="cy.jpg")
    op = ops.add_faces([cy.pk], ana.pk)
    assert person_of("cy.jpg") == ana
    fileops.undo(op.pk)
    assert person_of("cy.jpg") is None

    op = ops.add_faces([cy.pk], name="Cy")
    cy_person = Person.objects.get(name="Cy")
    assert person_of("cy.jpg") == cy_person and cy_person.confirmed
    fileops.undo(op.pk)
    assert not Person.objects.filter(name="Cy").exists()


def test_suggestions_rank_faces_that_look_like_the_person(album):
    from people.services import browse

    ana = person_of("ana0.jpg")
    cy = Face.objects.get(file__rel_path="cy.jpg")
    # Nothing unassigned looks like Ana: Cy does not clear the bar.
    assert str(cy.pk) not in {s["id"] for s in browse.suggestions(ana.pk)}
    Face.objects.filter(pk=cy.pk).update(embedding=look("ana").tobytes())
    assert browse.suggestions(ana.pk)[0]["id"] == str(cy.pk)


def test_not_a_face_and_deleting_a_person_set_faces_aside(album):
    ben = person_of("ben0.jpg")
    cy = Face.objects.get(file__rel_path="cy.jpg")
    ops.not_faces([cy.pk])
    cy.refresh_from_db()
    assert cy.ignored

    bens = sorted(ben.faces.values_list("pk", flat=True))
    op = ops.delete_person(ben.pk)
    assert not Person.objects.filter(pk=ben.pk).exists()
    cluster.recluster()
    assert person_of("ben0.jpg") is None, "a deleted group does not come back"
    fileops.undo(op.pk)
    assert sorted(Person.objects.get(pk=ben.pk).faces.values_list("pk", flat=True)) == bens


# ---------------------------------------------------------------------------
# What the screens see
# ---------------------------------------------------------------------------


@pytest.fixture
def signed_in(settings) -> Client:
    user = get_user_model().objects.create_user(username=settings.GALLERY_USERNAME, password="x" * 20)
    client = Client()
    client.force_login(user)
    return client


def test_people_api_and_browsing_by_person(signed_in, album):
    ana, ben = person_of("ana0.jpg"), person_of("ben0.jpg")
    assert signed_in.patch(f"/api/people/{ana.pk}", {"name": "Ana"}, content_type="application/json").status_code == 200

    listing = signed_in.get("/api/people").json()
    assert [p["name"] for p in listing["people"]] == ["Ana", ""], "named first"
    assert listing["people"][0]["faces"] == 6 and listing["people"][0]["items"] == 6
    assert listing["unassigned"] == 1 and listing["scan"] == {"done": 11, "total": 11}

    detail = signed_in.get(f"/api/people/{ana.pk}").json()
    assert len(detail["faces"]) == 6 and detail["faces"][0]["url"].startswith("/media/face/")

    both = signed_in.get("/api/assets", {"people": f"{ana.pk},{ben.pk}"}).json()["results"]
    assert [a["name"] for a in both] == ["both.jpg"]
    only_ana = signed_in.get("/api/assets", {"people": str(ana.pk)}).json()["results"]
    assert len(only_ana) == 6
    item = next(a for a in only_ana if a["name"] == "both.jpg")
    assert [p["name"] for p in item["people"]] == ["Ana", ""]
    assert all(p["faceUrl"].startswith("/media/face/") for p in item["people"])

    cy = Face.objects.get(file__rel_path="cy.jpg")
    assert signed_in.post("/api/faces/assign", {"faces": [cy.pk], "name": "Cy"}, content_type="application/json").status_code == 200
    assert signed_in.post("/api/people/merge", {"ids": [ana.pk]}, content_type="application/json").status_code == 400
    gone = signed_in.post("/api/people/delete", {"ids": [ana.pk, ben.pk]}, content_type="application/json")
    assert gone.status_code == 200 and not Person.objects.filter(pk__in=[ana.pk, ben.pk]).exists()
    assert signed_in.get("/api/people/999999").status_code == 404


def test_face_crops_are_served_to_a_session_only(signed_in, client, album):
    face = Face.objects.first()
    assert client.get(f"/media/face/{face.pk}").status_code == 403
    response = signed_in.get(f"/media/face/{face.pk}")
    assert response.status_code == 200 and response["Content-Type"] == "image/webp"
    assert signed_in.get("/media/face/999999").status_code == 404


def test_trashing_a_photo_takes_its_faces_out_of_people(album, signed_in):
    from fileops.services import ops as files

    files.trash([MediaFile.objects.get(rel_path="both.jpg").asset_id])
    drain()
    listing = signed_in.get("/api/people").json()
    assert sorted(p["faces"] for p in listing["people"]) == [4, 5]


def test_not_people_api(signed_in, album):
    ben = person_of("ben0.jpg")
    response = signed_in.post("/api/people/not-people", {"ids": [ben.pk]}, content_type="application/json")
    assert response.status_code == 200 and "not a person" in response.json()["summary"]
    assert not Person.objects.filter(pk=ben.pk).exists()
