from __future__ import annotations

from checkph import bot, importer
from checkph.lookup import Attribution
from conftest import write_snapshot

R1 = '495;1000000;1009999;10000;ПАО "МГТС";г. Москва;Город Москва;7710016640'
R_DASH = '900;0000000;0061999;62000;ООО "Т2 МОБАЙЛ";-;Краснодарский край;7743895280'


def make_attr(**overrides) -> Attribution:
    base = dict(
        number=74951005000, code=495, from_number=1000000, to_number=1009999,
        operator_name='ПАО "МГТС"', inn="7710016640", region="г. Москва", gar="Город Москва", since="2024-01-01",
    )
    base.update(overrides)
    return Attribution(**base)


def test_format_answer_plain():
    assert bot.format_answer(make_attr()) == 'ПАО "МГТС" (г. Москва)'


def test_format_answer_dash_region_uses_gar():
    attr = make_attr(region="-", gar="Краснодарский край")
    assert bot.format_answer(attr) == 'ПАО "МГТС" (Краснодарский край)'


def test_format_answer_with_enrichment():
    attr = make_attr(owner_name="ООО Ромашка", owner_kind="company", owner_source="tbank", is_spam=True)
    assert bot.format_answer(attr).splitlines() == [
        'ПАО "МГТС" (г. Москва)',
        "Владелец: ООО Ромашка (источник: tbank)",
        "Отмечен как спам.",
    ]


def test_repository_resolve_and_log(con, snapshots):
    importer.import_snapshot(con, write_snapshot(snapshots, "a", [R1, R_DASH]), "2024-01-01")
    repo = bot.Repository(con)

    attr = repo.resolve(79000000005)
    assert attr is not None and attr.display_region == "Краснодарский край"
    assert repo.resolve(74960000000) is None

    repo.log_lookup(79000000005, "+7 900 000-00-05", 42, True, 1.5)
    repo.log_lookup(None, "hello", 42, False, 0.0)
    rows = con.execute("SELECT number, raw_input, tg_user_id, found FROM lookup_event ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [(79000000005, "+7 900 000-00-05", 42, 1), (None, "hello", 42, 0)]
