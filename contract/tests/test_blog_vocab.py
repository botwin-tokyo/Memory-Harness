'The blog vocabulary, and the two node kinds one post produces.'

from __future__ import annotations

from memory_contract import (
    ARTIFACT_TAG,
    BLOG_ARTICLE_TAG,
    BLOG_CHUNK_TAG,
    BLOG_SCOPE,
    BLOG_TAG,
    COLLAPSED_TAGS,
    blog_article_lineage_key,
    blog_article_tags,
    blog_chunk_tags,
    blog_lineage_key,
    blog_tags,
    journal_tags,
    node_kind,
)

GUID = "post_0000000000000001"
DATE = "2000-01-01"


def test_the_hub_is_a_blog_and_its_article_is_an_artifact() -> None:
    'One post draws ONE blog node.'
    assert node_kind(blog_tags(GUID, date=DATE)) == "blog"
    assert node_kind(blog_article_tags(GUID, date=DATE)) == "artifact"


def test_a_body_chunk_is_never_a_blog() -> None:
    assert node_kind(blog_chunk_tags(GUID, 0)) != "blog"


def test_body_chunks_collapse_out_of_the_graph() -> None:
    'A nine-minute essay is one node, not fourteen.'
    assert BLOG_CHUNK_TAG in COLLAPSED_TAGS


def test_the_article_half_is_not_collapsed() -> None:
    assert not set(blog_article_tags(GUID, date=DATE)) & set(COLLAPSED_TAGS)


def test_identity_is_the_guid_not_the_slug_or_title() -> None:
    assert blog_lineage_key(GUID) == f"blog:{GUID}"
    assert blog_tags(GUID, date=DATE, slug="one-title") != blog_tags(
        GUID, date=DATE, slug="another-title"
    )
    assert blog_lineage_key(GUID) == blog_lineage_key(GUID)


def test_hub_and_article_keys_are_distinct_but_paired() -> None:
    assert blog_article_lineage_key(GUID) == f"{BLOG_ARTICLE_TAG}:{GUID}"
    assert blog_lineage_key(GUID) != blog_article_lineage_key(GUID)
    assert GUID in blog_lineage_key(GUID)
    assert GUID in blog_article_lineage_key(GUID)


def test_the_date_tag_is_present_because_the_pairing_pass_needs_it() -> None:
    assert f"date:{DATE}" in blog_tags(GUID, date=DATE)
    assert f"date:{DATE}" in blog_article_tags(GUID, date=DATE)


def test_a_blog_hub_and_a_journal_hub_of_the_same_day_agree_on_the_date_tag() -> None:
    'The pairing works by matching this string across both kinds.'
    blog_date = [t for t in blog_tags(GUID, date=DATE) if t.startswith("date:")]
    journal_date = [
        t for t in journal_tags(DATE, "full-day") if t.startswith("date:")
    ]
    assert blog_date == journal_date


def test_category_and_topics_are_slugified() -> None:
    tags = blog_tags(
        GUID, date=DATE, category="Systems / Productivity",
        topics=["Fine Tuning", "Embedding service"],
    )
    assert "category:systems-productivity" in tags
    assert "topic:fine-tuning" in tags
    assert not any(" " in t or "/" in t for t in tags)


def test_empty_topics_do_not_produce_bare_tags() -> None:
    tags = blog_tags(GUID, date=DATE, topics=["", "   ", "Real"])
    assert "topic:" not in tags
    assert "topic:real" in tags


def test_the_article_carries_the_generic_artifact_tag() -> None:
    assert ARTIFACT_TAG in blog_article_tags(GUID, date=DATE)


def test_posts_are_shared_and_never_library() -> None:
    assert BLOG_SCOPE == "shared"


def test_the_guid_is_recoverable_from_every_node_in_the_subtree() -> None:
    'Hub, article and chunk must all be findable from one guid.'
    for tags in (blog_tags(GUID, date=DATE),
                 blog_article_tags(GUID, date=DATE),
                 blog_chunk_tags(GUID, 2)):
        assert f"guid:{GUID}" in tags


def test_blog_tag_and_article_tag_are_not_substrings_of_each_other_in_matching() -> None:
    assert BLOG_ARTICLE_TAG != BLOG_TAG
    assert BLOG_TAG not in set(blog_article_tags(GUID, date=DATE))
