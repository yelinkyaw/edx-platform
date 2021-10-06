"""
Unit tests for SafeSessionMiddleware
"""

from unittest.mock import patch, MagicMock

import ddt

from crum import set_current_request
from django.core.cache import cache
from django.conf import settings
from django.contrib.auth import SESSION_KEY
from django.contrib.auth.models import AnonymousUser
from django.http import HttpResponse, HttpResponseRedirect, SimpleCookie
from django.test import TestCase
from django.test.utils import override_settings

from openedx.core.djangolib.testing.utils import get_mock_request, CacheIsolationTestCase
from common.djangoapps.student.tests.factories import UserFactory

from ..middleware import SafeCookieData, SafeSessionMiddleware, log_request_user_changes, USER_MISMATCH_CACHE_KEY
from .test_utils import TestSafeSessionsLogMixin


@ddt.ddt
class TestSafeSessionProcessRequest(TestSafeSessionsLogMixin, CacheIsolationTestCase):
    """
    Test class for SafeSessionMiddleware.process_request
    """

    ENABLED_CACHES = ['default']

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request()

    def assert_response(self, safe_cookie_data=None, success=True):
        """
        Calls SafeSessionMiddleware.process_request and verifies
        the response.

        Arguments:
            safe_cookie_data - If provided, it is serialized and
              stored in the request's cookies.
            success - If True, verifies a successful response.
              Else, verifies a failed response with an HTTP redirect.
        """
        if safe_cookie_data:
            self.request.COOKIES[settings.SESSION_COOKIE_NAME] = str(safe_cookie_data)
        response = SafeSessionMiddleware().process_request(self.request)
        if success:
            assert response is None
            assert getattr(self.request, 'need_to_delete_cookie', None) is None
        else:
            assert response.status_code == HttpResponseRedirect.status_code
            assert self.request.need_to_delete_cookie

    def assert_no_session(self):
        """
        Asserts that a session object is *not* set on the request.
        """
        assert getattr(self.request, 'session', None) is None

    def assert_no_user_in_session(self):
        """
        Asserts that a user object is *not* set on the request's session.
        """
        assert self.request.session.get(SESSION_KEY) is None

    def assert_user_in_session(self):
        """
        Asserts that a user object *is* set on the request's session.
        """
        assert SafeSessionMiddleware.get_user_id_from_session(self.request) == self.user.id

    @patch("openedx.core.djangoapps.safe_sessions.middleware.log_request_user_changes")
    @ddt.unpack
    @ddt.data(
        {'toggle': True, 'cache_flag': True, 'should_log': True},
        {'toggle': True, 'cache_flag': False, 'should_log': False},
        {'toggle': False, 'cache_flag': True, 'should_log': False},
        {'toggle': False, 'cache_flag': False, 'should_log': False},
    )
    def test_success(self, mock_log_request_user_changes, toggle, cache_flag, should_log):
        with patch("openedx.core.djangoapps.safe_sessions.middleware.LOG_REQUEST_USER_CHANGES", toggle):
            self.client.login(username=self.user.username, password='test')
            cache.set(USER_MISMATCH_CACHE_KEY, cache_flag)
            session_id = self.client.session.session_key
            safe_cookie_data = SafeCookieData.create(session_id, self.user.id)

            # pre-verify steps 3, 4, 5
            assert getattr(self.request, 'session', None) is None
            assert getattr(self.request, 'safe_cookie_verified_user_id', None) is None

            # verify step 1: safe cookie data is parsed
            self.assert_response(safe_cookie_data)
            self.assert_user_in_session()

            # verify step 2: cookie value is replaced with parsed session_id
            assert self.request.COOKIES[settings.SESSION_COOKIE_NAME] == session_id

            # verify step 3: session set in request
            assert self.request.session is not None

            # verify steps 4, 5: user_id stored for later verification
            assert self.request.safe_cookie_verified_user_id == self.user.id

            if should_log:
                assert mock_log_request_user_changes.called
            else:
                assert not mock_log_request_user_changes.called

    def test_success_no_cookies(self):
        self.assert_response()
        self.assert_no_user_in_session()

    def test_success_no_session(self):
        safe_cookie_data = SafeCookieData.create('no_such_session_id', self.user.id)
        self.assert_response(safe_cookie_data)
        self.assert_no_user_in_session()

    def test_success_no_session_and_user(self):
        safe_cookie_data = SafeCookieData.create('no_such_session_id', 'no_such_user')
        self.assert_response(safe_cookie_data)
        self.assert_no_user_in_session()

    def test_parse_error_at_step_1(self):
        with self.assert_parse_error():
            self.assert_response('not-a-safe-cookie', success=False)
        self.assert_no_session()

    def test_invalid_user_at_step_4(self):
        self.client.login(username=self.user.username, password='test')
        safe_cookie_data = SafeCookieData.create(self.client.session.session_key, 'no_such_user')
        with self.assert_incorrect_user_logged():
            self.assert_response(safe_cookie_data, success=False)
        self.assert_user_in_session()


