from app.i18n import T, t, menu_key

BAD = (chr(0x2014), chr(0x2013))


def test_same_keys():
    assert set(T["ro"]) == set(T["ru"])


def test_no_long_dashes():
    for lang in T.values():
        for v in lang.values():
            assert not any(b in v for b in BAD), v


def test_menu_key_both_langs():
    assert menu_key(T["ro"]["m_votes"]) == "m_votes"
    assert menu_key(T["ru"]["m_dues"]) == "m_dues"
    assert menu_key("altceva") is None


def test_format():
    assert "Ion" in t("ru", "welcome", name="Ion", garages="1, 2")
