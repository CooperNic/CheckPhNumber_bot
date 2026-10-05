from __future__ import annotations

from checkph import enrichment, importer, lookup
from conftest import write_snapshot

R1 = '495;1000000;1009999;10000;ПАО "МГТС";г. Москва;Город Москва;7710016640'
R2 = '495;1010000;1010000;1;ООО "Связь";г. Москва;Город Москва;7700000001'
R3 = '900;0000000;0061999;62000;ООО "Т2 МОБАЙЛ";Краснодарский край;Краснодарский край;7743895280'


def test_normalize():
    assert lookup.normalize("79001234567") == 79001234567
    assert lookup.normalize("+7 (900) 123-45-67") == 79001234567
    assert lookup.normalize("8 900 123 45 67") == 79001234567
    assert lookup.normalize("9001234567") is None
    assert lookup.normalize("abc") is None
    assert lookup.normalize("7900123456x") is None
    assert lookup.normalize("49512345678") is None


def test_region_for_display_falls_back_to_gar():
    assert lookup.region_for_display("г. Москва", "Город Москва") == "г. Москва"
    assert lookup.region_for_display("-", "Краснодарский край") == "Краснодарский край"
    assert lookup.region_for_display(" ", "Ростовская область") == "Ростовская область"


def test_split_number():
    assert lookup.split_number(74951234567) == (495, 1234567)
    assert lookup.split_number(79000000005) == (900, 5)


def test_resolve_hit_miss_and_boundaries(con, snapshots):
    importer.import_snapshot(con, write_snapshot(snapshots, "a", [R1, R2, R3]), "2024-01-01")

    hit = lookup.resolve(con, 74951005000)
    assert hit is not None
    assert (hit.operator_name, hit.region, hit.inn, hit.since) == ('ПАО "МГТС"', "г. Москва", "7710016640", "2024-01-01")
    assert (hit.from_number, hit.to_number) == (1000000, 1009999)

    assert lookup.resolve(con, 74951000000).operator_name == 'ПАО "МГТС"'   # нижняя граница
    assert lookup.resolve(con, 74951009999).operator_name == 'ПАО "МГТС"'   # верхняя граница
    assert lookup.resolve(con, 74951010000).operator_name == 'ООО "Связь"'  # диапазон из одного номера
    assert lookup.resolve(con, 74951010001) is None                          # сразу за диапазоном
    assert lookup.resolve(con, 74950999999) is None                          # перед диапазоном
    assert lookup.resolve(con, 74961005000) is None                          # другой код
    assert lookup.resolve(con, 79000000005).operator_name == 'ООО "Т2 МОБАЙЛ"'


def test_resolve_uses_only_current_version(con, snapshots):
    importer.import_snapshot(con, write_snapshot(snapshots, "a", [R1]), "2024-01-01")
    changed = R1.replace("7710016640", "7700000009").replace('ПАО "МГТС"', 'АО "Новый"')
    importer.import_snapshot(con, write_snapshot(snapshots, "b", [changed]), "2024-02-01")

    hit = lookup.resolve(con, 74951005000)
    assert (hit.operator_name, hit.since) == ('АО "Новый"', "2024-02-01")


def test_history_shows_versions_and_gaps(con, snapshots):
    importer.import_snapshot(con, write_snapshot(snapshots, "a", [R1]), "2024-01-01")
    changed = R1.replace("7710016640", "7700000009").replace('ПАО "МГТС"', 'АО "Новый"')
    importer.import_snapshot(con, write_snapshot(snapshots, "b", [changed]), "2024-02-01")
    importer.import_snapshot(con, write_snapshot(snapshots, "c", []), "2024-03-01")   # диапазон исчез

    hist = lookup.history(con, 74951005000)
    assert [(h.operator_name, h.since, h.until) for h in hist] == [
        ('ПАО "МГТС"', "2024-01-01", "2024-02-01"),
        ('АО "Новый"', "2024-02-01", "2024-03-01"),
    ]
    assert lookup.resolve(con, 74951005000) is None
    assert lookup.history(con, 74960000000) == []


def test_resolve_includes_enrichment(con, snapshots):
    importer.import_snapshot(con, write_snapshot(snapshots, "a", [R1]), "2024-01-01")
    number = 74951005000

    plain = lookup.resolve(con, number)
    assert (plain.owner_name, plain.is_spam) == (None, None)

    enrichment.record_owner_observation(con, number, "tbank", "Иванов И.", "person", observed_at="2024-01-10 00:00:00", source_kind="bank")
    enrichment.record_owner_observation(con, number, "user_report", "Петров П.", "person", observed_at="2024-01-20 00:00:00")
    enrichment.record_spam_observation(con, number, "kaspersky", True, category="telemarketing")

    rich = lookup.resolve(con, number)
    assert (rich.owner_name, rich.owner_source, rich.is_spam) == ("Петров П.", "user_report", True)
