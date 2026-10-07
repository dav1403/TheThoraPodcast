"""Episode pages must not change when ANOTHER rav publishes a class.

Measured 07/10/2026: every "chore: update podcast feeds" commit rewrote
~37 500 files, 75/75 sampled episode pages differing only by the '(N)'
episode count of the "Rabbins" nav dropdown. Episode pages now render that
dropdown without counts; channel/speaker pages keep them.
"""


def _nav(gen, counts):
    gen.NAV_ITEMS.clear()
    gen.NAV_ITEMS.extend(
        {"name": f"Rav {i}", "slug": f"rav-{i}", "count": n} for i, n in enumerate(counts)
    )


def test_episode_page_independent_of_nav_counts(gen, fixture_workdir, all_data):
    ch, entries = all_data[0]
    ep = dict(next(e for e in entries if e.get("video_id")))
    all_channels = [c for c, _ in all_data]
    saved = list(gen.NAV_ITEMS)
    try:
        _nav(gen, [10, 20, 30])
        before = gen.render_episode_page(ep, ch, entries, all_channels)
        _nav(gen, [11, 25, 30])
        after = gen.render_episode_page(ep, ch, entries, all_channels)
    finally:
        gen.NAV_ITEMS[:] = saved
    assert before == after
    assert 'href="../rav-1.html">Rav 1</a>' in after


def test_channel_page_nav_keeps_counts(gen):
    saved = list(gen.NAV_ITEMS)
    try:
        _nav(gen, [7, 0])
        html = gen.render_nav_submenu("")
        assert ">Rav 0 (7)</a>" in html
        assert ">Rav 1</a>" in html  # zero count: no tag (unchanged behaviour)
        assert "(7)" not in gen.render_nav_submenu("../", show_counts=False)
    finally:
        gen.NAV_ITEMS[:] = saved
