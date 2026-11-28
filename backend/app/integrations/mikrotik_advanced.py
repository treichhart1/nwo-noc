"""Fixed optional RouterOS reads. No arbitrary commands, keys, scans or writes.

Vendor references and limitations: docs/release/NOC-134/PROTOCOLS.md.
"""
import re
from types import MappingProxyType
from app.integrations.mikrotik import counter, uptime_seconds, flag
from app.integrations.normalization import text
from app.integrations.read_only import IntegrationFailure

# Request narrow .proplist projections. Never request WireGuard keys, PPP users,
# subscriber identifiers, DHCP client IDs/comments or modem/SIM credentials.
ADVANCED_READS = MappingProxyType({
    'health': ('/system/health/print', 'name,value,type'),
    'connection_tracking': ('/ip/firewall/connection/tracking/print', 'enabled,total-entries,max-entries'),
    'wireguard': ('/interface/wireguard/peers/print', '.id,interface,disabled,last-handshake,rx,tx'),
    'bridge_ports': ('/interface/bridge/port/print', '.id,interface,bridge,disabled,inactive,hw'),
    'vlans': ('/interface/vlan/print', '.id,name,interface,vlan-id,running,disabled'),
    'addresses': ('/ip/address/print', '.id,address,network,interface,actual-interface,disabled,dynamic'),
    'ipv6_addresses': ('/ipv6/address/print', '.id,address,interface,disabled,dynamic'),
    'routes': ('/ip/route/print', '.id,dst-address,gateway,active,dynamic,disabled,distance,routing-table,routing-mark'),
    'ipv6_routes': ('/ipv6/route/print', '.id,dst-address,gateway,active,dynamic,disabled,distance,routing-table,routing-mark'),
    'tables': ('/routing/table/print', '.id,name,fib'),
    'vrfs7': ('/ip/vrf/print', '.id,name,interfaces,routing-table'),
    'vrfs6': ('/ip/route/vrf/print', '.id,interfaces,routing-mark'),
    'arp': ('/ip/arp/print', '.id,address,interface,status,dynamic,disabled'),
    'dhcp_servers': ('/ip/dhcp-server/print', '.id,name,interface,disabled,invalid'),
    'leases': ('/ip/dhcp-server/lease/print', '.id,address,server,status,dynamic,disabled,expires-after'),
    'dhcp_networks': ('/ip/dhcp-server/network/print', '.id,address,gateway'),
    'neighbors': ('/ip/neighbor/print', '.id,interface,address,identity,platform,version'),
})
GROUPS = MappingProxyType({
    'router': ('health','connection_tracking'),
    'switch': ('bridge_ports','vlans'),
    'tunnels': ('wireguard',),
    'ipam': ('addresses','ipv6_addresses','vlans','routes','ipv6_routes','tables','vrfs7','vrfs6','arp','dhcp_servers','leases','dhcp_networks','neighbors'),
})
ETHERNET_FIELDS = frozenset({'name','status','rate','full-duplex','auto-negotiation','sfp-module-present',
    'sfp-temperature','sfp-supply-voltage','sfp-tx-power','sfp-rx-power','sfp-rx-loss','sfp-tx-fault'})
POE_FIELDS = frozenset({'name','poe-out-status','poe-out-voltage','poe-out-current','poe-out-power'})
MONITORS = {'ethernet': ('/interface/ethernet/monitor', ETHERNET_FIELDS),
            'poe': ('/interface/ethernet/poe/monitor', POE_FIELDS)}


def monitor_sentence(kind, ident):
    if kind not in MONITORS or not re.fullmatch(r'\*[0-9A-Fa-f]{1,8}',ident):
        raise IntegrationFailure('operation_denied')
    return (MONITORS[kind][0], '=numbers='+ident, '=once=')


def bounded_rows(rows, fields, secrets=()):
    if not isinstance(rows,list) or len(rows)>512:
        raise IntegrationFailure('invalid_response')
    output=[]
    for row in rows:
        if not isinstance(row,dict):
            raise IntegrationFailure('invalid_response')
        result={}
        for key,value in row.items():
            if key not in fields: continue
            if isinstance(value,bool): result[key]=value
            elif isinstance(value,(str,int)) and len(str(value))<=253:
                result[key]=text(str(value),secrets=secrets,limit=253)
            else: raise IntegrationFailure('invalid_response')
        output.append(result)
    return output


def quantity(value, unit, low, high):
    if not isinstance(value,str): return None
    match=re.fullmatch(r'(-?\d+(?:\.\d+)?)'+re.escape(unit),value)
    if not match:return None
    number=float(match[1])
    return number if low<=number<=high else None


