"""PC checks for destructive-action guards; Android UI is tested separately."""
import importlib.machinery
import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch


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

    def test_converted_untrimmed_video_can_bind_original_with_negative_times(self):
        path = "/DCIM/video.mp4"
        batch = plugin.SendBatch(1, 123, [{"send_paths": {path}}])
        # Native code concatenates duration directly after lastModified.
        original = path + "53266_17916600000004000_-1_-1"
        self.assertTrue(batch.bind(1, 123, -7, ["/cache/converted.mp4", original]))
        self.assertTrue(batch.acknowledge(1, 123, -7))
        for value in [path + ".backup53266_17916600000004000_-1_-1",
                      path + "53266_17916600000004000_-1_-1/other"]:
            self.assertFalse(plugin.matches_original_path(value, path))

    def test_trimmed_muted_video_and_photo_require_both_acknowledgements(self):
        video, photo = "/DCIM/video.mp4", "/DCIM/photo.jpg"
        batch = plugin.SendBatch(1, 123, [{"send_paths": {video}}, {"send_paths": {photo}}])
        self.assertTrue(batch.bind(1, 123, -7, [video + "53266_17916600000002500_500000_-1_m_480"]))
        self.assertTrue(batch.bind(1, 123, -8, [photo + "1234_1791660000000"]))
        self.assertFalse(batch.acknowledge(1, 123, -7))
        self.assertTrue(batch.acknowledge(1, 123, -8))

    def test_upload_hook_passes_video_editor_original_path(self):
        instance = plugin.BestGalleryPlugin()
        tracked = []
        instance._track_message = lambda *args: tracked.append(args)
        owner = object()
        obj = types.SimpleNamespace(messageOwner=owner, scheduled=False,
                                    videoEditedInfo=types.SimpleNamespace(originalPath="/DCIM/video.mp4"))
        instance._uploading_message(types.SimpleNamespace(thisObject="helper", args=[obj]))
        self.assertEqual(tracked, [("helper", owner, False, "/DCIM/video.mp4")])

    def test_untracked_send_releases_lock_even_after_picker_closed(self):
        instance = plugin.BestGalleryPlugin()
        batch = self.batch(1)
        batch.row = {"alert": types.SimpleNamespace(isShowing=lambda: False)}
        instance._batch = batch
        stopped, notices = [], []
        instance._stop_observing = lambda: stopped.append(True)
        instance._bulletin = lambda *args: notices.append(args)
        instance._request_delete = lambda *args: self.fail("Timeout tried deletion")
        instance._expire(batch)
        self.assertIsNone(instance._batch)
        self.assertEqual(stopped, [True])
        self.assertIn("сохранены", notices[0][1])

    def test_long_tracked_upload_keeps_waiting_without_timeout_deletion(self):
        instance = plugin.BestGalleryPlugin()
        batch = self.batch(1)
        batch.bind(1, 123, -1, ["/DCIM/photo0.jpg"])
        instance._batch = batch
        instance._stop_observing = lambda: self.fail("Valid upload was cancelled")
        timers = []
        with patch.object(plugin, "run_on_ui_thread", lambda f, delay: timers.append(delay)):
            instance._expire(batch)
        self.assertIs(instance._batch, batch)
        self.assertEqual(timers, [60000])

    def test_stale_timeout_cannot_cancel_a_new_batch(self):
        instance = plugin.BestGalleryPlugin()
        previous, current = self.batch(1), self.batch(1)
        instance._batch = current
        instance._stop_observing = lambda: self.fail("Old timeout cancelled new send")
        instance._expire(previous)
        self.assertIs(instance._batch, current)

    def test_cancelled_upload_releases_lock_and_late_ack_cannot_delete(self):
        instance = plugin.BestGalleryPlugin()
        batch = self.batch(1)
        batch.bind(1, 123, -7, ["/DCIM/photo0.jpg"])
        instance._batch = batch
        instance._stop_observing = lambda: None
        instance._bulletin = lambda *args: None
        instance._request_delete = lambda *args: self.fail("Cancelled upload tried deletion")
        messenger = types.ModuleType("org.telegram.messenger")
        messenger.NotificationCenter = types.SimpleNamespace(messagesDeleted=1, messageSendError=2,
                                                              messageReceivedByServer=3)
        def ids(*values):
            return types.SimpleNamespace(size=lambda: len(values), get=lambda i: values[i])
        with patch.dict(sys.modules, {"org.telegram.messenger": messenger}):
            for account, scheduled, deleted in [(2, False, -7), (1, True, -7), (1, False, -8)]:
                instance._notification(1, account, [ids(deleted), 0, scheduled])
                self.assertIs(instance._batch, batch)
            instance._notification(1, 1, [ids(-7), 0, False])
            self.assertIsNone(instance._batch)
            instance._notification(3, 1, [-7, 77, None, 123, None, None, False])

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
