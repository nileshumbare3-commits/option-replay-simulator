import unittest
from unittest.mock import MagicMock, patch
from datetime import datetime, timedelta
import pandas as pd

from fetch_historical import parse_date, get_date_chunks, download_historical_data

class TestHistoricalDataExtractor(unittest.TestCase):

    def test_parse_date(self):
        # Test standard YYYY-MM-DD format
        dt = parse_date("2023-05-15")
        self.assertEqual(dt, datetime(2023, 5, 15))

        # Test ISO format
        dt_iso = parse_date("2023-05-15T12:30:00")
        self.assertEqual(dt_iso, datetime(2023, 5, 15, 12, 30, 0))

        # Test invalid date string
        with self.assertRaises(ValueError):
            parse_date("invalid-date-format")

    def test_get_date_chunks(self):
        start_dt = datetime(2023, 1, 1)
        end_dt = datetime(2023, 2, 10) # 40 days total

        # Split into 14-day chunks
        chunks = get_date_chunks(start_dt, end_dt, chunk_days=14)

        # Expect 3 chunks:
        # Chunk 1: Jan 1 to Jan 15 (14 days after start_dt)
        # Chunk 2: Jan 15 + 1s to Jan 29
        # Chunk 3: Jan 29 + 1s to Feb 10
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0][0], start_dt)
        self.assertEqual(chunks[-1][1], end_dt)

    @patch('fetch_historical.BreezeClient')
    def test_download_historical_data(self, MockBreezeClient):
        # Setup mock instance
        mock_client = MagicMock()
        MockBreezeClient.return_value = mock_client

        # Mock historical method return value
        # Year 2023 mock data
        mock_data_2023 = [
            {
                'datetime': '2023-06-15 09:15:00',
                'open': '18000', 'high': '18050', 'low': '17990', 'close': '18020',
                'volume': '500', 'open_interest': '1000'
            },
            {
                'datetime': '2023-06-15 09:16:00',
                'open': '18020', 'high': '18030', 'low': '18010', 'close': '18015',
                'volume': '300', 'open_interest': '1000'
            }
        ]
        # Year 2024 mock data
        mock_data_2024 = [
            {
                'datetime': '2024-01-10 12:00:00',
                'open': '21500', 'high': '21550', 'low': '21480', 'close': '21510',
                'volume': '700', 'open_interest': '1500'
            }
        ]

        # Mock client historical return value to toggle based on date
        def side_effect(*args, **kwargs):
            from_date = kwargs.get('from_date', '')
            to_date = kwargs.get('to_date', '')
            if '2024' in to_date:
                return mock_data_2024
            elif '2023' in from_date:
                return mock_data_2023
            return []

        mock_client.historical.side_effect = side_effect

        # Run downloader covering parts of 2023 and 2024
        year_wise_dfs = download_historical_data(
            api_key="mock_key",
            session_token="mock_session",
            stock_code="NIFTY",
            exchange_code="NFO",
            product_type="options",
            start_date="2023-06-15",
            end_date="2024-01-10",
            interval="1minute",
            chunk_days=180 # Large chunk days so we fetch all in two queries/chunks
        )

        # Check that we got data grouped for both years
        self.assertIn(2023, year_wise_dfs)
        self.assertIn(2024, year_wise_dfs)

        # Check row counts
        self.assertEqual(len(year_wise_dfs[2023]), 2)
        self.assertEqual(len(year_wise_dfs[2024]), 1)

        # Verify columns numeric parsing
        df_2023 = year_wise_dfs[2023]
        self.assertEqual(df_2023.iloc[0]['close'], 18020.0)
        self.assertEqual(df_2023.iloc[0]['volume'], 500)

if __name__ == '__main__':
    unittest.main()
