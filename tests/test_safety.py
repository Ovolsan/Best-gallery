"""PC checks for destructive-action guards; Android UI is tested separately."""
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import types
import unittest


def load_plugin():
    for module, attributes in {
        "base_plugin": {"BasePlugin": object},
        "android_utils": {"OnClickListener": lambda f: f, "run_on_ui_thread": lambda f, *a: f()},
        "hook_utils": {"get_private_field": lambda o, k: getattr(o, k, None)},
    }.items():
        stub = types.ModuleType(module)
        stub.__dict__.update(attributes)
        sys.modules[module] = stub
    loader = importlib.machinery.SourceFileLoader("gallery_under_test", str(Path(__file__).parents[1] / "best_gallery.plugin"))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    plugin = importlib.util.module_from_spec(spec)
    loader.exec_module(plugin)
    return plugin


plugin = load_plugin()


class SafetyTests(unittest.TestCase):
    def batch(self, count=3):
        return plugin.SendBatch(1, 123, [{"send_paths": {f"/DCIM/photo{i}.jpg"}} for i in range(count)])

    def test_album_requires_all_distinct_acknowledgements(self):
        batch = self.batch()
        for i in range(3):
            self.assertTrue(batch.bind(1, 123, -i - 1, [f"/DCIM/photo{i}.jpg100_123456"]))
        self.assertFalse(batch.acknowledge(1, 123, -2))
        self.assertFalse(batch.acknowledge(1, 123, -2))
        self.assertFalse(batch.acknowledge(1, 123, -1))
        self.assertTrue(batch.acknowledge(1, 123, -3))

    def test_other_account_chat_schedule_and_edit_cannot_delete(self):
        batch = self.batch(1)
        path = ["/DCIM/photo0.jpg"]
        for account, dialog, msg, scheduled in [(2, 123, -1, False), (1, 456, -1, False),
                                                (1, 123, -1, True), (1, 123, 100, False)]:
            self.assertFalse(batch.bind(account, dialog, msg, path, scheduled))
        self.assertTrue(batch.bind(1, 123, -1, path))
        for account, dialog, msg, scheduled in [(2, 123, -1, False), (1, 456, -1, False),
                                                (1, 123, -1, True), (1, 123, -2, False)]:
            self.assertFalse(batch.acknowledge(account, dialog, msg, scheduled))

    def test_original_path_suffix_and_prefix_collision(self):
        path = "/DCIM/photo.jpg"
        for suffix in ["", "123_456", "123_456_5000_0_2000", "123_456_5000_0_2000_m_720"]:
            self.assertTrue(plugin.matches_original_path(path + suffix, path))
        for suffix in [".backup", "2.jpg", "/other", "_123", "123_", "123_456/other"]:
            self.assertFalse(plugin.matches_original_path(path + suffix, path))

    def test_same_original_cannot_satisfy_two_album_entries(self):
        batch = self.batch(2)
        self.assertTrue(batch.bind(1, 123, -1, ["/DCIM/photo0.jpg"]))
        self.assertFalse(batch.bind(1, 123, -2, ["/DCIM/photo0.jpg"]))
        self.assertFalse(batch.acknowledge(1, 123, -1))

    def test_folder_grant_cannot_escape_its_directory_or_volume(self):
        self.assertTrue(plugin.tree_contains("primary:Pictures", "primary:Pictures/Demo/photo.jpg"))
        for document in ["primary:Pictures2/photo.jpg", "primary:DCIM/photo.jpg",
                         "FFFF-AAAA:Pictures/photo.jpg", "primary:Pictures/../DCIM/photo.jpg",
                         "primary:Pictures", None]:
            self.assertFalse(plugin.tree_contains("primary:Pictures", document))

    def test_document_mapping_respects_storage_root_boundaries(self):
        for path in ["/storage/emulated/0/Pictures/a.jpg", "/sdcard/Pictures/a.jpg"]:
            self.assertEqual(plugin.storage_document_id(path, "/storage/emulated/0"), "primary:Pictures/a.jpg")
        self.assertEqual(plugin.storage_document_id("/storage/FFFF-AAAA/DCIM/a.jpg", "/storage/emulated/0"),
                         "FFFF-AAAA:DCIM/a.jpg")
        for path in ["/data/user/0/private.jpg", "/storage/emulated/01/a.jpg", "/sdcard/../private.jpg"]:
            self.assertIsNone(plugin.storage_document_id(path, "/storage/emulated/0"))

    def test_album_spanning_multiple_telegram_groups(self):
        batch = self.batch(23)
        for i in range(23):
            batch.bind(1, 123, -i - 1, [f"/DCIM/photo{i}.jpg"])
        for i in range(22):
            self.assertFalse(batch.acknowledge(1, 123, -i - 1))
        self.assertTrue(batch.acknowledge(1, 123, -23))

    def test_system_cancel_preserves_selection_and_never_finishes_deletion(self):
        instance = plugin.BestGalleryPlugin()
        request = {"entries": [object()], "row": object()}
        instance._delete_request = request
        notices = []
        instance._bulletin = lambda *args: notices.append(args)
        instance._finish_delete = lambda *args: self.fail("Cancellation tried to clear selection")
        instance._activity_result(types.SimpleNamespace(args=[plugin.DELETE_REQUEST, 0, None]))
        self.assertIsNone(instance._delete_request)
        self.assertIn("сохранены", notices[0][1])

    def test_unrelated_activity_result_does_not_finish_our_request(self):
        instance = plugin.BestGalleryPlugin()
        request = {"entries": [object()]}
        instance._delete_request = request
        instance._finish_delete = lambda *args: self.fail("Unrelated result caused deletion")
        instance._activity_result(types.SimpleNamespace(args=[99, -1, None]))
        self.assertIs(instance._delete_request, request)

    def test_folder_picker_cancel_does_not_delete_or_clear_selection(self):
        instance = plugin.BestGalleryPlugin()
        instance._delete_request = {"entries": [object()]}
        instance._bulletin = lambda *args: None
        instance._finish_delete = lambda *args: self.fail("Folder cancel tried deletion")
        instance._activity_result(types.SimpleNamespace(args=[plugin.FOLDER_REQUEST, 0, None]))
        self.assertIsNone(instance._delete_request)


if __name__ == "__main__":
    unittest.main()
