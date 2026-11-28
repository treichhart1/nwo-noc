"""Synthetic encrypted email sentinel, no provider calls or queued messages."""
SEED='''
from app.core.database import SessionLocal
from app.notifications.models import NotificationCredential
from app.notifications.email_service import encrypt
from app.notifications.email_models import EmailSuppression
with SessionLocal() as db:
 db.add(NotificationCredential(provider='email.synthetic.recovery',ciphertext=encrypt({'marker':'synthetic-email-key-preservation'})))
 db.add(EmailSuppression(contact_hash='e'*64,reason='unsubscribe',evidence='synthetic-recovery-only'))
 db.commit()
'''
PROBE='''
from app.core.database import SessionLocal
from app.notifications.models import NotificationCredential
from app.notifications.email_service import decrypt
from app.notifications.email_models import EmailSuppression
with SessionLocal() as db:
 row=db.get(NotificationCredential,'email.synthetic.recovery')
 assert row and 'synthetic-email-key-preservation' not in row.ciphertext
 assert decrypt(row.ciphertext)['marker']=='synthetic-email-key-preservation'
 assert db.get(EmailSuppression,'e'*64).reason=='unsubscribe'
print('Encrypted email evidence and shared suppression preserved')
'''