@ddt.ddt
class TestSafeSessionProcessResponse(TestSafeSessionsLogMixin, CacheIsolationTestCase):
    """
    Test class for SafeSessionMiddleware.process_response
    """

    ENABLED_CACHES = ['default']

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request()
        self.request.session = MagicMock()
        self.client.response = HttpResponse()
        self.client.response.cookies = SimpleCookie()

    def assert_response(self, set_request_user=False, set_session_cookie=False):
        """
        Calls SafeSessionMiddleware.process_response and verifies
        the response.

        Arguments:
            set_request_user - If True, the user is set on the request
                object.
            set_session_cookie - If True, a session_id is set in the
                session cookie in the response.
            set_request_session_id - If True, a session_id is set on the request object
        """
        if set_request_user:
            self.request.user = self.user
            SafeSessionMiddleware.set_user_id_in_session(self.request, self.user)
        if set_session_cookie:
            self.client.response.cookies[settings.SESSION_COOKIE_NAME] = "some_session_id"

        response = SafeSessionMiddleware().process_response(self.request, self.client.response)
        assert response.status_code == 200

    def assert_response_with_delete_cookie(
            self,
            expect_delete_called=True,
            set_request_user=False,
            set_session_cookie=False,
    ):
        """
        Calls SafeSessionMiddleware.process_response and verifies
        the response, while expecting the cookie to be deleted if
        expect_delete_called is True.

        See assert_response for information on the other
        parameters.
        """
        with patch('django.http.HttpResponse.set_cookie') as mock_delete_cookie:
            self.assert_response(set_request_user=set_request_user, set_session_cookie=set_session_cookie)
            assert mock_delete_cookie.called == expect_delete_called

    @patch("django.core.cache.cache.set")
    @patch("django.core.cache.cache.touch")
    def test_success(self, mock_cache_set, mock_cache_touch):
        with self.assert_not_logged():
            self.assert_response(set_request_user=True, set_session_cookie=True)
        assert not mock_cache_set.called
        assert not mock_cache_touch.called

    @patch("django.core.cache.cache.set")
    @patch("django.core.cache.cache.touch")
    def test_confirm_user_at_step_2(self, mock_cache_set, mock_cache_touch):
        self.request.safe_cookie_verified_user_id = self.user.id
        with self.assert_not_logged():
            self.assert_response(set_request_user=True, set_session_cookie=True)
        assert not mock_cache_set.called
        assert not mock_cache_touch.called

    @patch("django.core.cache.cache.set")
    def test_different_user_at_step_2_error(self, mock_cache_set):
        print(self.request)

        self.request.safe_cookie_verified_user_id = "different_user"
        self.request.safe_cookie_session_id = "some_other_session"
        self.request.session.configure_mock(**{'session_key': 'some_session_id'})

        with self.assert_logged_for_request_user_mismatch("different_user", self.user.id, 'warning', self.request.path,
                                                          "some_other_session", self.request.session.session_key):
            self.assert_response(set_request_user=True, set_session_cookie=True)
            assert mock_cache_set.called

        mock_cache_set.reset_mock()
        with self.assert_logged_for_session_user_mismatch("different_user", self.user.id, self.request.path,
                                                          "some_other_session", self.request.session.session_key):
            self.assert_response(set_request_user=True, set_session_cookie=True)
            assert mock_cache_set.called

    @patch("django.core.cache.cache.set")
    def test_anonymous_user(self, mock_cache_set):
        self.request.safe_cookie_verified_user_id = self.user.id
        self.request.user = AnonymousUser()
        self.request.session[SESSION_KEY] = self.user.id
        with self.assert_no_error_logged():
            self.assert_response(set_request_user=False, set_session_cookie=True)
        assert not mock_cache_set.called

    def test_update_cookie_data_at_step_3(self):
        self.assert_response(set_request_user=True, set_session_cookie=True)

        serialized_cookie_data = self.client.response.cookies[settings.SESSION_COOKIE_NAME].value
        safe_cookie_data = SafeCookieData.parse(serialized_cookie_data)
        assert safe_cookie_data.version == SafeCookieData.CURRENT_VERSION
        assert safe_cookie_data.session_id == 'some_session_id'
        assert safe_cookie_data.verify(self.user.id)

    def test_cant_update_cookie_at_step_3_error(self):
        self.client.response.cookies[settings.SESSION_COOKIE_NAME] = None
        with self.assert_invalid_session_id():
            self.assert_response_with_delete_cookie(set_request_user=True)

    @ddt.data(True, False)
    def test_deletion_of_cookies_at_step_4(self, set_request_user):
        self.request.need_to_delete_cookie = True
        self.assert_response_with_delete_cookie(set_session_cookie=True, set_request_user=set_request_user)

    def test_deletion_of_no_cookies_at_step_4(self):
        self.request.need_to_delete_cookie = True
        # delete_cookies is called even if there are no cookies set
        self.assert_response_with_delete_cookie()


