import unittest
from fastapi.testclient import TestClient
import test_auth


class AccountLifecycleTests(unittest.TestCase):
    setUp=test_auth.AccountIsolationTests.setUp
    login=test_auth.AccountIsolationTests.login

    def test_revocation_does_not_expire_cookie_from_a_newer_tab_login(self):
        self.login()
        reply=self.client.post('/api/auth/password',json={'current_password':'Alice-test-9182!','new_password':'Replacement-9182!'})
        self.assertEqual(reply.status_code,200)
        self.assertNotIn('set-cookie',reply.headers)
        self.login('alice','Replacement-9182!')
        self.assertNotIn('set-cookie',self.client.post('/api/auth/logout').headers)
    def test_change_password_revokes_all_sessions_and_requires_old_password(self):
        self.login()
        other = TestClient(self.app)
        self.addCleanup(other.close)
        other.post('/api/auth/login', json={'username':'alice','password':'Alice-test-9182!'})
        payload = {'current_password':'wrong', 'new_password':'Replacement-9182!'}
        self.assertEqual(self.client.post('/api/auth/password', json=payload).status_code, 403)
        payload['current_password'] = 'Alice-test-9182!'
        self.assertEqual(self.client.post('/api/auth/password', json=payload).status_code, 200)
        self.assertEqual(self.client.get('/api/sessions').status_code, 401)
        self.assertEqual(other.get('/api/sessions').status_code, 401)
        self.login('alice', 'Replacement-9182!')
        self.assertEqual(self.client.get('/api/sessions').json()['total'], 1)

    def test_admin_reset_and_disable_preserve_records(self):
        other = TestClient(self.app)
        self.addCleanup(other.close)
        other.post('/api/auth/login', json={'username':'alice','password':'Alice-test-9182!'})
        self.login('admin', 'Admin-test-9182!')
        path = '/api/admin/users/' + self.alice['id']
        self.assertEqual(self.client.post(path+'/password', json={'new_password':'Replacement-9182!'}).status_code, 200)
        self.assertEqual(other.get('/api/sessions').status_code, 401)
        self.assertEqual(self.client.patch(path, json={'active':False}).status_code, 200)
        self.assertEqual(other.post('/api/auth/login', json={'username':'alice','password':'Replacement-9182!'}).status_code, 401)
        recipients = self.client.get('/api/device/recipients', headers=self.headers).json()['users']
        self.assertNotIn(self.alice['id'], [u['id'] for u in recipients])
        self.assertEqual(self.client.patch(path, json={'active':True}).status_code, 200)
        self.login('alice', 'Replacement-9182!')
        self.assertEqual(self.client.get('/api/sessions').json()['total'], 1)

    def test_admin_accounts_protected_and_user_cannot_manage_others(self):
        self.login()
        path='/api/admin/users/'+self.bob['id']
        self.assertEqual(self.client.patch(path,json={'active':False}).status_code,403)
        self.assertEqual(self.client.post(path+'/password',json={'new_password':'Replacement-9182!'}).status_code,403)
        self.login('admin','Admin-test-9182!')
        path='/api/admin/users/'+self.admin['id']
        self.assertEqual(self.client.patch(path,json={'active':False}).status_code,409)
        self.assertEqual(self.client.post(path+'/password',json={'new_password':'Replacement-9182!'}).status_code,409)
        self.assertEqual(self.client.get('/api/admin/users').status_code,200)


if __name__ == '__main__': unittest.main()
