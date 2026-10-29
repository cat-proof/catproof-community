"""Run the real collector CLI against a local HTTPS hosted server."""
import datetime
import json
import os
from pathlib import Path
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from cryptography import x509
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from tests.db import database
from hosted_store import HostedStore
from enterprise import RegistryAccounts
from hosted_app import create_server

class CollectorTLSTests(unittest.TestCase):
    def test_generated_enrollment_command_and_report_with_explicit_ca(self):
        url,connect=database();store=HostedStore(url,connect);accounts=RegistryAccounts(url,connect)
        self.addCleanup(store.store.db.close)
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
            name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]);now=datetime.datetime.now(datetime.timezone.utc)
            cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-datetime.timedelta(minutes=1)).not_valid_after(now+datetime.timedelta(days=1)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False).sign(key,hashes.SHA256()))
            ca=root/'ca.pem';private=root/'key.pem';ca.write_bytes(cert.public_bytes(serialization.Encoding.PEM));private.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()));private.chmod(0o600)
            server=create_server({'APP_PASSWORD':'fixture-tls-password','APP_ORIGIN':'https://localhost'},store,('127.0.0.1',0),accounts)
            origin='https://localhost:'+str(server.server_port);server.hosts={'localhost:'+str(server.server_port)};server.origins={origin}
            context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(ca,private);server.socket=context.wrap_socket(server.socket,server_side=True)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            try:
                _,session=accounts.register({'name':'TLS owner','email':'tls@fixture.test','workspace':'TLS','password':'fixture-owner-password'});actor=accounts.resolve(session)
                pair=server.local_devices.pair(actor,'TLS collector','fixture-owner-password')
                command=[sys.executable,str(Path(__file__).parent/'local/collector.py'),'--state-dir',str(root/'state'),'enroll','--server',origin]
                # Untrusted TLS must fail before the code is redeemed.
                failed=subprocess.run(command,input='CONNECT\n'+pair['code']+'\n',capture_output=True,text=True,timeout=30)
                self.assertNotEqual(failed.returncode,0)
                # Main's paste normalization and the branch's explicit CA must work together.
                result=subprocess.run(command+['--ca-file',str(ca)],input='CONNECT\n `'+pair['code']+'` \n',capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr)
                result=subprocess.run([sys.executable,str(Path(__file__).parent/'local/collector.py'),'--state-dir',str(root/'state'),'run','--once'],capture_output=True,text=True,timeout=30)
                self.assertEqual(result.returncode,0,result.stderr)
                with connect() as db:row=db.execute('SELECT sequence,last_seen FROM catproof_local_devices WHERE registry_id=%s',(actor['workspace_id'],)).fetchone()
                self.assertEqual(row[0],1);self.assertIsNotNone(row[1]);self.assertEqual((root/'state/connection.json').stat().st_mode&0o777,0o600)
            finally:server.shutdown();server.server_close();thread.join()
