"""Зашифрованные матрицы 2ГИС: шифрование, выбор файла при старте, откат на формулу и scripts/transit_encrypt.py.

Всё на своих ключах во временном каталоге; настоящие матрицы из data/transit проверяет test_transit_real.py.
"""

import json

import pytest
from cryptography.fernet import Fernet

from app.api.deps import build_deps
from app.geo.transit import (
    TransitKeyError,
    build_transit_matrix,
    decrypt_matrix_bytes,
    encrypt_matrix_bytes,
    encrypted_matrix_path,
    encrypted_regions,
    load_region_matrix,
    load_transit_matrices,
    load_transit_matrix,
    parse_transit_matrix,
    save_transit_matrix,
    transit_matrix_path,
)
from app.settings import Settings
from scripts import transit_encrypt as cli
from tests.helpers import at

KEY = Fernet.generate_key().decode()
OTHER_KEY = Fernet.generate_key().decode()
POINTS = [at(0, 0), at(3, 0), at(0, 4)]
MINUTES = [[0, 12, 20], [12, 0, 30], [30, 25, 0]]
LOGGER = "app.geo.transit"


def save_plain(directory, region: str, departure: str = "13:00", minutes=MINUTES):
    """Открытый файл матрицы региона, как его пишет scripts/transit_matrix.py."""
    path = transit_matrix_path(directory, region)
    save_transit_matrix(build_transit_matrix(POINTS, minutes, departure, region=region), path)
    return path


def seal(directory, region: str, key: str = KEY, keep_plain: bool = False, departure: str = "13:00"):
    """Зашифрованный файл региона из точных байтов открытого; открытый по умолчанию убирается, как в репозитории."""
    plain = save_plain(directory, region, departure)
    data = plain.read_bytes()
    encrypted_matrix_path(directory, region).write_bytes(encrypt_matrix_bytes(data, key))
    if not keep_plain:
        plain.unlink()
    return data


def snapshot(directory) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in sorted(directory.iterdir())}


# --- шифрование ---


def test_round_trip_gives_back_the_exact_bytes():
    data = json.dumps({"region": "восток", "minutes": MINUTES}, ensure_ascii=False).encode("utf-8")

    token = encrypt_matrix_bytes(data, KEY)
    again = encrypt_matrix_bytes(data, KEY)

    assert decrypt_matrix_bytes(token, KEY) == data == decrypt_matrix_bytes(again, KEY)
    # Токен каждый раз другой, а открытых байтов в нём нет.
    assert token != again and data not in token and "восток".encode() not in token


def test_a_wrong_or_broken_key_raises_without_the_key_in_the_message():
    token = encrypt_matrix_bytes(b"{}", KEY)
    for key in (OTHER_KEY, "не-ключ", KEY[:-4]):
        with pytest.raises(TransitKeyError) as error:
            decrypt_matrix_bytes(token, key)
        assert KEY not in str(error.value) and key not in str(error.value)
        assert error.value.__cause__ is None and error.value.__suppress_context__
    with pytest.raises(TransitKeyError):
        decrypt_matrix_bytes(token[:-8], KEY)


# --- какой файл берёт сервис ---


def test_the_encrypted_file_is_preferred_when_the_key_is_set(tmp_path, caplog):
    # Открытый файл рядом старше: у него другое время выезда. Берётся зашифрованный.
    seal(tmp_path, "east", departure="13:00")
    save_plain(tmp_path, "east", departure="09:00")

    with caplog.at_level("WARNING", logger=LOGGER):
        loaded = load_transit_matrices(tmp_path, KEY)

    assert [(matrix.region, matrix.departure) for matrix in loaded] == [("east", "13:00")]
    assert loaded[0].matches(POINTS) and loaded[0].minutes == MINUTES
    assert caplog.records == []


def test_the_plaintext_is_used_when_there_is_no_encrypted_file(tmp_path, caplog):
    save_plain(tmp_path, "east")

    with caplog.at_level("WARNING", logger=LOGGER):
        without_key = load_transit_matrices(tmp_path)
        with_key = load_transit_matrices(tmp_path, KEY)

    assert without_key == with_key == [load_transit_matrix(transit_matrix_path(tmp_path, "east"))]
    assert caplog.records == []


