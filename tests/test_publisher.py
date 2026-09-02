"""Offline regression tests; external station and network clients are mocked."""
import csv
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import types
import unittest
from datetime import datetime
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
client_module = types.ModuleType('paho.mqtt.client')
client_module.Client = Mock
client_module.MQTT_ERR_SUCCESS = 0
external_modules = {
    'paho': types.ModuleType('paho'),
    'paho.mqtt': types.ModuleType('paho.mqtt'),
    'paho.mqtt.client': client_module,
    'requests': Mock(),
    'pyvantagepro': Mock(),
}
spec = importlib.util.spec_from_file_location('publisher', ROOT / 'vantage-publisher.py')
publisher = importlib.util.module_from_spec(spec)
with patch.dict(sys.modules, external_modules):
    spec.loader.exec_module(publisher)


class PublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        publisher.mqtt_pending.clear()
        publisher.mqtt_online = False
        publisher.offline_queue = publisher.OfflineQueueSQLite(str(self.root / 'queue.sqlite'))

    def test_delivery_is_persistent_until_acknowledgment(self):
        queue = publisher.offline_queue
        queue.enqueue('station', 'packet', qos=1)
        info = Mock(rc=0)
        info.is_published.return_value = False
        client = Mock()
        client.publish.return_value = info
        publisher.on_connect(client, None, None, 0)
        client.publish.assert_not_called()
        publisher.flush_offline_queue(client)
        publisher.flush_offline_queue(client)
        self.assertEqual(queue.size(), 1)
        client.publish.assert_called_once_with('station', 'packet', qos=1, retain=False)
        info.is_published.return_value = True
        publisher.flush_offline_queue(client)
        self.assertEqual(queue.size(), 0)

    def test_failed_publish_keeps_packet_for_retry(self):
        publisher.offline_queue.enqueue('station', 'packet')
        publisher.mqtt_online = True
        client = Mock()
        client.publish.return_value = Mock(rc=4)
        publisher.flush_offline_queue(client)
        self.assertEqual(publisher.offline_queue.size(), 1)
        self.assertFalse(publisher.mqtt_pending)
        self.assertTrue(publisher.mqtt_online)

    def test_rejected_connection_is_not_online(self):
        publisher.on_connect(Mock(), None, None, 5)
        self.assertFalse(publisher.mqtt_online)

    def test_expired_records_are_not_replayed_after_restart(self):
        publisher.offline_queue.enqueue('station', 'expired')
        with sqlite3.connect(publisher.offline_queue.db_path) as connection:
            connection.execute('UPDATE queue SET ts = 0')
        self.assertEqual(publisher.offline_queue.peek_batch(), [])

    def test_csv_schema_expands_without_losing_rows(self):
        config = {'pathStorage': str(self.root), 'uuid': 'station'}
        publisher.save_data_to_csv(config, {'Datetime': '2026-09-03T12:00:00Z', 'TempOut': 20})
        publisher.save_data_to_csv(config, {'Datetime': '2026-09-03T12:01:00Z', 'HumOut': 50})
        path = self.root / 'station/2026/09/03/station_20260903Z1200.csv'
        with path.open() as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['TempOut'], '20')
        self.assertEqual(rows[1]['HumOut'], '50')

    def test_failed_schema_replacement_preserves_original_and_skips_append(self):
        path = self.root / 'station/2026/09/03/station_20260903Z1200.csv'
        path.parent.mkdir(parents=True)
        original = 'Datetime,TempOut\n2026-09-03T12:00:00Z,20\n'
        path.write_text(original)
        with patch.object(publisher.os, 'replace', side_effect=OSError('disk failure')):
            publisher.save_data_to_csv(
                {'pathStorage': str(self.root), 'uuid': 'station'},
                {'Datetime': '2026-09-03T12:01:00Z', 'HumOut': 50},
            )
        self.assertEqual(path.read_text(), original)
        self.assertEqual(list(path.parent.iterdir()), [path])

    def test_empty_storage_path_does_not_write_to_working_directory(self):
        with patch.object(publisher.Path, 'mkdir') as mkdir:
            publisher.save_data_to_csv({'pathStorage': ''}, {'TempOut': 20})
        mkdir.assert_not_called()

    def test_dry_mode_serializes_datetime_and_observes_delay(self):
        config_path = self.root / 'config.json'
        config_path.write_text(json.dumps({'uuid': 'station', 'name': 'Test', 'lat': 0, 'lon': 0}))
        args = types.SimpleNamespace(config=str(config_path), parameters=str(self.root / 'missing'),
                                     dry=True, storage=None, mqtt=None, signalk=None)
        stop = threading.Event()
        reader = Mock()
        reader.get_latest.return_value = ({'Datetime': datetime(2026, 9, 3), 'TempOut': 20}, 1, 1)
        with patch.object(publisher, 'parse_args', return_value=args), \
             patch.object(publisher.threading, 'Event', return_value=stop), \
             patch.object(stop, 'wait', side_effect=lambda timeout: stop.set()) as wait, \
             patch.object(publisher, 'USBReaderThread', return_value=reader), \
             patch.object(publisher, 'install_signal_handlers', return_value={}), \
             patch.object(publisher.logger, 'info') as log:
            publisher.main()
        wait.assert_called_once_with(2.0)
        self.assertTrue(any(str(call.args[0]).startswith('CSV_ROW;') for call in log.call_args_list))


if __name__ == '__main__':
    unittest.main()
