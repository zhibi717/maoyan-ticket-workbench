"""Export boundaries: selected target only, one ticket, no attendee/device data."""
import json
import re
import tempfile
import unittest
from pathlib import Path

from ticketdesk.autox_export import build_script, export_selected, target_from_profile
from ticketdesk.profiles import ProfileStore, validate_profile


PROFILE = {
    'platform': 'maoyan',
    'title': '测试演唱会',
    'keyword': '测试演唱会 广州',
    'city': '广州',
    'date': '2026-12-05 周六 19:00',
    'event_time': '2026-12-05T19:00:00+08:00',
    'sale_time': '2026-10-08T13:00:00+08:00',
    'price': '看台 1380 元',
    'users': ['私密观演人'],
    'serial': 'private-device-serial',
}


class AutoXExportTests(unittest.TestCase):
    def test_target_contains_event_only(self):
        target = target_from_profile(validate_profile(PROFILE))
        self.assertEqual(set(target), {'package', 'titleToken', 'city', 'dateLabels', 'amount'})
        self.assertIn('12月5日', target['dateLabels'])
        self.assertEqual(target['amount'], 1380)

    def test_generated_script_excludes_attendee_and_serial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = ProfileStore(root / 'profiles.json')
            profile = store.save(PROFILE)
            selection = root / 'selected.txt'
            selection.write_text(profile['id'], encoding='utf-8')
            output = root / 'maoyan-probe.js'

            self.assertEqual(export_selected(selection=selection, store=store, output=output), output)
            script = output.read_text(encoding='utf-8')
            self.assertIn('var TARGET = {', script)
            for private in ('私密观演人', 'private-device-serial', 'users', 'serial'):
                self.assertNotIn(private, script)
                self.assertNotIn(json.dumps(private, ensure_ascii=True)[1:-1], script)
            self.assertNotIn('var TARGET = null;', script)
            self.assertIsNone(re.search(
                r'\b(?:click|press|swipe|gesture|setText|input|submit|captureScreen)\s*\(', script
            ))

    def test_wrong_platform_or_ticket_count_rejected(self):
        with self.assertRaises(ValueError):
            build_script({**PROFILE, 'platform': 'damai'})
        with self.assertRaises(ValueError):
            build_script({**PROFILE, 'users': ['甲', '乙']})


if __name__ == '__main__':
    unittest.main()
