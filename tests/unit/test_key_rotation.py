import pytest
from pathlib import Path

from src.shared.security.key_rotation import KeyRotationError, fernet_for_secret, rotate_header_values, rotate_secret_value


OLD_SECRET = "old-secret-key-abcdefghijklmnopqrstuvwxyz"
NEW_SECRET = "new-secret-key-abcdefghijklmnopqrstuvwxyz"


def test_rotate_secret_value_preserves_old_ciphertext_and_encrypts_plaintext():
    old_ciphertext = fernet_for_secret(OLD_SECRET).encrypt(b"api-key").decode()

    rotated_ciphertext = rotate_secret_value(old_ciphertext, old_secret=OLD_SECRET, new_secret=NEW_SECRET)
    rotated_plaintext = rotate_secret_value("legacy-key", old_secret=OLD_SECRET, new_secret=NEW_SECRET)

    assert fernet_for_secret(NEW_SECRET).decrypt(rotated_ciphertext.encode()).decode() == "api-key"
    assert fernet_for_secret(NEW_SECRET).decrypt(rotated_plaintext.encode()).decode() == "legacy-key"


def test_rotate_headers_preserves_non_strings_and_rotates_marked_values():
    old_header = "enc:v1:" + fernet_for_secret(OLD_SECRET).encrypt(b"header-secret").decode()

    rotated = rotate_header_values({"Authorization": old_header, "Retries": 3}, old_secret=OLD_SECRET, new_secret=NEW_SECRET)

    assert rotated["Retries"] == 3
    assert rotated["Authorization"].startswith("enc:v1:")
    payload = rotated["Authorization"][len("enc:v1:"):]
    assert fernet_for_secret(NEW_SECRET).decrypt(payload.encode()).decode() == "header-secret"


def test_rotate_rejects_unreadable_fernet_looking_value():
    with pytest.raises(KeyRotationError):
        rotate_secret_value("gAAAA-not-a-valid-token", old_secret=OLD_SECRET, new_secret=NEW_SECRET)


def test_rotation_script_adds_project_root_before_importing_app_modules():
    script = Path(__file__).resolve().parents[2] / "scripts" / "rotate_secret_key.py"

    source = script.read_text(encoding="utf-8")

    assert "PROJECT_ROOT = Path(__file__).resolve().parents[1]" in source
    assert "sys.path.insert(0, str(PROJECT_ROOT))" in source