@pytest.mark.parametrize("keep_plain", [False, True], ids=["только .enc", ".enc и открытый"])
def test_a_missing_key_falls_back_to_the_formula_with_a_warning(tmp_path, caplog, keep_plain):
    seal(tmp_path, "east", keep_plain=keep_plain)
    before = snapshot(tmp_path)

    with caplog.at_level("WARNING", logger=LOGGER):
        loaded = load_transit_matrices(tmp_path, None)

    # Как без матрицы региона вовсе: пустой каталог даёт то же самое.
    assert loaded == load_transit_matrices(tmp_path / "пусто") == []
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "east" in message and "east.json.enc" in message and "TRANSIT_KEY не задан" in message
    assert "формула" in message
    assert snapshot(tmp_path) == before


@pytest.mark.parametrize("key", [OTHER_KEY, "не-ключ"], ids=["чужой ключ", "не ключ Fernet"])
def test_a_wrong_key_falls_back_to_the_formula_with_a_warning(tmp_path, caplog, key):
    seal(tmp_path, "east", keep_plain=True)
    save_plain(tmp_path, "north_west")

    with caplog.at_level("WARNING", logger=LOGGER):
        loaded = load_transit_matrices(tmp_path, key)

    # Регион с нерасшифрованной матрицей считает формула, остальные регионы на месте.
    assert [matrix.region for matrix in loaded] == ["north_west"]
    assert len(caplog.records) == 1
    message = caplog.records[0].getMessage()
    assert "east" in message and "не расшифровать" in message and "формула" in message
    assert KEY not in caplog.text and key not in caplog.text


def test_a_broken_encrypted_file_falls_back_with_a_warning(tmp_path, caplog):
    encrypted_matrix_path(tmp_path, "east").write_bytes(b"not a token")
    encrypted_matrix_path(tmp_path, "south").write_bytes(encrypt_matrix_bytes(b"{not json", KEY))

    with caplog.at_level("WARNING", logger=LOGGER):
        assert load_transit_matrices(tmp_path, KEY) == []

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 2
    assert "east" in messages[0] and "не расшифровать" in messages[0]
    assert "south" in messages[1] and "это не матрица" in messages[1]


def test_regions_keep_the_order_of_the_plaintext_file_names(tmp_path):
    # «a-b.json» идёт раньше «a.json» («-» меньше «.»), хотя «a» раньше «a-b»: порядок прежний, по именам файлов.
    seal(tmp_path, "a-b")
    save_plain(tmp_path, "a")
    seal(tmp_path, "b")

    assert [matrix.region for matrix in load_transit_matrices(tmp_path, KEY)] == ["a-b", "a", "b"]
    assert encrypted_regions(tmp_path) == ["a-b", "b"]
    assert encrypted_regions(tmp_path / "нет-каталога") == []


def test_the_decrypted_matrix_never_touches_the_disk(tmp_path):
    data = seal(tmp_path, "east")
    before = snapshot(tmp_path)

    matrix = load_region_matrix(tmp_path, "east", KEY)

    assert matrix == parse_transit_matrix(data.decode("utf-8"))
    assert snapshot(tmp_path) == before and list(before) == ["east.json.enc"]


def test_settings_read_the_key_hide_it_and_build_deps_decrypts_with_it(tmp_path):
    env = {"DATA_DIR": str(tmp_path), "GEOCODER": "cache-only", "SOLVER_WORKERS": "1"}
    settings = Settings.from_env({**env, "TRANSIT_KEY": f" {KEY} "})
    assert settings.transit_key == KEY and KEY not in repr(settings)
    assert Settings.from_env(env).transit_key is None
    seal(settings.transit_dir, "east")

    loaded = build_deps(settings).ingest.planning.transit
    assert [matrix.region for matrix in loaded] == ["east"]
    assert list(build_deps(Settings.from_env(env)).ingest.planning.transit) == []


# --- scripts/transit_encrypt.py ---


def run_encrypt(tmp_path, *argv, key: str | None = KEY):
    env = {"DATA_DIR": str(tmp_path)}
    if key is not None:
        env["TRANSIT_KEY"] = key
    return cli.main(list(argv), env=env)


