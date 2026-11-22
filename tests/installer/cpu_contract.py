"""Verify the real QEMU CPU ABI used by disposable NumPy/GIS acceptance."""
import argparse
import json
import subprocess

CPU_MODEL = 'Nehalem'
V2 = {'pni':'pni','ssse3':'ssse3','sse4_1':'sse4.1','sse4_2':'sse4.2',
      'popcnt':'popcnt','cx16':'cx16','lahf_lm':'lahf-lm'}


def model_contract():
    requests=[{'execute':'qmp_capabilities','id':'caps'}]
    for model in ('qemu64',CPU_MODEL):
        requests.append({'execute':'query-cpu-model-expansion','id':model,
                         'arguments':{'type':'full','model':{'name':model}}})
    requests.append({'execute':'quit','id':'quit'})
    result=subprocess.run(['qemu-system-x86_64','-machine','none','-accel','tcg',
                           '-display','none','-nodefaults','-qmp','stdio'],
                          input='\n'.join(json.dumps(r) for r in requests)+'\n',
                          capture_output=True,text=True,timeout=20,check=True)
    reports={}
    for line in result.stdout.splitlines():
        record=json.loads(line)
        if record.get('id') in ('qemu64',CPU_MODEL):
            props=record['return']['model']['props']
            reports[record['id']]={name:props.get(prop) is True for name,prop in V2.items()}
    if set(reports)!= {'qemu64',CPU_MODEL} or not all(reports[CPU_MODEL].values()):
        raise RuntimeError('Explicit disposable CPU does not satisfy the pinned x86-64-v2 wheel ABI')
    return {'selected_model':CPU_MODEL,'required_abi':'x86-64-v2','models':reports,
            'scope':'Actual QEMU model expansion, not live-device or production-host acceptance'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute-qemu',action='store_true');args=parser.parse_args()
    if not args.execute_qemu:parser.error('Explicit local model query required')
    print(json.dumps(model_contract()))
