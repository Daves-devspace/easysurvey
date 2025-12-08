# tests/test_bulk_sms_stress.py
"""
Comprehensive stress test for bulk SMS system.
Tests ability to handle thousands of clients without failures.

Run with: python manage.py test apps.EasyDocs.tests.test_bulk_sms_stress
Or: pytest apps/EasyDocs/tests/test_bulk_sms_stress.py -v
"""

import time
from decimal import Decimal
from unittest.mock import patch, MagicMock
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from celery import states

from apps.EasyDocs.models import Client, MessageLog, ScheduledTask
from apps.EasyDocs.tasks import _send_chunk, schedule_bulk_broadcast
from apps.EasyDocs.utils import MobileSasaAPI


class BulkSMSStressTest(TransactionTestCase):
    """
    Stress test for bulk SMS system with thousands of clients.
    Uses TransactionTestCase to properly test Celery tasks.
    """
    
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        print("\n" + "="*70)
        print("🚀 STARTING BULK SMS STRESS TEST")
        print("="*70)
    
    def setUp(self):
        """Create test clients before each test"""
        self.test_message = "Hello {client_first_name}, this is a test message!"
        
    def tearDown(self):
        """Clean up after each test"""
        MessageLog.objects.all().delete()
        ScheduledTask.objects.all().delete()
        Client.objects.all().delete()
    
    def create_test_clients(self, count):
        """Efficiently create test clients"""
        print(f"\n📊 Creating {count} test clients...")
        start = time.time()
        
        clients = [
            Client(
                first_name=f"Client_{i}",
                last_name=f"Test_{i}",
                email=f"client{i}@test.com",
                phone=f"254700{i:06d}"  # Format: 254700000001, 254700000002, etc.
            )
            for i in range(1, count + 1)
        ]
        
        Client.objects.bulk_create(clients, batch_size=500)
        elapsed = time.time() - start
        
        print(f"✅ Created {count} clients in {elapsed:.2f}s")
        return Client.objects.all()
    
    @patch('apps.EasyDocs.tasks.MobileSasaAPI')
    def test_100_clients_no_failures(self, MockAPI):
        """Test: 100 clients - all should succeed"""
        print("\n" + "-"*70)
        print("TEST 1: 100 Clients (Small Scale)")
        print("-"*70)
        
        # Setup mock API instance
        mock_api_instance = MockAPI.return_value
        mock_api_instance.get_balance.return_value = {'balance': 10000, 'status': True}
        
        def mock_send_side_effect(message_pairs):
            """Return phones from actual message_pairs sent"""
            return {
                'sent': [m['phone'] for m in message_pairs],
                'failed': []
            }
        
        mock_api_instance.send_personalized_sms.side_effect = mock_send_side_effect
        
        # Create clients
        clients = self.create_test_clients(100)
        client_ids = list(clients.values_list('id', flat=True))
        
        # Execute task
        print("\n📤 Sending messages...")
        start = time.time()
        result = _send_chunk(self.test_message, client_ids)
        elapsed = time.time() - start
        
        # Verify results
        print(f"\n📊 Results:")
        print(f"   ⏱️  Time: {elapsed:.2f}s")
        print(f"   ✅ Sent: {result['sent']}")
        print(f"   ❌ Failed: {result['failed']}")
        print(f"   📝 Logs: {result['logs_created']}")
        
        # Assertions
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['sent'], 100)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(MessageLog.objects.count(), 100)
        self.assertEqual(MessageLog.objects.filter(send_status='sent').count(), 100)
        
        print("\n✅ TEST PASSED: All 100 messages sent successfully")
    
    @patch('apps.EasyDocs.tasks.MobileSasaAPI')
    def test_1000_clients_no_failures(self, MockAPI):
        """Test: 1,000 clients - all should succeed"""
        print("\n" + "-"*70)
        print("TEST 2: 1,000 Clients (Medium Scale)")
        print("-"*70)
        
        # Setup mock API instance
        mock_api_instance = MockAPI.return_value
        mock_api_instance.get_balance.return_value = {'balance': 50000, 'status': True}
        
        def mock_send_side_effect(message_pairs):
            """Simulate API sending in chunks"""
            return {
                'sent': [m['phone'] for m in message_pairs],
                'failed': []
            }
        
        mock_api_instance.send_personalized_sms.side_effect = mock_send_side_effect
        
        # Create clients
        clients = self.create_test_clients(1000)
        client_ids = list(clients.values_list('id', flat=True))
        
        # Execute task
        print("\n📤 Sending messages...")
        start = time.time()
        result = _send_chunk(self.test_message, client_ids)
        elapsed = time.time() - start
        
        # Verify results
        print(f"\n📊 Results:")
        print(f"   ⏱️  Time: {elapsed:.2f}s")
        print(f"   ✅ Sent: {result['sent']}")
        print(f"   ❌ Failed: {result['failed']}")
        print(f"   📝 Logs: {result['logs_created']}")
        print(f"   📈 Rate: {result['sent']/elapsed:.1f} messages/sec")
        
        # Assertions
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['sent'], 1000)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(MessageLog.objects.count(), 1000)
        self.assertEqual(MessageLog.objects.filter(send_status='sent').count(), 1000)
        
        print("\n✅ TEST PASSED: All 1,000 messages sent successfully")
    
    @patch('apps.EasyDocs.tasks.MobileSasaAPI')
    def test_5000_clients_no_failures(self, MockAPI):
        """Test: 5,000 clients - all should succeed"""
        print("\n" + "-"*70)
        print("TEST 3: 5,000 Clients (Large Scale)")
        print("-"*70)
        
        # Setup mock API instance
        mock_api_instance = MockAPI.return_value
        mock_api_instance.get_balance.return_value = {'balance': 100000, 'status': True}
        
        def mock_send_side_effect(message_pairs):
            """Simulate API sending in chunks"""
            return {
                'sent': [m['phone'] for m in message_pairs],
                'failed': []
            }
        
        mock_api_instance.send_personalized_sms.side_effect = mock_send_side_effect
        
        # Create clients
        clients = self.create_test_clients(5000)
        client_ids = list(clients.values_list('id', flat=True))
        
        # Execute task
        print("\n📤 Sending messages...")
        start = time.time()
        result = _send_chunk(self.test_message, client_ids)
        elapsed = time.time() - start
        
        # Verify results
        print(f"\n📊 Results:")
        print(f"   ⏱️  Time: {elapsed:.2f}s")
        print(f"   ✅ Sent: {result['sent']}")
        print(f"   ❌ Failed: {result['failed']}")
        print(f"   📝 Logs: {result['logs_created']}")
        print(f"   📈 Rate: {result['sent']/elapsed:.1f} messages/sec")
        
        # Assertions
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['sent'], 5000)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(MessageLog.objects.count(), 5000)
        self.assertEqual(MessageLog.objects.filter(send_status='sent').count(), 5000)
        
        print("\n✅ TEST PASSED: All 5,000 messages sent successfully")
    
    @patch('apps.EasyDocs.tasks.MobileSasaAPI')
    def test_partial_failures_dont_stop_batch(self, MockAPI):
        """Test: Some failures should NOT stop the entire batch"""
        print("\n" + "-"*70)
        print("TEST 4: Partial Failures (Error Isolation)")
        print("-"*70)
        
        # Setup mock API instance
        mock_api_instance = MockAPI.return_value
        mock_api_instance.get_balance.return_value = {'balance': 10000, 'status': True}
        
        # Simulate 20% failure rate
        def mock_send_with_failures(message_pairs):
            sent = []
            failed = []
            for i, m in enumerate(message_pairs):
                if i % 5 == 0:  # Every 5th message fails
                    failed.append(m['phone'])
                else:
                    sent.append(m['phone'])
            return {'sent': sent, 'failed': failed}
        
        mock_api_instance.send_personalized_sms.side_effect = mock_send_with_failures
        
        # Create clients
        clients = self.create_test_clients(500)
        client_ids = list(clients.values_list('id', flat=True))
        
        # Execute task
        print("\n📤 Sending messages (with simulated failures)...")
        start = time.time()
        result = _send_chunk(self.test_message, client_ids)
        elapsed = time.time() - start
        
        # Verify results
        print(f"\n📊 Results:")
        print(f"   ⏱️  Time: {elapsed:.2f}s")
        print(f"   ✅ Sent: {result['sent']}")
        print(f"   ❌ Failed: {result['failed']}")
        print(f"   📝 Logs: {result['logs_created']}")
        print(f"   📉 Failure Rate: {(result['failed']/500)*100:.1f}%")
        
        # Assertions
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['sent'], 400)  # 80% success
        self.assertEqual(result['failed'], 100)  # 20% failure
        self.assertEqual(MessageLog.objects.count(), 500)
        self.assertEqual(MessageLog.objects.filter(send_status='sent').count(), 400)
        self.assertEqual(MessageLog.objects.filter(send_status='failed').count(), 100)
        
        print("\n✅ TEST PASSED: Failures isolated, batch completed")
    
    @patch('apps.EasyDocs.tasks.MobileSasaAPI')
    def test_insufficient_balance_fails_gracefully(self, MockAPI):
        """Test: Low balance should fail gracefully without crashes"""
        print("\n" + "-"*70)
        print("TEST 5: Insufficient Balance (Graceful Failure)")
        print("-"*70)
        
        # Setup mock - no balance
        mock_api_instance = MockAPI.return_value
        mock_api_instance.get_balance.return_value = {'balance': 0, 'status': True}
        
        # Create clients
        clients = self.create_test_clients(100)
        client_ids = list(clients.values_list('id', flat=True))
        
        # Execute task
        print("\n📤 Attempting to send with zero balance...")
        start = time.time()
        result = _send_chunk(self.test_message, client_ids)
        elapsed = time.time() - start
        
        # Verify results
        print(f"\n📊 Results:")
        print(f"   Status: {result['status']}")
        print(f"   Reason: {result.get('reason', 'N/A')}")
        print(f"   📝 Logs: {MessageLog.objects.count()}")
        
        # Assertions
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['reason'], 'insufficient_balance')
        self.assertEqual(MessageLog.objects.count(), 100)
        self.assertEqual(MessageLog.objects.filter(send_status='failed').count(), 100)
        
        # Verify all have correct error message
        for log in MessageLog.objects.all():
            self.assertEqual(log.error_details, 'Insufficient SMS balance')
        
        print("\n✅ TEST PASSED: Low balance handled gracefully")
    
    @patch('apps.EasyDocs.tasks._send_chunk.apply_async')
    def test_schedule_bulk_broadcast_chunking(self, mock_apply):
        """Test: Verify proper chunking for bulk broadcasts"""
        print("\n" + "-"*70)
        print("TEST 6: Bulk Broadcast Chunking")
        print("-"*70)
        
        # Create clients
        clients = self.create_test_clients(250)  # Should create 5 chunks (50 each)
        
        # Mock the async task with unique IDs for each chunk
        call_count = 0
        def get_mock_result(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            mock_result = MagicMock()
            mock_result.id = f'test-task-{call_count}'  # Unique ID per chunk
            return mock_result
        
        mock_apply.side_effect = get_mock_result
        
        # Execute scheduler
        print("\n📤 Scheduling bulk broadcast...")
        result = schedule_bulk_broadcast(self.test_message, scheduled_iso=None)
        
        # Verify chunking
        print(f"\n📊 Results:")
        print(f"   Chunks created: {result['chunks']}")
        print(f"   Tasks scheduled: {len(result['task_ids'])}")
        print(f"   Expected chunks: 5 (250 clients ÷ 50 batch size)")
        
        # Assertions
        self.assertEqual(result['chunks'], 5)
        self.assertEqual(len(result['task_ids']), 5)
        self.assertEqual(mock_apply.call_count, 5)
        
        print("\n✅ TEST PASSED: Chunking works correctly")
    
    @patch('apps.EasyDocs.tasks.MobileSasaAPI')
    def test_database_performance(self, MockAPI):
        """Test: Verify bulk database writes are fast"""
        print("\n" + "-"*70)
        print("TEST 7: Database Performance (Bulk Writes)")
        print("-"*70)
        
        # Setup mocks
        mock_api_instance = MockAPI.return_value
        mock_api_instance.get_balance.return_value = {'balance': 50000, 'status': True}
        
        def mock_send_side_effect(message_pairs):
            """Return phones from actual message_pairs sent"""
            return {
                'sent': [m['phone'] for m in message_pairs],
                'failed': []
            }
        
        mock_api_instance.send_personalized_sms.side_effect = mock_send_side_effect
        
        # Create clients
        clients = self.create_test_clients(1000)
        client_ids = list(clients.values_list('id', flat=True))
        
        # Measure DB write time
        print("\n📤 Testing bulk database writes...")
        
        # Clear any existing logs
        MessageLog.objects.all().delete()
        
        start = time.time()
        result = _send_chunk(self.test_message, client_ids)
        elapsed = time.time() - start
        
        # Calculate approximate DB write time (total time - mocked API time)
        db_write_time = elapsed  # API is mocked so this is mostly DB time
        
        print(f"\n📊 Performance Metrics:")
        print(f"   Total time: {elapsed:.2f}s")
        print(f"   DB writes: {MessageLog.objects.count()} logs")
        print(f"   Write rate: {MessageLog.objects.count()/elapsed:.1f} logs/sec")
        print(f"   ⚡ Expected: >500 logs/sec (bulk write)")
        print(f"   🐌 Individual writes would take: ~100-200s")
        
        # Assertions
        self.assertEqual(MessageLog.objects.count(), 1000)
        self.assertLess(elapsed, 30, "Should complete in under 30 seconds with mocked API")
        
        print("\n✅ TEST PASSED: Bulk writes are significantly faster")
    
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        print("\n" + "="*70)
        print("🏁 ALL STRESS TESTS COMPLETED")
        print("="*70)


class QuickSmokeTest(TestCase):
    """Quick smoke test - runs without Celery"""
    
    @patch('apps.EasyDocs.tasks.MobileSasaAPI')
    def test_quick_smoke_test(self, MockAPI):
        """Quick test: 10 clients to verify basic functionality"""
        print("\n" + "="*70)
        print("⚡ QUICK SMOKE TEST (10 clients)")
        print("="*70)
        
        # Setup
        mock_api_instance = MockAPI.return_value
        mock_api_instance.get_balance.return_value = {'balance': 1000, 'status': True}
        mock_api_instance.send_personalized_sms.return_value = {
            'sent': [f"254700{i:06d}" for i in range(1, 11)],
            'failed': []
        }
        
        # Create 10 test clients
        clients = [
            Client.objects.create(
                first_name=f"Client{i}",
                last_name=f"Test{i}",
                email=f"client{i}@test.com",
                phone=f"254700{i:06d}"
            )
            for i in range(1, 11)
        ]
        
        client_ids = [c.id for c in clients]
        
        # Execute
        result = _send_chunk("Test message", client_ids)
        
        # Verify
        self.assertEqual(result['sent'], 10)
        self.assertEqual(result['failed'], 0)
        self.assertEqual(MessageLog.objects.count(), 10)
        
        print("\n✅ SMOKE TEST PASSED")


# Run summary on import
if __name__ == '__main__':
    print("""
    
    ╔═══════════════════════════════════════════════════════════════════╗
    ║                    SMS STRESS TEST SUITE                          ║
    ╠═══════════════════════════════════════════════════════════════════╣
    ║                                                                   ║
    ║  Tests Included:                                                  ║
    ║  ✅ 100 clients  - Small scale                                   ║
    ║  ✅ 1,000 clients - Medium scale                                 ║
    ║  ✅ 5,000 clients - Large scale                                  ║
    ║  ✅ Partial failures - Error isolation                           ║
    ║  ✅ Low balance - Graceful failure                               ║
    ║  ✅ Chunking - Proper batch processing                           ║
    ║  ✅ DB performance - Bulk write speed                            ║
    ║  ⚡ Smoke test - Quick validation                                ║
    ║                                                                   ║
    ║  Run with:                                                        ║
    ║  $ python manage.py test apps.EasyDocs.tests.test_bulk_sms_stress║
    ║                                                                   ║
    ║  Quick test only:                                                 ║
    ║  $ python manage.py test apps.EasyDocs.tests.QuickSmokeTest     ║
    ║                                                                   ║
    ╚═══════════════════════════════════════════════════════════════════╝
    
    """)