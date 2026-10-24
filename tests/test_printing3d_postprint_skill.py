"""Regression: printer FINISH is not mechanical/product acceptance."""

from agent_skills.registry import SkillRegistry


def test_3d_preflight_requires_post_print_functional_acceptance():
    body = SkillRegistry().load("3d-print-preflight", role="alice").body
    assert "`print_finished_unverified`" in body
    assert "`functional_failed`" in body
    assert "print-in-place" in body.lower()
    assert "do not automatically reprint" in body.lower()


def test_watch_never_converts_finish_to_verified_clip():
    body = SkillRegistry().load("a1-mini-print-watch", role="alice").body
    assert "`FINISH`" in body
    assert "`print_finished_unverified`" in body
    assert "`functional_failed`" in body
    assert "mechanical" in body.lower()


def test_bagclip_case_retains_author_guidance_and_unknowns():
    body = SkillRegistry().load("3d-print-preflight", role="alice").body
    assert "squinn" in body
    assert "0.2 mm" in body
    assert "0.3 mm" in body
    assert "0.4 mm" in body
    assert "5 wall loops" in body.lower()
    assert "source slicer settings" in body.lower()
