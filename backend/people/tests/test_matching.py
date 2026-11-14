"""Matching to confirmed people: several faces, counter-examples, re-runs."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
from django.utils import timezone

from jobs.models import Job
from library.models import MediaFile
from library.tests.conftest import drain, library, tags  # noqa: F401 - fixtures
from people.models import AssignedBy, Face, MatchRun, Person
from people.services import cluster, matching, ops

from .test_people import album, fake_faces, look, person_of  # noqa: F401 - fixtures and helpers

pytestmark = pytest.mark.django_db

RNG = np.random.default_rng(11)


def unit(v: np.ndarray) -> np.ndarray:
    return (v / np.linalg.norm(v)).astype(np.float32)


def near(v: np.ndarray, jitter: float = 0.1) -> np.ndarray:
    return unit(v + RNG.normal(scale=jitter / np.sqrt(128), size=128))


def add_face(vector: np.ndarray, person: Person | None = None, by: str = "") -> Face:
    """A face on one of the album's files, described by `vector`."""
    media = MediaFile.objects.first()
    return Face.objects.create(
        file=media, x=0.5, y=0.5, w=0.1, h=0.1, score=0.9, embedding=vector.tobytes(), person=person, assigned_by=by
    )


def test_naming_someone_gathers_their_other_faces_at_once(album):
    ana = person_of("ana0.jpg")
    stray = Face.objects.get(file__rel_path="ana4.jpg")
    Face.objects.filter(pk=stray.pk).update(person=None, assigned_by="")
    ops.rename(ana.pk, "Ana")
    assert Job.objects.filter(kind="match_faces").exists(), "a quick run is queued, no new photos needed"
    drain()
    stray.refresh_from_db()
    assert stray.person_id == ana.pk and stray.assigned_by == AssignedBy.MATCH
    assert MatchRun.objects.filter(kind="match", finished_at__isnull=False).exists()


def test_a_face_is_recognised_from_the_look_it_resembles_not_the_average(album):
    """Twelve looks over the years: the average resembles none of them."""
    looks = [unit(RNG.normal(size=128)) for _ in range(12)]
    person = Person.objects.create(name="Grows up", confirmed=True)
    for v in looks:
        for _ in range(2):
            add_face(near(v), person, AssignedBy.OWNER)
    newcomer = add_face(near(looks[5]))
    mean = unit(np.mean([f.vector() for f in person.faces.all()], axis=0))
    assert float(newcomer.vector() @ mean) < matching.MATCH, "matching the average alone would miss it"
    matching.match()
    newcomer.refresh_from_db()
    assert newcomer.person_id == person.pk


def test_one_lucky_look_alike_is_not_enough(album):
    person = Person.objects.create(name="Dee", confirmed=True)
    base = unit(RNG.normal(size=128))
    for _ in range(4):
        add_face(near(base, 0.05), person, AssignedBy.OWNER)
    odd = unit(RNG.normal(size=128))
    add_face(odd, person, AssignedBy.OWNER)  # one odd face in their set
    stranger = add_face(near(odd, 0.05))
    matching.match()
    stranger.refresh_from_db()
    assert stranger.person_id is None, "it resembles only one of their five faces"


def twin_of(v: np.ndarray, closeness: float = 0.75) -> np.ndarray:
    """Someone who looks a good deal like `v` (cosine about `closeness`)."""
    other = unit(RNG.normal(size=128))
    other = unit(other - (other @ v) * v)
    return unit(closeness * v + np.sqrt(1 - closeness**2) * other)


def test_a_face_like_the_ones_removed_from_someone_stays_out(album):
    base = unit(RNG.normal(size=128))
    twin = twin_of(base)
    person = Person.objects.create(name="Eve", confirmed=True)
    for _ in range(4):
        add_face(near(base, 0.05), person, AssignedBy.OWNER)
    # Without counter-examples the twin would join Eve...
    trial = add_face(near(twin, 0.05))
    matching.match()
    trial.refresh_from_db()
    assert trial.person_id == person.pk
    # ...the owner takes the twin's faces away from Eve...
    removed = [add_face(near(twin, 0.05), person, AssignedBy.OWNER) for _ in range(2)]
    ops.remove_faces([f.pk for f in removed] + [trial.pk])
    drain()
    # ...and from then on the twin's faces stay out - including a new one.
    newcomer = add_face(near(twin, 0.05))
    matching.match()
    newcomer.refresh_from_db()
    assert newcomer.person_id is None
    assert all(Face.objects.get(pk=f.pk).person_id is None for f in removed)


