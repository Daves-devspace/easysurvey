from unittest.mock import patch

import requests
from django.core.cache import cache
from django.test import TestCase

from apps.EasyDocs.communication import send_and_log_sms
from apps.EasyDocs.models import Client, SmsProviderToken
from apps.EasyDocs.utils import (
    MobileSasaAPI,
    SMS_BALANCE_AUTH_COOLDOWN_CACHE_KEY,
    update_pending_sms_logs_and_balance,
)


class SmsAuthSafetyTests(TestCase):
    def setUp(self):
        cache.delete(SMS_BALANCE_AUTH_COOLDOWN_CACHE_KEY)
        cache.delete('sms_provider_token')
        cache.delete('sms_balance_check')

    def _create_token(self):
        return SmsProviderToken.objects.create(
            api_token='invalid-token-for-test',
            sender_id='TEST_SENDER',
        )

    def test_get_balance_marks_auth_error_and_sets_cooldown_on_401(self):
        self._create_token()
        api = MobileSasaAPI()

        response = requests.Response()
        response.status_code = 401
        response.url = MobileSasaAPI.BASE_URL_BALANCE
        http_error = requests.exceptions.HTTPError(response=response)

        with patch('apps.EasyDocs.utils.requests.get', side_effect=http_error) as mocked_get:
            first = api.get_balance()
            second = api.get_balance()

        self.assertEqual(mocked_get.call_count, 1)
        self.assertTrue(first.get('auth_error'))
        self.assertEqual(first.get('status_code'), 401)
        self.assertIsNone(first.get('balance'))

        self.assertTrue(second.get('cooldown_active'))
        self.assertEqual(second.get('error'), 'auth_cooldown_active')
        self.assertEqual(cache.get(SMS_BALANCE_AUTH_COOLDOWN_CACHE_KEY), True)

    @patch(
        'apps.EasyDocs.utils.MobileSasaAPI.get_balance',
        return_value={
            'status': False,
            'balance': None,
            'error': 'unauthorized',
            'auth_error': True,
            'status_code': 401,
        },
    )
    def test_update_pending_summary_includes_balance_error_for_auth_issue(self, _mock_balance):
        summary = update_pending_sms_logs_and_balance()
        self.assertIn('balance_error', summary)
        self.assertEqual(summary['balance_error'], 'auth_unauthorized')
        self.assertIsNone(summary['balance'])

    @patch(
        'apps.EasyDocs.communication.MobileSasaAPI.get_balance',
        return_value={
            'status': False,
            'balance': None,
            'error': 'unauthorized',
            'auth_error': True,
            'status_code': 401,
        },
    )
    @patch('apps.EasyDocs.communication.MobileSasaAPI.send_sms')
    def test_send_and_log_sms_fails_fast_when_provider_auth_is_invalid(self, mock_send_sms, _mock_balance):
        client = Client.objects.create(
            first_name='Auth',
            last_name='Fail',
            phone='0700000600',
        )

        log = send_and_log_sms(
            client_service=None,
            client=client,
            phone=client.phone,
            message='hello',
            reason='auth safety test',
        )

        self.assertEqual(log.send_status, 'failed')
        self.assertEqual(log.delivery_status, 'failed')
        self.assertIn('unauthorized', (log.error_details or '').lower())
        self.assertEqual(cache.get('sms_balance_check'), '__auth_error__')
        mock_send_sms.assert_not_called()
