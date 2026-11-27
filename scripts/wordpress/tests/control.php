<?php
$_SERVER['HTTP_HOST']='93.184.216.36';$_SERVER['REQUEST_METHOD']='GET';
require '/var/www/html/wp-load.php';
$mode=getenv('NOC128_ACTION');
if ($mode==='clear') { NWO_Network_Status::clear_cache(); }
elseif ($mode==='configure') {
    $s=NWO_Network_Status::defaults();$s['enabled']=true;
    $e=NWO_Status_Transport::endpoint('https://status.noc128.test/network-status-api/v2');
    $s['gateway_url']=$e['url'];$s['gateway_ips']=NWO_Status_Transport::resolve($e['host']);
    update_option(NWO_Network_Status::OPTION,$s,false);NWO_Network_Status::clear_cache();
} elseif ($mode==='block') {
    wp_insert_post(['post_title'=>'Synthetic status block','post_name'=>'status-block','post_status'=>'publish','post_type'=>'page','post_content'=>'<!-- wp:nwo/network-status {"view":"banner"} /-->']);
} elseif ($mode==='theme') {
    switch_theme(getenv('NOC128_THEME'));
} elseif ($mode==='version') { echo json_encode(['wordpress'=>$wp_version,'php'=>PHP_VERSION,'theme'=>get_stylesheet()]); }
elseif ($mode==='contracts') {
    $count=0;
    foreach (['http://status.noc128.test/network-status-api/v2','https://127.0.0.1/network-status-api/v2','https://localhost/network-status-api/v2','https://status.noc128.test/admin','https://status.noc128.test/network-status-api/v2?x=1','https://user:password@status.noc128.test/network-status-api/v2','https://status.noc128.test:8443/network-status-api/v2','https://status.noc128.test/%2e%2e/'] as $url) {
        $denied=false;try{NWO_Status_Transport::endpoint($url);}catch(Throwable $e){$denied=true;}if(!$denied){throw new RuntimeException('malicious_url_accepted');}$count++;
    }
    foreach (['127.0.0.1','10.0.0.1','169.254.169.254','100.64.0.1','192.0.2.1','::1','::ffff:127.0.0.1','fe80::1','2001:db8::1'] as $ip) { if(NWO_Status_Transport::public_ip($ip)){throw new RuntimeException('private_ip_accepted');}$count++; }
    if (!NWO_Status_Transport::public_ip('93.184.216.34')) {throw new RuntimeException('public_ip_rejected');}
    update_option('nwo_status_cache_0',['saved'=>time(),'feed'=>['expires_at'=>'broken']],false);
    if (NWO_Network_Status::data(0)['available'] !== true) {throw new RuntimeException('corrupt_cache_not_recovered');}
    echo json_encode(['security_contract_assertions'=>$count+2]);
} else {throw new RuntimeException('unsupported_fixture_action');}