def test_an_earlier_match_is_released_when_it_no_longer_fits(album):
    base = unit(RNG.normal(size=128))
    twin = twin_of(base)
    person = Person.objects.create(name="Fay", confirmed=True)
    for _ in range(4):
        add_face(near(base, 0.05), person, AssignedBy.OWNER)
    matched = add_face(near(twin, 0.05))
    matching.match()
    matched.refresh_from_db()
    assert (matched.person_id, matched.assigned_by) == (person.pk, AssignedBy.MATCH)
    # The owner removes other faces of the twin; the earlier match is re-decided.
    others = [add_face(near(twin, 0.05), person, AssignedBy.OWNER) for _ in range(2)]
    ops.remove_faces([f.pk for f in others])
    drain()
    matched.refresh_from_db()
    assert matched.person_id is None


def test_matched_faces_do_not_vouch_for_more(album):
    """Only the owner's faces are anchors: no chain of look-alikes."""
    ana = person_of("ana0.jpg")
    ops.rename(ana.pk, "Ana")
    drain()
    ref = matching.reference()
    j = ref.people.index(ana.pk)
    a, b = ref.spans[j]
    assert b - a == Face.objects.filter(person=ana).exclude(assigned_by=AssignedBy.MATCH).count()


def test_suggestions_skip_faces_that_look_like_ones_removed(album):
    from people.services import browse

    base = unit(RNG.normal(size=128))
    twin = twin_of(base)
    person = Person.objects.create(name="Gus", confirmed=True)
    for _ in range(4):
        add_face(near(base, 0.05), person, AssignedBy.OWNER)
    removed = [add_face(near(twin, 0.05), person, AssignedBy.OWNER) for _ in range(2)]
    ops.remove_faces([f.pk for f in removed])
    candidate = add_face(near(twin, 0.05))
    assert str(candidate.pk) not in {s["id"] for s in browse.suggestions(person.pk)}


def test_a_full_run_happens_at_least_daily(album):
    Job.objects.all().delete()
    MatchRun.objects.all().delete()
    assert cluster.schedule_due() is True
    drain()
    run = MatchRun.objects.get(kind="full")
    assert run.finished_at is not None and "pool" in run.summary
    assert cluster.schedule_due() is False, "one ran just now"
    MatchRun.objects.filter(pk=run.pk).update(finished_at=timezone.now() - timedelta(hours=25))
    assert cluster.schedule_due() is True


def test_a_new_matching_rule_runs_again_at_once(album):
    Job.objects.all().delete()
    MatchRun.objects.all().delete()
    cluster.schedule_due()
    drain()
    run = MatchRun.objects.get(kind="full")
    assert run.summary["rule"] == matching.RULE
    assert cluster.schedule_due() is False
    MatchRun.objects.filter(pk=run.pk).update(summary={**run.summary, "rule": "join>=0.4;need=2"})
    assert cluster.schedule_due() is True, "faces joined under the old rule are decided again"


def test_raising_the_bar_releases_weaker_earlier_matches(album):
    base = unit(RNG.normal(size=128))
    person = Person.objects.create(name="Hal", confirmed=True)
    for _ in range(4):
        add_face(near(base, 0.05), person, AssignedBy.OWNER)
    weak = add_face(twin_of(base, 0.45), person, AssignedBy.MATCH)  # joined under the old 0.40 bar
    matching.match()
    weak.refresh_from_db()
    assert weak.person_id is None


def test_people_merged_but_never_named_are_matched_too(album):
    """Merging is confirming: an unnamed person the owner shaped counts."""
    ana = person_of("ana0.jpg")
    split = Person.objects.create()  # a second group the app found for Ana
    moved = Face.objects.get(file__rel_path="ana4.jpg")
    Face.objects.filter(pk=moved.pk).update(person=split, assigned_by=AssignedBy.CLUSTER)
    ops.merge([ana.pk, split.pk])
    kept = Person.objects.get(faces=moved)
    assert (kept.name, kept.confirmed) == ("", True)
    assert kept.pk in matching.reference().people
    stray = add_face(look("ana", jitter=0.05))
    drain()  # the merge queued a quick run
    stray.refresh_from_db()
    assert stray.person_id == kept.pk