def optical(rows):
    result=[]
    for r in rows:
        rate=r.get('rate','')
        m=re.fullmatch(r'(\d+(?:\.\d+)?)(M|G)bps',rate)
        result.append({'interface':r.get('name'), 'link_status':r.get('status') if r.get('status') in {'link-ok','no-link','unknown'} else 'unknown',
            'speed_mbps': float(m[1])*(1000 if m[2]=='G' else 1) if m else None,
            'optical_tx_dbm':quantity(r.get('sfp-tx-power'),'dBm',-100,30),
            'optical_rx_dbm':quantity(r.get('sfp-rx-power'),'dBm',-100,30),
            'transceiver_temperature_c':quantity(r.get('sfp-temperature'),'C',-100,200),
            'transceiver_voltage_v':quantity(r.get('sfp-supply-voltage'),'V',0,100)})
    return result


def poe(rows):
    states={'powered-on','waiting-for-load','short-circuit','overload','voltage-too-low','voltage-too-high','disabled','unknown'}
    return [{'interface':r.get('name'),'state':r.get('poe-out-status') if r.get('poe-out-status') in states else 'unknown',
        'voltage_v':quantity(r.get('poe-out-voltage'),'V',0,100),
        'current_ma':quantity(r.get('poe-out-current'),'mA',0,20000),
        'power_w':quantity(r.get('poe-out-power'),'W',0,2000)} for r in rows]


def tunnel_rows(rows):
    result=[]
    for r in rows:
        age=uptime_seconds(r.get('last-handshake'))
        # WireGuard has no intrinsic UP/DOWN state from handshake age alone.
        result.append({'id':r.get('.id'),'interface':r.get('interface'),'disabled':flag(r.get('disabled')),
            'last_handshake_seconds':age,'rx_bytes':counter(r.get('rx')),'tx_bytes':counter(r.get('tx')),
            'assessment':'unknown' if age is None else 'stale_handshake' if age>300 else 'recent_handshake',
            'availability':'unknown'})
    return result


def collect_optional(client, version, neighbors=(), health=None):
    cfg=client.config
    major=7 if version.startswith('7.') else 6
    resources={};unavailable={}
    names=sorted({name for group in cfg.advanced_groups for name in GROUPS[group]})
    for name in names:
        if major==6 and name in {'wireguard','tables','vrfs7'} or major==7 and name=='vrfs6':
            unavailable[name]='unsupported_routeros_version';continue
        try:
            resources[name]=bounded_rows(health,{'name','value','type'},client.credentials.validate_values()) if name=='health' and health is not None else client._read_advanced(name)
        except IntegrationFailure as exc:
            if exc.code!='operation_denied': raise
            unavailable[name]='unavailable_or_permission_denied'
    if cfg.collect_ospf:
        resources['ospf_neighbors']=bounded_rows(list(neighbors),{'router-id','state'},client.credentials.validate_values())
    for kind,ids in [('ethernet',cfg.ethernet_monitor_ids),('poe',cfg.poe_monitor_ids)]:
        resources[kind]=[]
        for ident in ids:
            try:
                rows=client._exchange(monitor_sentence(kind,ident))
                resources[kind].extend(bounded_rows(rows,MONITORS[kind][1],client.credentials.validate_values()))
            except IntegrationFailure as exc:
                if exc.code!='operation_denied':raise
                unavailable[kind+':'+ident]='unavailable_or_permission_denied'
    return {'schema_version':'mikrotik_advanced_v1','routeros_major':major,
        'resources':resources,'unavailable':unavailable,'optical':optical(resources.get('ethernet',[])),
        'poe':poe(resources.get('poe',[])),'wireguard':tunnel_rows(resources.get('wireguard',[])),
        'cellular_native':'BLOCKED: monitor output includes SIM identifiers; verified source field selection required',
        'read_only':True,'complete_snapshot':False}


def transition_evidence(previous,current,*,elapsed_seconds,uptime_continuous):
    """Advisory evidence only. Missing rows and failed partial reads are unknown."""
    if not uptime_continuous or not 0<elapsed_seconds<=600:return {'state':'boundary','events':[]}
    events=[]
    for resource in ('bridge_ports','routes','wireguard'):
        a={r.get('.id'):r for r in previous.get('resources',{}).get(resource,[]) if r.get('.id')}
        b={r.get('.id'):r for r in current.get('resources',{}).get(resource,[]) if r.get('.id')}
        fields={'bridge_ports':('inactive','disabled'),'routes':('active','gateway'),'wireguard':('disabled',)}[resource]
        for ident in sorted(a.keys() & b.keys()):
            for field in fields:
                old,new=a[ident].get(field),b[ident].get(field)
                if old is not None and new is not None and old!=new:
                    events.append({'resource':resource,'identity':ident,'field':field,'before':old,'after':new,'cause':'unverified'})
    return {'state':'comparable','events':events,'configuration_changes':0}


