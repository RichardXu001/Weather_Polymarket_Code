import unittest
from unittest.mock import patch, MagicMock
from datetime import datetime, timezone, timedelta
from trading_hub import TradingHub

class TestMarketSwitch(unittest.TestCase):
    def setUp(self):
        self.hub = TradingHub()
        self.seoul_template = "highest-temperature-in-seoul-on-{month}-{day}-{year}"
        self.london_template = "highest-temperature-in-london-on-{month}-{day}-{year}"

    def test_slug_format(self):
        # 验证 2月6日 格式
        dt = datetime(2026, 2, 6, 12, 0, 0, tzinfo=timezone.utc)
        slug = self.hub._get_dynamic_slug(self.seoul_template, 9)
        # 注意：hub 内使用的是 datetime.now(timezone.utc)，我们需要 mock 它
        with patch('datetime.datetime') as mock_date:
            mock_date.now.return_value = dt
            slug = self.hub._get_dynamic_slug(self.seoul_template, 9)
            # 首尔过 9 小时还是 2月6日
            self.assertEqual(slug, "highest-temperature-in-seoul-on-february-6-2026")

    def test_seoul_midnight_switch(self):
        """首尔在北京时间 23:00 (UTC 15:00) 跨入次日"""
        # 1. UTC 14:59 -> 首尔 23:59
        dt_before = datetime(2026, 2, 6, 14, 59, 50, tzinfo=timezone.utc)
        # 2. UTC 15:01 -> 首尔 00:01 (翌日)
        dt_after = datetime(2026, 2, 6, 15, 0, 10, tzinfo=timezone.utc)

        with patch('datetime.datetime') as mock_date:
            mock_date.now.return_value = dt_before
            date_before = self.hub._get_local_date(9)
            slug_before = self.hub._get_dynamic_slug(self.seoul_template, 9)
            
            mock_date.now.return_value = dt_after
            date_after = self.hub._get_local_date(9)
            slug_after = self.hub._get_dynamic_slug(self.seoul_template, 9)

            print(f"\n[Seoul Test] Before: {date_before} | Slug: {slug_before}")
            print(f"[Seoul Test] After:  {date_after} | Slug: {slug_after}")

            self.assertEqual(date_before, "2026-02-06")
            self.assertEqual(date_after, "2026-02-07")
            self.assertIn("february-7-2026", slug_after)

    def test_london_midnight_switch(self):
        """伦敦在北京时间 次日 08:00 (UTC 00:00) 跨入次日"""
        # 1. UTC 23:59 (北京时间次日 07:59) -> 伦敦 23:59
        dt_before = datetime(2026, 2, 6, 23, 59, 50, tzinfo=timezone.utc)
        # 2. UTC 00:01 (北京时间次日 08:01) -> 伦敦 00:01 (翌日)
        dt_after = datetime(2026, 2, 7, 0, 0, 10, tzinfo=timezone.utc)

        with patch('datetime.datetime') as mock_date:
            mock_date.now.return_value = dt_before
            date_before = self.hub._get_local_date(0)
            slug_before = self.hub._get_dynamic_slug(self.london_template, 0)
            
            mock_date.now.return_value = dt_after
            date_after = self.hub._get_local_date(0)
            slug_after = self.hub._get_dynamic_slug(self.london_template, 0)

            print(f"\n[London Test] Before: {date_before} | Slug: {slug_before}")
            print(f"[London Test] After:  {date_after} | Slug: {slug_after}")

            self.assertEqual(date_before, "2026-02-06")
            self.assertEqual(date_after, "2026-02-07")
            self.assertIn("february-7-2026", slug_after)

if __name__ == "__main__":
    unittest.main()
