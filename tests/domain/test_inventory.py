

def test_registry_less_entities_of_a_previous_boot_are_tombstoned_on_load():
    from custom_components.ha_sentinel.core.inventory import EntityRecord, Inventory

    old = EntityRecord(uid="old", domain="conversation", current_alias="conversation.ha",
                       first_seen_us=1, last_seen_us=1)
    kept = EntityRecord(uid="reg", domain="light", current_alias="light.a", first_seen_us=1,
                        last_seen_us=1, registry_id="r1")
    inv = Inventory("boot-2")
    changes = inv.load([old, kept], [], now_us=100)
    assert [c.entity.uid for c in changes] == ["old"] and old.deleted_at_us == 100
    assert inv.uid_for_alias("light.a") == "reg"
    uid, _ = inv.observe(entity_id="conversation.ha", registry_id=None, device_id=None,
                         now_us=101)
    assert uid != "old" and [e.uid for e in inv.active_entities()] == ["reg", uid]