def lte_documented_fields(raw):
    """Sanitized recorded RouterOS LTE monitor fields, NOT a native poller.

    Raw monitor may contain IMSI/ICCID; caller must provide a sanitized fixture.
    NR mode/identifiers are not inferred from LTE anchor or modem model.
    """
    allowed={'status','current-operator','current-cellid','enb-id','phy-cellid','data-class','session-uptime','rssi','rsrp','rsrq','sinr'}
    if not isinstance(raw,dict) or set(raw)-allowed:raise ValueError('Only sanitized documented LTE fields accepted')
    if any(not isinstance(v,str) or len(v)>120 for v in raw.values()):raise ValueError('Malformed cellular scalar')
    tech='LTE' if raw.get('data-class')=='LTE' else 'unknown'
    cell=raw.get('current-cellid')
    if cell is not None and (tech!='LTE' or not re.fullmatch(r'\d{1,10}',cell) or int(cell)>=2**28):cell=None
    return {'technology':tech,'cell':cell,'cell_identity_technology':'LTE' if cell is not None else None,
        'registration':'registered' if raw.get('status')=='connected' else 'unknown',
        'rssi_dbm':quantity(raw.get('rssi'),'dBm',-160,0),'rsrp_dbm':quantity(raw.get('rsrp'),'dBm',-160,0),
        'rsrq_db':quantity(raw.get('rsrq'),'dB',-50,20),'sinr_db':quantity(raw.get('sinr'),'dB',-50,100),
        'connection_uptime_seconds':uptime_seconds(raw.get('session-uptime')),
        'carrier':text(raw.get('current-operator'),limit=80) if raw.get('current-operator') else None,
        'mcc':None,'mnc':None,'gnodeb':None,'nr_arfcn':None}

# Durable receipt validator: fixed resource keys and field projections only.
from pydantic import BaseModel,ConfigDict,Field,model_validator
from typing import Literal


class AdvancedSnapshot(BaseModel):
    model_config=ConfigDict(extra='forbid',hide_input_in_errors=True,allow_inf_nan=False)
    schema_version:Literal['mikrotik_advanced_v1']='mikrotik_advanced_v1'
    routeros_major:Literal[6,7]
    resources:dict=Field(default_factory=dict,max_length=20)
    unavailable:dict[str,str]=Field(default_factory=dict,max_length=40)
    optical:list=Field(default_factory=list,max_length=512)
    poe:list=Field(default_factory=list,max_length=512)
    wireguard:list=Field(default_factory=list,max_length=512)
    cellular_native:Literal['BLOCKED: monitor output includes SIM identifiers; verified source field selection required']='BLOCKED: monitor output includes SIM identifiers; verified source field selection required'
    read_only:Literal[True]=True
    complete_snapshot:Literal[False]=False

    @model_validator(mode='after')
    def closed_evidence(self):
        import json
        allowed={name:set(props.split(',')) for name,(_,props) in ADVANCED_READS.items()}
        allowed.update({'ethernet':ETHERNET_FIELDS,'poe':POE_FIELDS,'ospf_neighbors':{'router-id','state'}})
        if set(self.resources)-set(allowed):raise ValueError('Unknown advanced resource')
        for name,rows in self.resources.items():
            try:checked=bounded_rows(rows,allowed[name])
            except IntegrationFailure:raise ValueError('Malformed advanced resource') from None
            if checked!=rows:raise ValueError('Unsafe or unrequested native fields')
        for key,value in self.unavailable.items():
            if ':' in key and not re.fullmatch(r'(ethernet|poe):\*[0-9A-Fa-f]{1,8}',key):raise ValueError('Invalid unavailable identity')
            if key.split(':')[0] not in allowed or value not in {'unsupported_routeros_version','unavailable_or_permission_denied'}:
                raise ValueError('Unknown unavailable resource')
        if self.optical!=optical(self.resources.get('ethernet',[])) or self.poe!=poe(self.resources.get('poe',[])) or self.wireguard!=tunnel_rows(self.resources.get('wireguard',[])):
            raise ValueError('Derived advanced evidence mismatch')
        if len(json.dumps(self.model_dump()))>200000:raise ValueError('Advanced receipt budget exceeded; narrow profiles')
        return self