def test_a_group_marked_not_a_person_and_its_look_alikes_later(album):
    from fileops.services import ops as fileops

    statue = unit(RNG.normal(size=128))
    group = Person.objects.create()  # what the app found: the same statue, 4 times
    marked = [add_face(near(statue, 0.05), group, AssignedBy.CLUSTER) for _ in range(4)]
    ana = person_of("ana0.jpg")
    ops.rename(ana.pk, "Ana")

    op = ops.not_people([group.pk])
    assert not Person.objects.filter(pk=group.pk).exists()
    assert all(
        (f.ignored, f.ignored_because) == (True, "not_person") for f in Face.objects.filter(pk__in=[m.pk for m in marked])
    )
    drain()

    # The statue again, in a new photo: set aside by itself. Ana is not.
    again = add_face(near(statue, 0.05))
    someone = add_face(look("ana", jitter=0.05))
    summary = matching.match()
    again.refresh_from_db()
    someone.refresh_from_db()
    assert (again.ignored, again.ignored_because, again.person_id) == (True, "auto", None)
    assert not someone.ignored and someone.person_id == ana.pk
    assert summary["set_aside"] == 1

    # Undo: the group comes back, and the look-alike is decided again.
    fileops.undo(op.pk)
    assert Person.objects.filter(pk=group.pk).exists()
    matching.match()
    again.refresh_from_db()
    assert not again.ignored


def test_not_a_face_teaches_too(album):
    pattern = unit(RNG.normal(size=128))
    wrong = [add_face(near(pattern, 0.05)) for _ in range(3)]
    ops.not_faces([f.pk for f in wrong])
    assert Face.objects.filter(pk__in=[f.pk for f in wrong], ignored_because="not_face").count() == 3
    later = add_face(near(pattern, 0.05))
    matching.match()
    later.refresh_from_db()
    assert later.ignored and later.ignored_because == "auto"


def test_deleting_a_group_does_not_teach(album):
    """Deleting says "not interested", not "not a person": no look-alikes set aside."""
    stranger = unit(RNG.normal(size=128))
    group = Person.objects.create()
    for _ in range(4):
        add_face(near(stranger, 0.05), group, AssignedBy.CLUSTER)
    ops.delete_people([group.pk])
    later = add_face(near(stranger, 0.05))
    matching.match()
    later.refresh_from_db()
    assert not later.ignored


# ---------------------------------------------------------------------------
# "Are they the same person?"
# ---------------------------------------------------------------------------


def test_questions_ask_about_faces_near_the_line_and_answers_teach(album):
    from fileops.services import ops as fileops
    from people.services import questions

    base = unit(RNG.normal(size=128))
    person = Person.objects.create(name="Ivy", confirmed=True)
    for _ in range(4):
        add_face(near(base, 0.03), person, AssignedBy.OWNER)
    sure = add_face(near(base, 0.03))  # clearly her: no need to ask
    unsure = add_face(twin_of(base, 0.48))  # just under the line
    stranger = add_face(unit(RNG.normal(size=128)))  # clearly not
    asked = questions.questions()["questions"]
    ids = [q["face"]["id"] for q in asked]
    assert str(unsure.pk) in ids
    assert str(sure.pk) not in ids and str(stranger.pk) not in ids
    q = next(q for q in asked if q["face"]["id"] == str(unsure.pk))
    assert q["person"]["name"] == "Ivy" and len(q["person"]["faces"]) == 4 and not q["matched"]

    # "No": a counter-example, and never asked about Ivy again.
    op = ops.not_them(unsure.pk, person.pk)
    unsure.refresh_from_db()
    assert person.pk in unsure.rejected
    assert str(unsure.pk) not in [q["face"]["id"] for q in questions.questions()["questions"]]
    fileops.undo(op.pk)
    unsure.refresh_from_db()
    assert person.pk not in unsure.rejected

    # "Yes": one of her own faces from now on.
    ops.add_faces([unsure.pk], person.pk)
    unsure.refresh_from_db()
    assert (unsure.person_id, unsure.assigned_by) == (person.pk, AssignedBy.OWNER)


def test_no_takes_a_matched_face_back_out(album):
    base = unit(RNG.normal(size=128))
    person = Person.objects.create(name="Jo", confirmed=True)
    for _ in range(4):
        add_face(near(base, 0.03), person, AssignedBy.OWNER)
    matched = add_face(twin_of(base, 0.55), person, AssignedBy.MATCH)
    ops.not_them(matched.pk, person.pk)
    drain()
    matched.refresh_from_db()
    assert matched.person_id is None and person.pk in matched.rejected


def test_questions_api(album, client, settings):
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.create_user(username=settings.GALLERY_USERNAME, password="x" * 20)
    client.force_login(user)
    body = client.get("/api/people/questions").json()
    assert body["questions"] == [] and body["people"] == 0, "nobody confirmed yet: nothing to ask"
    face = Face.objects.first()
    person = person_of("ana0.jpg")
    response = client.post("/api/faces/not-them", {"face": face.pk, "person": person.pk}, content_type="application/json")
    assert response.status_code == 200