def test_the_script_encrypts_every_region_and_leaves_unchanged_files_alone(tmp_path, capsys):
    directory = tmp_path / "transit"
    plains = {region: save_plain(directory, region).read_bytes() for region in ("east", "north_west")}

    assert run_encrypt(tmp_path) == 0

    out = capsys.readouterr().out
    assert out.count("зашифрован —") == 2 and "с ошибкой: 0" in out and "--build backend" in out
    for region, data in plains.items():
        assert decrypt_matrix_bytes(encrypted_matrix_path(directory, region).read_bytes(), KEY) == data
    assert KEY not in out and "13:00" not in out
    sealed = snapshot(directory)

    # Второй запуск ничего не переписывает: токен Fernet каждый раз другой, и в git была бы лишняя правка.
    assert run_encrypt(tmp_path) == 0
    assert capsys.readouterr().out.count("без изменений") == 2
    assert snapshot(directory) == sealed

    # Пересчитанная матрица шифруется заново.
    changed = save_plain(directory, "east", departure="09:00").read_bytes()
    assert run_encrypt(tmp_path) == 0
    out = capsys.readouterr().out
    assert "регион east: зашифрован" in out and "регион north_west: без изменений" in out
    assert decrypt_matrix_bytes(encrypted_matrix_path(directory, "east").read_bytes(), KEY) == changed
    assert not list(directory.glob("*.tmp"))


def test_the_script_needs_a_proper_key(tmp_path, capsys):
    save_plain(tmp_path / "transit", "east")
    assert run_encrypt(tmp_path, key=None) == 2
    assert "TRANSIT_KEY" in capsys.readouterr().err
    assert run_encrypt(tmp_path, key="не-ключ") == 2
    err = capsys.readouterr().err
    assert "не ключ Fernet" in err and "не-ключ" not in err
    assert encrypted_regions(tmp_path / "transit") == []


def test_the_script_does_not_reencrypt_under_another_key_unless_asked(tmp_path, capsys):
    directory = tmp_path / "transit"
    data = seal(directory, "east", key=OTHER_KEY, keep_plain=True)
    sealed = snapshot(directory)

    assert run_encrypt(tmp_path) == 1
    err = capsys.readouterr().err
    assert "не расшифровывается этим ключом — не тронут" in err and "--rekey" in err
    assert snapshot(directory) == sealed

    assert run_encrypt(tmp_path, "--rekey") == 0
    assert decrypt_matrix_bytes(encrypted_matrix_path(directory, "east").read_bytes(), KEY) == data


def test_the_script_does_not_encrypt_what_is_not_a_matrix(tmp_path, capsys):
    directory = tmp_path / "transit"
    directory.mkdir()
    (directory / "broken.json").write_text("{не json", encoding="utf-8")
    save_plain(directory, "east")

    assert run_encrypt(tmp_path) == 1
    captured = capsys.readouterr()
    assert "broken.json — не матрица" in captured.err and "регион east: зашифрован" in captured.out
    assert encrypted_regions(directory) == ["east"]


def test_the_script_has_nothing_to_do_without_plaintext(tmp_path, capsys):
    assert run_encrypt(tmp_path) == 0
    assert "шифровать нечего" in capsys.readouterr().out


def test_the_script_checks_encrypted_files_that_have_no_plaintext(tmp_path, capsys):
    # Свежий клон или рабочая копия после слияния: открытых файлов нет, есть только .enc.
    directory = tmp_path / "transit"
    directory.mkdir()
    seal(directory, "east")
    seal(directory, "south", key=OTHER_KEY)
    sealed = snapshot(directory)

    assert run_encrypt(tmp_path) == 1
    captured = capsys.readouterr()
    assert "регион east: east.json.enc расшифровывается этим ключом" in captured.out
    assert "регион south: south.json.enc не расшифровывается этим ключом, открытого файла нет" in captured.err
    assert "регионов: 2, с ошибкой: 1" in captured.out and "--build backend" not in captured.out
    assert snapshot(directory) == sealed

    (directory / "south.json.enc").unlink()
    assert run_encrypt(tmp_path) == 0
    assert "с ошибкой: 0" in capsys.readouterr().out


def test_rekey_refuses_when_an_encrypted_file_has_no_plaintext(tmp_path, capsys):
    # Смена ключа с частью открытых файлов оставила бы в каталоге .enc на двух разных ключах.
    directory = tmp_path / "transit"
    seal(directory, "east", key=OTHER_KEY, keep_plain=True)
    seal(directory, "south", key=OTHER_KEY)
    sealed = snapshot(directory)

    assert run_encrypt(tmp_path, "--rekey") == 1
    err = capsys.readouterr().err
    assert "у регионов south нет открытых" in err and "Ничего не тронуто" in err and "git show" in err
    assert snapshot(directory) == sealed

    # Без открытых файлов вовсе --rekey тоже не проходит молча.
    (directory / "east.json").unlink()
    assert run_encrypt(tmp_path, "--rekey") == 1
    assert "у регионов east, south нет открытых" in capsys.readouterr().err
