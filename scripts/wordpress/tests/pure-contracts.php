<?php
// Executes under native PHP in disposable CI; no WordPress, network or credentials.
if (!defined('ABSPATH')) {define('ABSPATH','/synthetic/');}
if (!class_exists('NWO_Status_Validation')) {
    $root=getenv('NOC128_PLUGIN_ROOT');
    require $root.'/validation.php';require $root.'/transport.php';
}
$count=0;
function check_contract(bool $okay): void {global $count;if(!$okay){throw new RuntimeException('Public PHP contract regression');}$count++;}
function denied_contract(callable $fn): void {$denied=false;try{$fn();}catch(Throwable $error){$denied=true;}check_contract($denied);}
foreach (['127.0.0.1','10.0.0.1','169.254.169.254','172.16.4.1','192.168.1.1','100.64.0.1','192.0.2.1','198.18.0.1','224.0.0.1','::1','::ffff:127.0.0.1','fe80::1','fc00::1','2001:db8::1','2002:7f00:1::1'] as $ip) {check_contract(!NWO_Status_Transport::public_ip($ip));}
foreach (['93.184.216.37','8.8.8.8','2606:4700:4700::1111'] as $ip) {check_contract(NWO_Status_Transport::public_ip($ip));}
foreach (['http://status.example.invalid/network-status-api/v2','https://127.0.0.1/network-status-api/v2','https://localhost/network-status-api/v2','https://status.example.invalid/admin','https://status.example.invalid/network-status-api/v2?private=1','https://user:password@status.example.invalid/network-status-api/v2','https://status.example.invalid:8443/network-status-api/v2','https://status.example.invalid/%2e%2e/'] as $url) {denied_contract(fn()=>NWO_Status_Transport::endpoint($url));}
check_contract(NWO_Status_Transport::endpoint('https://status.example.invalid/network-status-api/v2')['host']==='status.example.invalid');
$now=time();$feed=['api_version'=>'2','overall_status'=>'all_systems_operational','freshness'=>'fresh','generated_at'=>gmdate('c',$now),'expires_at'=>gmdate('c',$now+90),'last_public_update'=>gmdate('c',$now),'basis'=>'administrator-approved announcements; not live service verification','service_areas'=>[],'incidents'=>[],'maintenance'=>[],'resolved'=>[],'next_history_offset'=>null,'revision_token'=>str_repeat('a',64)];
check_contract(NWO_Status_Validation::feed(json_encode($feed),$now)['overall_status']==='all_systems_operational');
foreach (['customer_phone'=>'private','private_topology'=>[],'device_ip'=>'192.168.1.1'] as $key=>$value) {$bad=$feed;$bad[$key]=$value;denied_contract(fn()=>NWO_Status_Validation::feed(json_encode($bad),$now));}
foreach (['expires_at'=>gmdate('c',$now-1),'generated_at'=>gmdate('c',$now+3600),'last_public_update'=>gmdate('c',$now-172800),'overall_status'=>'healthy','revision_token'=>'not-a-hash'] as $key=>$value) {$bad=$feed;$bad[$key]=$value;denied_contract(fn()=>NWO_Status_Validation::feed(json_encode($bad),$now));}
$bad=$feed;$bad['freshness']='stale';denied_contract(fn()=>NWO_Status_Validation::feed(json_encode($bad),$now));
denied_contract(fn()=>NWO_Status_Validation::feed(str_repeat('a',524289),$now));
denied_contract(fn()=>NWO_Status_Validation::date('2026-02-30T00:00:00+00:00'));
foreach (['<script>alert(1)</script>','customer@example.invalid','192.168.1.1','41.123456,-83.123456'] as $text){denied_contract(fn()=>NWO_Status_Validation::prose($text,200));}
echo json_encode(['php'=>PHP_VERSION,'pure_contract_assertions'=>$count,'network'=>'none','scope'=>'PHP validation only; not WordPress acceptance']);
