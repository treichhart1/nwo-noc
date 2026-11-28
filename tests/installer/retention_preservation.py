"""Synthetic-only preservation probes passed to the actual backend image."""
SEED = '''
from sqlalchemy import select
from app.core.database import SessionLocal
from app.models.entities import User
from app.retention.models import Case,Policy,Reason
from app.signup import crypto
from app.field.models import now
from uuid import uuid4
with SessionLocal() as db:
 user=db.scalar(select(User).where(User.role=='admin'))
 policy=Policy(version=1,text='Synthetic recovery cancellation policy; operator review required and no debt established.',created_by=user.id)
 db.add(policy);db.add(Reason(code='unknown',label='Unknown',voluntary='unknown'));db.flush()
 case=Case(reference='SYN131-RECOVERY',submission_key=str(uuid4()),digest='c'*64,receipt_hash='d'*64,requested_date=now().date(),reported_reason='unknown',policy_id=policy.id,policy_ack_at=now(),equipment_ack=True)
 crypto.initialize(case,{'marker':'synthetic-encrypted-retention-preservation','account_lookup':False})
 db.add(case);db.commit()
'''
PROBE = '''
from sqlalchemy import select
from app.core.database import SessionLocal
from app.retention.models import Case
from app.signup import crypto
with SessionLocal() as db:
 case=db.scalar(select(Case).where(Case.reference=='SYN131-RECOVERY'))
 assert case and case.customer_id is None
 assert 'synthetic-encrypted-retention-preservation' not in case.pii_ciphertext
 assert crypto.unseal(case,case.pii_ciphertext)['marker']=='synthetic-encrypted-retention-preservation'
print('Encrypted cancellation record and restored key verified')
'''
