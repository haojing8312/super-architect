import pytest

from architecture_workbench.release_defaults import default_release_trust
from architecture_workbench.remote import RemoteClient
from architecture_workbench.store import Store


def test_installed_product_trust_is_local_and_usable(tmp_path):
    store = Store(tmp_path)
    policy = RemoteClient(store).release_policy()
    assert policy.origin == "https://archi.taoofagi.com"
    assert policy.installer_prefix == "ArchitectureWorkbench"
    assert set(policy.public_keys) == {"archi-20261004"}
    # Callers cannot mutate the next installation's pin through a shared dict.
    returned = default_release_trust()
    returned["public_keys"].clear()
    assert default_release_trust()["public_keys"]


@pytest.mark.parametrize("original", [{}, {"origin": "https://custom.example", "public_keys": {"custom": "local-value"}}])
def test_reopening_existing_store_retains_explicit_policy(tmp_path, original):
    store = Store(tmp_path)
    store.save_settings({"release_trust": original, "admin_url": "https://custom.example"})
    reopened = Store(tmp_path)
    assert reopened.get_settings()["release_trust"] == original
    assert reopened.get_settings()["admin_url"] == "https://custom.example"
    if not original:
        with pytest.raises(ValueError, match="可信"):
            RemoteClient(reopened).release_policy()


def test_old_database_without_trust_row_acquires_only_product_pin(tmp_path):
    store = Store(tmp_path)
    store.save_settings({"admin_url": "https://other.example"})
    with store.connection() as db:
        db.execute("DELETE FROM settings WHERE key='release_trust'")
    reopened = Store(tmp_path)
    assert reopened.get_settings()["admin_url"] == "https://other.example"
    assert reopened.get_settings()["release_trust"] == default_release_trust()
