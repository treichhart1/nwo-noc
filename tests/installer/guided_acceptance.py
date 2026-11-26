"""Actual offline guided installation in the independent disposable VM only."""
import json
from pathlib import Path
import shutil


def install(source, evidence, work, recipient, secret, command, passed):
    prepared=Path(evidence)
    data=json.loads((prepared/'prepared.json').read_text())
    commit,tree=data['commit'],data['tree']
    a={'purpose':'controlled-test','commit':commit,'tree':tree,'reference':'NOC123 independent VM installation approval','confirmation':'INSTALL ISOLATED LAB','ssh_host':'localhost','backup_recipient':recipient,'backup_destination':'synthetic second-host encrypted recovery',**{k:True for k in ('isolated_lab_authorized','private_management_reviewed','native_risks_reviewed','backup_custody_reviewed')}}
    auth=work/'guided-authorization.json';auth.write_text(json.dumps(a));auth.chmod(0o600)
    argv=['python3',source/'scripts/host/lab.py','install','--execute','--commit',commit,'--tree',tree,'--work',prepared,'--authorization-file',auth,'--password-stdin']
    plan=command(['python3',source/'scripts/host/lab.py','plan','--commit',commit,'--tree',tree])
    report=json.loads(plan.stdout)
    passed('guided_plan_no_authorization_or_mutation',report['changes_authorized'] is False and not Path('/opt/nwo-noc').exists())
    # Preserve complete corrupted copies; never overwrite or clean the valid evidence.
    for name in ('purpose','source','missing-image','altered-report'):
        bad=work/('guided-negative-'+name);shutil.copytree(prepared,bad)
        if name=='altered-report':(bad/'npm-audit.json').write_text('{}')
        else:
            item=json.loads((bad/'prepared.json').read_text())
            if name=='purpose':item['purpose']='release'
            if name=='source':item['commit']='f'*40
            if name=='missing-image':del item['images']['backend']
            (bad/'prepared.json').write_text(json.dumps(item))
        attempt=list(argv);attempt[attempt.index('--work')+1]=bad
        result=command(attempt,data=(secret+'\n').encode(),success=False)
        passed('guided_reject_'+name,result.returncode!=0 and not Path('/opt/nwo-noc').exists())
    result=command(argv,data=(secret+'\n').encode())
    passed('guided_actual_install_and_verify',result.returncode==0)
    duplicate=command(argv,data=(secret+'\n').encode(),success=False)
    passed('guided_existing_install_rejected',duplicate.returncode!=0)
    return result,prepared/'approval.json',prepared/'lab.crt',prepared/'lab.key'
