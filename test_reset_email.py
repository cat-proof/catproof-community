import json,threading,unittest
from unittest.mock import patch
from reset_email import ResendMailer,PasswordResetEmail

class MailerTests(unittest.TestCase):
    def test_disabled_and_invalid_sender_are_not_ready(self):
        for env in ({},{'RESEND_API_KEY':'fake'},{'RESEND_API_KEY':'fake','PASSWORD_RESET_FROM':'x\r\nBcc: other@example.test'}):self.assertFalse(ResendMailer(env).ready)
    def test_provider_contract_uses_fixed_https_target_and_text_only(self):
        class Response:
            status=200
            def __enter__(self):return self
            def __exit__(self,*a):pass
        mailer=ResendMailer({'RESEND_API_KEY':'synthetic-secret','PASSWORD_RESET_FROM':'CatProof <reset@example.test>'})
        with patch('reset_email.build_opener') as opener:
            opener.return_value.open.return_value=Response();mailer.send('owner@example.test','https://app.example.test/recover#token=synthetic')
            args,kwargs=opener.return_value.open.call_args;req=args[0]
            self.assertEqual(req.full_url,'https://api.resend.com/emails');self.assertEqual(req.get_header('Authorization'),'Bearer synthetic-secret')
            body=json.loads(req.data);self.assertEqual(body['to'],['owner@example.test']);self.assertNotIn('html',body);self.assertIn('30 minutes',body['text']);self.assertEqual(kwargs['timeout'],15)
    def test_unknown_and_known_requests_dispatch_without_provider_results_in_response(self):
        class Recovery:
            def issue(self,email):return 'x'*43 if email=='owner@example.test' else None
        class Mailer:
            ready=True
            def __init__(self):self.sent=[];self.done=threading.Event()
            def send(self,email,link):self.sent.append((email,link));self.done.set()
        mailer=Mailer();service=PasswordResetEmail(Recovery(),mailer,'https://app.example.test')
        self.assertIsNone(service.request('OWNER@example.test'));self.assertTrue(mailer.done.wait(3));self.assertEqual(len(mailer.sent),1)
        self.assertIn('/recover#token=',mailer.sent[0][1]);self.assertIn('email=owner%40example.test',mailer.sent[0][1])
        self.assertIsNone(service.request('missing@example.test'))
    def test_failure_does_not_log_provider_secrets_and_releases_capacity(self):
        class Recovery:
            def issue(self,email):raise RuntimeError('synthetic-sensitive-token')
        class Mailer:ready=True
        service=PasswordResetEmail(Recovery(),Mailer(),'https://app.example.test');service.capacity.acquire()
        with patch('builtins.print') as output:service.deliver('owner@example.test')
        self.assertNotIn('synthetic-sensitive-token',str(output.call_args));self.assertTrue(service.capacity.acquire(blocking=False))