@ddt.ddt
class TestSafeSessionMiddleware(TestSafeSessionsLogMixin, TestCase):
    """
    Test class for SafeSessionMiddleware, testing both
    process_request and process_response.
    """

    def setUp(self):
        super().setUp()
        self.user = UserFactory.create()
        self.addCleanup(set_current_request, None)
        self.request = get_mock_request()
        self.client.response = HttpResponse()
        self.client.response.cookies = SimpleCookie()

    def cookies_from_request_to_response(self):
        """
        Transfers the cookies from the request object to the response
        object.
        """
        if self.request.COOKIES.get(settings.SESSION_COOKIE_NAME):
            self.client.response.cookies[settings.SESSION_COOKIE_NAME] = self.request.COOKIES[
                settings.SESSION_COOKIE_NAME
            ]

    def verify_success(self):
        """
        Verifies success path.
        """
        self.client.login(username=self.user.username, password='test')
        self.request.user = self.user

        session_id = self.client.session.session_key
        safe_cookie_data = SafeCookieData.create(session_id, self.user.id)
        self.request.COOKIES[settings.SESSION_COOKIE_NAME] = str(safe_cookie_data)

        with self.assert_not_logged():
            response = SafeSessionMiddleware().process_request(self.request)
        assert response is None

        assert self.request.safe_cookie_verified_user_id == self.user.id
        self.cookies_from_request_to_response()

        with self.assert_not_logged():
            response = SafeSessionMiddleware().process_response(self.request, self.client.response)
        assert response.status_code == 200

    def test_success(self):
        self.verify_success()

    def test_success_from_mobile_web_view(self):
        self.request.path = '/xblock/block-v1:org+course+run+type@html+block@block_id'
        self.verify_success()

    @override_settings(MOBILE_APP_USER_AGENT_REGEXES=[r'open edX Mobile App'])
    def test_success_from_mobile_app(self):
        self.request.META = {'HTTP_USER_AGENT': 'open edX Mobile App Version 2.1'}
        self.verify_success()

    def verify_error(self, expected_response_status):
        """
        Verifies error path.
        """
        self.request.COOKIES[settings.SESSION_COOKIE_NAME] = 'not-a-safe-cookie'

        with self.assert_parse_error():
            request_response = SafeSessionMiddleware().process_request(self.request)
            assert request_response.status_code == expected_response_status

        assert self.request.need_to_delete_cookie
        self.cookies_from_request_to_response()

        with patch('django.http.HttpResponse.set_cookie') as mock_delete_cookie:
            SafeSessionMiddleware().process_response(self.request, self.client.response)
            assert mock_delete_cookie.called

    def test_error(self):
        self.verify_error(302)

    @override_settings(MOBILE_APP_USER_AGENT_REGEXES=[r'open edX Mobile App'])
    def test_error_from_mobile_app(self):
        self.request.META = {'HTTP_USER_AGENT': 'open edX Mobile App Version 2.1'}
        self.verify_error(401)


@ddt.ddt
class TestLogRequestUserChanges(TestCase):
    """
    Test the function that instruments a request object.

    Ensure that we are logging changes to the 'user' attribute and
    that the correct messages are written.
    """

    @patch("openedx.core.djangoapps.safe_sessions.middleware.log.info")
    def test_initial_user_setting_logging(self, mock_log):
        request = get_mock_request()
        del request.user
        log_request_user_changes(request)
        request.user = UserFactory.create()

        assert mock_log.called
        assert "Setting for the first time" in mock_log.call_args[0][0]

    @patch("openedx.core.djangoapps.safe_sessions.middleware.log.info")
    def test_user_change_logging(self, mock_log):
        request = get_mock_request()
        original_user = UserFactory.create()
        new_user = UserFactory.create()

        request.user = original_user
        log_request_user_changes(request)

        # Verify that we don't log if set to same as current value.
        request.user = original_user
        assert not mock_log.called

        # Verify logging on change.
        request.user = new_user
        assert mock_log.called
        assert f"Changing request user. Originally {original_user.id!r}" in mock_log.call_args[0][0]
        assert f"will become {new_user.id!r}" in mock_log.call_args[0][0]

        # Verify change back logged.
        request.user = original_user
        assert mock_log.call_count == 2
        expected_msg = f"Originally {original_user.id!r}, now {new_user.id!r} and will become {original_user.id!r}"
        assert expected_msg in mock_log.call_args[0][0]

    @patch("openedx.core.djangoapps.safe_sessions.middleware.log.info")
    def test_user_change_with_no_ids(self, mock_log):
        request = get_mock_request()
        del request.user

        log_request_user_changes(request)
        request.user = object()
        assert mock_log.called
        assert "Setting for the first time, but user has no id" in mock_log.call_args[0][0]

        request.user = object()
        assert mock_log.call_count == 2
        assert "Changing request user but user has no id." in mock_log.call_args[0][0]
